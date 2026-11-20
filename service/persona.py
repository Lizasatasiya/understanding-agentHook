"""Rewrite one developer's persona summary when an interview ends.

Counts are not stored on the persona row. They are counted from
commit_attempts and assessments. An answer passes when its score is at least 75.
"""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from service.llm.api_utils import (
    call_llm_api,
    extract_json,
    get_model,
    load_nous_api_key,
)

from service.trace import trace

from .models import Answer, Assessment, CommitAttempt, DeveloperPersona, Question


_OPEN = ("in_progress", "no_questions", "expired", "error")


def _outcome(attempt: CommitAttempt, failed: bool) -> str | None:
    if attempt.status == "skip":
        return "allowed"
    if attempt.status in _OPEN:
        return None
    if failed:
        return "rejected"
    if attempt.question_count > 0 and attempt.answered_count == attempt.question_count:
        return "allowed"
    return None


def _facts(db: Session, developer_id, github_email: str) -> dict:
    attempts = db.execute(
        select(CommitAttempt).where(CommitAttempt.developer_id == developer_id)
    ).scalars().all()
    failed_ids = set()
    attempt_ids = [attempt.public_id for attempt in attempts]
    if attempt_ids:
        failed_ids = set(db.execute(
            select(Question.attempt_id)
            .join(Answer, Answer.question_id == Question.id)
            .join(Assessment, Assessment.answer_id == Answer.id)
            .where(Question.attempt_id.in_(attempt_ids), Assessment.passed.is_(False))
        ).scalars().all())
    allowed = 0
    rejected = 0
    means = []
    for attempt in attempts:
        outcome = _outcome(attempt, attempt.public_id in failed_ids)
        if outcome == "allowed":
            allowed += 1
        elif outcome == "rejected":
            rejected += 1
        if attempt.mean_score is not None:
            means.append(float(attempt.mean_score))
    judged = allowed + rejected
    return {
        "github_email": github_email,
        "attempt_count": judged,
        "allowed_count": allowed,
        "rejected_count": rejected,
        "mean_score": round(sum(means) / len(means), 1) if means else 0.0,
        "pass_rate": round(allowed / judged, 3) if judged else 0.0,
    }


def _deterministic_summary(facts: dict) -> str:
    rate = float(facts["pass_rate"]) * 100
    return (
        f"{facts['github_email']} has finished {facts['attempt_count']} interviews "
        f"with a pass rate of {rate:.0f}% and a mean score of {facts['mean_score']:.0f}."
    )


def _model_summary(facts: dict) -> str:
    api_key = load_nous_api_key()
    model = get_model()
    if not api_key or not model:
        return ""
    prompt = (
        "Write one or two sentences describing this developer's code-understanding record. "
        "Use only the aggregates below. Do not mention a specific question, answer, or diff, "
        "and do not invent traits that the numbers do not support.\n"
        + json.dumps(facts, sort_keys=True)
    )
    status, body = call_llm_api(api_key, {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 180,
    }, timeout=30)
    if status != 200:
        return ""
    try:
        result = json.loads(body)
        choices = result.get("choices") or []
        if not choices:
            return ""
        text_out = ((choices[0].get("message") or {}).get("content") or "").strip()
    except Exception:
        parsed = extract_json(body)
        text_out = parsed if isinstance(parsed, str) else ""
    return text_out[:500]


def write_summary(db: Session, developer_id, github_email: str) -> DeveloperPersona:
    """Store a fresh persona sentence from the current ledger totals."""
    facts = _facts(db, developer_id, github_email)
    summary = _model_summary(facts) or _deterministic_summary(facts)
    trace("ledger.persona", source="persona.write_summary", input=facts, output=summary)
    persona = db.get(DeveloperPersona, developer_id)
    if persona is None:
        persona = DeveloperPersona(developer_id=developer_id, summary=summary)
        db.add(persona)
    else:
        persona.summary = summary
    db.flush()
    return persona
