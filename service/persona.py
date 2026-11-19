"""Developer persona aggregates and the questions they were asked.

Each finished or stopped interview folds its scores into one row keyed by
the git email, and appends the question and answer text for that developer.
Nothing here is read when the next diff is assessed.
"""

import datetime
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from service.llm.api_utils import (
    call_llm_api,
    extract_json,
    get_model,
    load_nous_api_key,
)

from .models import DeveloperExchange, DeveloperPersona, User

_MAX_CONCEPTS = 40


def get_or_create_user(db: Session, git_email: str, git_name: str) -> User:
    email = git_email.strip().lower()
    name = git_name.strip()
    row = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if row:
        row.full_name = name or row.full_name
        return row
    row = User(
        email=email,
        full_name=name,
        system_username=name,
    )
    db.add(row)
    db.flush()
    return row


def _top_concepts(counts: dict) -> list:
    ranked = sorted(counts.items(), key=lambda item: (-int(item[1]), item[0]))
    return [name for name, _count in ranked[:8]]


def _category_means(buckets: dict) -> dict:
    means = {}
    for name, bucket in buckets.items():
        count = int(bucket.get("count") or 0)
        if count:
            means[name] = round(float(bucket.get("sum") or 0) / count, 1)
    return means


def _facts(persona: DeveloperPersona, git_name: str) -> dict:
    return {
        "git_name": git_name,
        "git_email": persona.github_username,
        "attempt_count": persona.attempt_count,
        "pass_count": persona.pass_count,
        "fail_count": persona.fail_count,
        "mean_score": round(persona.mean_score, 1),
        "pass_rate": round(persona.pass_rate, 3),
        "category_means": _category_means(persona.scores_by_category or {}),
        "top_missing_concepts": _top_concepts(persona.missing_concepts or {}),
    }


def _deterministic_summary(facts: dict) -> str:
    rate = float(facts["pass_rate"]) * 100
    return (
        f"{facts['git_name']} ({facts['git_email']}) has finished {facts['attempt_count']} interviews "
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
        text = ((choices[0].get("message") or {}).get("content") or "").strip()
    except Exception:
        parsed = extract_json(body)
        text = parsed if isinstance(parsed, str) else ""
    return text[:500]


def _record_exchanges(db: Session, persona: DeveloperPersona, user: User, scored: list) -> None:
    for item in scored:
        question = str(item.get("question") or "")[:2000]
        answer = str(item.get("answer") or "")[:8000]
        if not question and not answer:
            continue
        concepts = []
        for concept in item.get("missing_concepts") or []:
            if isinstance(concept, str) and concept.strip():
                concepts.append(concept.strip()[:200])
        db.add(DeveloperExchange(
            persona_id=persona.id,
            user_id=user.id,
            question_text=question,
            answer_text=answer,
            score=float(item.get("score") or 0),
            category=str(item.get("category") or "Reasoning")[:100],
            missing_concepts=concepts[:8],
            evaluation_feedback=str(item.get("evaluation") or "")[:4000],
        ))


def fold_persona(db: Session, git_email: str, git_name: str, *, passed: bool, scored: list) -> DeveloperPersona:
    """Update one developer's persona and store the questions and answers from this interview.

    The row is keyed by the git email on the commit. `scored` items are
    `{question, answer, category, score, missing_concepts, evaluation}`.
    """
    user = get_or_create_user(db, git_email, git_name)
    persona = db.execute(
        select(DeveloperPersona).where(DeveloperPersona.github_username == user.email)
    ).scalar_one_or_none()
    if persona is None:
        persona = DeveloperPersona(
            github_username=user.email,
            user_id=user.id,
            scores_by_category={},
            missing_concepts={},
        )
        db.add(persona)
        db.flush()

    persona.user_id = user.id
    scores = [float(item.get("score") or 0) for item in scored]
    attempt_score = sum(scores) / len(scores) if scores else 0.0
    persona.attempt_count = int(persona.attempt_count or 0) + 1
    persona.score_sum = float(persona.score_sum or 0) + attempt_score
    persona.mean_score = persona.score_sum / persona.attempt_count
    if passed:
        persona.pass_count = int(persona.pass_count or 0) + 1
    else:
        persona.fail_count = int(persona.fail_count or 0) + 1
    persona.pass_rate = persona.pass_count / persona.attempt_count

    categories = dict(persona.scores_by_category or {})
    for item in scored:
        name = (item.get("category") or "Reasoning").strip() or "Reasoning"
        bucket = dict(categories.get(name) or {"sum": 0, "count": 0})
        bucket["sum"] = float(bucket.get("sum") or 0) + float(item.get("score") or 0)
        bucket["count"] = int(bucket.get("count") or 0) + 1
        bucket["mean"] = bucket["sum"] / bucket["count"]
        categories[name] = bucket
    persona.scores_by_category = categories

    concepts = dict(persona.missing_concepts or {})
    for item in scored:
        for concept in item.get("missing_concepts") or []:
            if not isinstance(concept, str):
                continue
            label = concept.strip()[:200]
            if not label:
                continue
            concepts[label] = int(concepts.get(label) or 0) + 1
    if len(concepts) > _MAX_CONCEPTS:
        kept = sorted(concepts.items(), key=lambda item: (-int(item[1]), item[0]))[:_MAX_CONCEPTS]
        concepts = dict(kept)
    persona.missing_concepts = concepts
    persona.last_attempt_at = datetime.datetime.now(datetime.timezone.utc)

    facts = _facts(persona, git_name or user.full_name)
    summary = _model_summary(facts) or _deterministic_summary(facts)
    persona.summary = summary
    _record_exchanges(db, persona, user, scored)
    db.flush()
    return persona
