"""Deterministic ChangeKind classifier (Bad Romance item 3 — tier one).

Always-on, offline-safe classification of what kind of change a commit is.
The hook attaches this result to the diff context. The service turns it
into question-prompt hints.

Signals, all deterministic and auditable:
  - paths: component/stylesheet extensions, migrations/schema files, test
    files, docs, config files, route/controller dirs
  - diff shape: SQL DDL/DML, CSS rules, JSX tags
  - symbols: changed-function names (render/Component/useState vs
    migrate/query/execute vs route/handle/dispatch)

Output is a TYPED field (never prose): category weights over the six frozen
domains + dominant category + the signals that fired, so a reviewer can
always ask "why did you call this UI?" and get an answer.
"""

import os
import re

DOMAINS = ("ui", "data_model", "control_flow", "infra_config", "tests", "docs")

# --- path signals (checked against the full staged path, slash-normalized) ---
_PATH_PATTERNS = [
    (re.compile(r"\.(tsx|jsx|vue|svelte|css|scss|less|html)$", re.I), "ui", 2.0),
    (re.compile(r"(^|/)(components?|pages?|views?|widgets?|ui)/", re.I), "ui", 1.0),
    (re.compile(r"(^|/)(migrations?|schema|schemas)(/|[-_.])", re.I), "data_model", 2.0),
    (re.compile(r"\.(sql|prisma|graphql|gql)$", re.I), "data_model", 1.0),
    (re.compile(r"(^|/)(models?|entities|repositories)(/|[-_.])", re.I), "data_model", 1.0),
    (re.compile(r"(^|/)(__tests__|tests?|specs?)(/|$)", re.I), "tests", 2.0),
    (re.compile(r"(^|/)(test_|_test\.|\.test\.|\.spec\.-|\.spec\.)", re.I), "tests", 2.0),
    (re.compile(r"\.(md|rst|txt|adoc)$", re.I), "docs", 2.0),
    (re.compile(r"(^|/)(docs?|documentation)(/|$)", re.I), "docs", 1.0),
    (re.compile(r"\.(ya?ml|toml|ini|cfg|conf|properties)$", re.I), "infra_config", 1.5),
    (re.compile(r"(^|/)(Dockerfile|docker-compose|Makefile|\.?env|jenkinsfile)", re.I),
     "infra_config", 1.5),
    (re.compile(r"(^|/)(ci|\.github|\.gitlab-ci|\.circleci)(/|$)", re.I), "infra_config", 1.5),
    (re.compile(r"(^|/)(routes?|controllers?|handlers?|endpoints?|api)(/|[-_.])", re.I),
     "control_flow", 1.0),
    (re.compile(r"\.(py|js|ts|go|rb|java|cs|rs)$", re.I), "control_flow", 0.5),
]

# --- diff-shape signals (checked against added diff lines) ---
_SHAPE_PATTERNS = [
    (re.compile(r"\b(CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+INDEX|"
                r"ADD\s+COLUMN|INSERT\s+INTO|FOREIGN\s+KEY|PRIMARY\s+KEY)", re.I),
     "data_model", 2.0),
    (re.compile(r"^[^/]*\{[^}]*:[^}]*;?\s*\}?\s*$"), "ui", 1.0),  # CSS-ish rule
    (re.compile(r"<[A-Z][A-Za-z0-9]*[\s/>]|</[A-Za-z]+>"), "ui", 1.0),  # JSX tag
    (re.compile(r"\b(useState|useEffect|useMemo|useCallback|useRef)\b"), "ui", 1.0),
]

# --- symbol signals (checked against changed-function names) ---
_SYMBOL_PATTERNS = [
    (re.compile(r"^(render|component|view|widget|style)", re.I), "ui", 1.0),
    (re.compile(r"(migrate|schema|query|execute|insert|update|delete|seed|transaction)", re.I),
     "data_model", 1.0),
    (re.compile(r"^(handle|on[A-Z]|route|dispatch|process|run|execute|serve|listen)", re.I),
     "control_flow", 1.0),
    (re.compile(r"(config|setup|deploy|install)", re.I), "infra_config", 1.0),
    (re.compile(r"^(test|it_|should)", re.I), "tests", 1.0),
]


def _added_lines(context: dict) -> str:
    """All added diff lines joined, from structured changes."""
    parts = []
    for sc in (context or {}).get("structured_changes", []) or []:
        diff = sc.get("diff") or ""
        parts.extend(l for l in diff.splitlines() if l.startswith("+"))
    return "\n".join(parts)


def classify_change(changes: dict, context: dict | None = None) -> dict:
    """Return the typed change-kind field:
    {"weights": {domain: 0..1}, "dominant": str, "signals": [str]}.

    Weights are normalized signal scores; dominant is the max-weight domain
    ("" when nothing fired — callers must treat that as "unknown", not a
    failure). Never raises; empty input returns zero weights.
    """
    scores = {d: 0.0 for d in DOMAINS}
    signals = []

    files = (changes or {}).get("files", []) or []
    paths = [f.get("path", "") for f in files if f.get("path")]

    for path in paths:
        norm = path.replace("\\", "/")
        for pat, domain, weight in _PATH_PATTERNS:
            if pat.search(norm):
                scores[domain] += weight
                signals.append(f"path:{norm}→{domain}")

    added = _added_lines(context or {})
    if added:
        for pat, domain, weight in _SHAPE_PATTERNS:
            if pat.search(added):
                scores[domain] += weight
                signals.append(f"shape:{pat.pattern[:24]}…→{domain}")

    for f in files:
        for fn in f.get("changed_functions", []) or []:
            name = fn.get("name", "")
            if not name or name == "<module>":
                continue
            for pat, domain, weight in _SYMBOL_PATTERNS:
                if pat.search(name):
                    scores[domain] += weight
                    signals.append(f"symbol:{name}→{domain}")

    total = sum(scores.values())
    if total > 0:
        weights = {d: round(scores[d] / total, 2) for d in DOMAINS}
        dominant = max(DOMAINS, key=lambda d: weights[d])
    else:
        weights = {d: 0.0 for d in DOMAINS}
        dominant = ""

    return {
        "weights": weights,
        "dominant": dominant,
        "signals": signals[:12],
    }
