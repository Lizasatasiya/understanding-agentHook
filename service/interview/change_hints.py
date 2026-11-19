"""Prompt lines for the question generator, from the hook's change classification.

The hook classifies the diff. These lines only shape the model prompt.
"""

DOMAINS = ("ui", "data_model", "control_flow", "infra_config", "tests", "docs")


def classification_hint_lines(change_kind: dict | None) -> list:
    """Prompt lines describing the classification. Empty when there is nothing to say."""
    if not change_kind or not change_kind.get("dominant"):
        return []
    weights = change_kind.get("weights") or {}
    strong = [domain for domain in DOMAINS if weights.get(domain, 0) >= 0.25]
    dominant = change_kind["dominant"]
    mixed = [domain for domain in strong if domain != dominant]
    lines = ["CHANGE CLASSIFICATION (deterministic tier):"]
    lines.append(f"- dominant: {dominant}" + (f" (mixed with {', '.join(mixed)})" if len(strong) > 1 else ""))
    if change_kind.get("signals"):
        lines.append("- signals: " + "; ".join(change_kind["signals"][:6]))
    guidance = {
        "ui": (
            "target data flow through the component, re-render consequences, "
            "and state ownership — not styling trivia"
        ),
        "data_model": (
            "target migration/consistency consequences, constraint "
            "correctness, and rollback — not column trivia"
        ),
        "control_flow": (
            "target failure modes, edge cases, and ordering "
            "dependencies in the new flow"
        ),
        "infra_config": (
            "target environment coupling, defaults, and rollback/"
            "blast radius of the configuration change"
        ),
        "tests": (
            "target what behavior the new tests pin down and what they "
            "still leave uncovered"
        ),
        "docs": (
            "target accuracy of the documentation against the code it "
            "describes"
        ),
    }
    focus = guidance.get(dominant)
    if focus:
        lines.append(f"- question focus: {focus}")
    return lines
