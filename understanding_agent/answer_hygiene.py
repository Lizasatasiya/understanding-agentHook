"""Answer hygiene — transcription artifacts never score as wrong answers.

The bug class this module exists to kill: a voice-dictation artifact (empty
capture, single clipped token, ASR stutter) reaches the evaluator, scores 0,
and aborts the commit. Dictation failure punished the developer.

Design (Pokerface P1, PLAN_POKERFACE_JUDAS.md):
  - Suspicious answers get ONE optional re-ask ("say it again / type it /
    press Enter to submit as-is"). Never a forced discard — mixed-language
    answers are legitimate and scorable (the evaluator prompt judges concept
    coverage, never language).
  - Discard is reserved for the developer explicitly submitting the suspect
    text after being warned, or an empty re-ask.
  - All checks are deterministic, pure-Python, no dependencies. Mixed-script
    detection exists as a helper (script_counts) for telemetry/tests, but
    script-mix alone is NOT a suspect signal: bilingual answers are normal;
    flagging every one would train users to ignore the warning.

Hygiene kill switch: UNDERSTANDING_AGENT_ANSWER_HYGIENE=off disables the
re-ask entirely (answers pass through untouched).
"""

import os
import re
import unicodedata

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def hygiene_enabled() -> bool:
    return os.environ.get("UNDERSTANDING_AGENT_ANSWER_HYGIENE", "").strip().lower() not in (
        "off", "false", "0", "no", "disable", "disabled",
    )


def script_counts(text: str) -> dict:
    """Count letters per broad script family (Unicode block heuristic).

    Cheap, dependency-free script detection — useful for telemetry about
    multilingual answers and for tests. Keys are lowercase family names.
    """
    counts = {}
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if "LATIN" in name:
            fam = "latin"
        elif "CYRILLIC" in name:
            fam = "cyrillic"
        elif "DEVANAGARI" in name:
            fam = "devanagari"
        elif "CJK" in name or "HIRAGANA" in name or "KATAKANA" in name or "HANGUL" in name:
            fam = "cjk"
        elif "ARABIC" in name:
            fam = "arabic"
        elif "HEBREW" in name:
            fam = "hebrew"
        elif "GREEK" in name:
            fam = "greek"
        elif "THAI" in name:
            fam = "thai"
        else:
            fam = "other"
        counts[fam] = counts.get(fam, 0) + 1
    return counts


def classify_answer(text: str) -> tuple:
    """Return (is_suspect, reason) for an answer about to be scored.

    A suspect answer should be offered ONE re-ask before evaluation. The
    developer may still submit it as-is — this is a warning, never a veto.
    """
    text = (text or "").strip()
    if not text:
        return True, "empty"

    tokens = _WORD_RE.findall(text)

    # A single clipped token is the classic ASR failure: the mic caught the
    # first word of a longer sentence and cut out.
    if len(tokens) <= 1:
        return True, "single-token"

    # ASR stutter: the same token repeated dominates the answer
    # ("the the the the ...", repeated hallucinated filler).
    lowered = [t.lower() for t in tokens]
    most_common = max(set(lowered), key=lowered.count)
    if lowered.count(most_common) >= 4 and lowered.count(most_common) / len(lowered) > 0.6:
        return True, f"repetition of '{most_common}'"

    return False, ""
