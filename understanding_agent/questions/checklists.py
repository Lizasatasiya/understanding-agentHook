"""Checklist-Based Verification Protocol for answer evaluation.

Implements the evaluation methodology from RepoProbe (arXiv 2608.04783):
instead of a single opaque scalar judgment, a question's understanding is
decomposed into atomic, weighted key points, each with a discrete rubric of
valid scores. The judge (LLM) writes evidence first and may only choose from
the predefined tiers; the final score is a DETERMINISTIC aggregation of
item-level decisions, never a model-invented scalar.

Design rules (frozen):
- k in 2..4 knowledge items per question, integer weights.
- One universal clarity item (0/1) flagging severe hallucination or
  incoherence, separate from knowledge credit.
- Multi-tier rubrics for complex items (e.g. 4/2/0), binary for single facts.
- The judge's per-item scores are validated against the rubric; anything the
  rubric does not allow is rejected, and the whole evaluation degrades to the
  offline fallback rather than trusting an out-of-band number.
- Scores from this module are 0-100 so the frozen pass/abort thresholds
  (75% pass, 25% abort, 25-70 follow-up band) apply unchanged.

The offline verifier is deterministic and needs no LLM: it checks whether the
answer text plausibly addresses each key point, using the key point's own
content words (not generic root-word matching over expected_concepts).
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ChecklistItem:
    """One atomic, verifiable aspect of a correct answer."""
    key_point: str            # what the developer must convey for this item
    weight: int               # maximum points (>= 1)
    tiers: tuple              # valid discrete scores, descending, incl. 0

    def validate(self) -> bool:
        if not isinstance(self.key_point, str) or not self.key_point.strip():
            return False
        if not isinstance(self.weight, int) or self.weight < 1 or self.weight > 6:
            return False
        if not isinstance(self.tiers, (list, tuple)) or len(self.tiers) < 2:
            return False
        try:
            vals = [int(t) for t in self.tiers]
        except (TypeError, ValueError):
            return False
        if vals != sorted(vals, reverse=True):
            return False
        if any(v < 0 or v > self.weight for v in vals):
            return False
        if 0 not in vals:
            return False
        return True


@dataclass
class Checklist:
    """Question-level checklist: knowledge items + a universal clarity item."""
    items: list = field(default_factory=list)   # list[ChecklistItem]
    clarity_weight: int = 1                    # hallucination/incoherence flag

    def validate(self) -> bool:
        if not (2 <= len(self.items) <= 4):
            return False
        if not all(isinstance(i, ChecklistItem) and i.validate() for i in self.items):
            return False
        return True

    @property
    def total_points(self) -> int:
        return sum(i.weight for i in self.items) + self.clarity_weight

    def key_points(self) -> list:
        return [i.key_point for i in self.items]


def parse_checklist(raw: object) -> Optional[Checklist]:
    """Validate an LLM-produced (or hand-authored) checklist dict.

    Accepted shape:
        {"items": [{"key_point": "...", "weight": 4,
                    "tiers": [4, 2, 0]}, ...],
         "clarity_weight": 1}            # optional, default 1

    Returns None on any schema violation — callers treat that as "no
    checklist" and fall back to the legacy scalar path.
    """
    if not isinstance(raw, dict):
        return None
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        return None
    items = []
    for r in raw_items:
        if not isinstance(r, dict):
            return None
        tiers = r.get("tiers")
        if isinstance(tiers, list):
            tiers = tuple(tiers)
        item = ChecklistItem(
            key_point=str(r.get("key_point", "")).strip(),
            weight=r.get("weight", 0),
            tiers=tiers if isinstance(tiers, tuple) else (),
        )
        if not item.validate():
            return None
        items.append(item)
    clarity = raw.get("clarity_weight", 1)
    if not isinstance(clarity, int) or clarity < 0 or clarity > 1:
        clarity = 1
    cl = Checklist(items=items, clarity_weight=clarity)
    return cl if cl.validate() else None


def aggregate_score(checklist: Checklist, awarded: list, clarity: int) -> int:
    """Deterministic 0-100 score from per-item decisions.

    awarded: per-item points, in checklist.items order.
    clarity: 0 or 1 on the universal hallucination/incoherence item.
    Invalid inputs raise ValueError — callers must catch and degrade.
    """
    if len(awarded) != len(checklist.items):
        raise ValueError("awarded must match items length")
    total = 0
    for item, pts in zip(checklist.items, awarded):
        pts = int(pts)
        if pts not in [int(t) for t in item.tiers]:
            raise ValueError(f"score {pts} not in rubric {list(item.tiers)}")
        total += pts
    clarity = int(clarity)
    if clarity not in (0, 1):
        raise ValueError("clarity must be 0 or 1")
    total += clarity
    if checklist.total_points <= 0:
        raise ValueError("empty checklist")
    return int(round(100.0 * total / checklist.total_points))


_STOPWORDS = frozenset(
    "the and for with that this from into not are was were has have had its "
    "his her their your our can may might must will would should could what "
    "which when where why how who whom there here about after before also "
    "more most other such only just than then them they you she".split()
)


def _content_words(text: str) -> set:
    import re
    return set(w for w in re.findall(r"[a-z0-9]{3,}", text.lower())
               if w not in _STOPWORDS)


def verify_offline(checklist: Checklist, answer_text: str) -> list:
    """Deterministic offline scoring when no LLM is available.

    An item is fully credited when the answer shares at least half of the
    key point's content words (or at least 2, whichever is smaller);
    otherwise it scores 0. Binary behavior for binary rubrics, full/partial
    for tiered ones (tiered items get their middle tier on partial overlap).
    """
    answer_words = _content_words(answer_text or "")
    awarded = []
    for item in checklist.items:
        kp_words = _content_words(item.key_point)
        need = max(1, min(2, len(kp_words) // 2)) if kp_words else 1
        overlap = len(kp_words & answer_words)
        tiers = sorted((int(t) for t in item.tiers), reverse=True)
        if overlap >= need:
            awarded.append(tiers[0])
        elif overlap >= 1 and len(tiers) > 2:
            awarded.append(tiers[1])
        else:
            awarded.append(0)
    return awarded


def clarity_offline(answer_text: str) -> int:
    """Clarity item offline: 0 only for empty/near-empty answers."""
    return 1 if (answer_text or "").strip() else 0


def fallback_banding(coverage: float) -> tuple:
    """Frozen fail-open banding shared with the legacy concept-matching path.

    Returns (score, follow_up_required):
    - coverage >= 0.70 -> 85 + up to 10 bonus, no follow-up
    - coverage >= 0.40 -> 70-band, follow-up required
    - otherwise        -> 76 baseline fail-open pass, no follow-up
    """
    if coverage >= 0.70:
        return 85 + int(min(10, (coverage - 0.70) * 33)), False
    if coverage >= 0.40:
        return 70 + int((coverage - 0.40) * 20), True
    return 76, False
