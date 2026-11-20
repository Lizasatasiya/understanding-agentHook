"""Write one interview into the ledger.

The live interview still runs in memory. These functions copy the attempt,
the questions, each answer, and the model's score into Postgres. The persona
summary is rewritten from the ledger tables when the interview ends.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from service.llm.api_utils import get_judge_model, get_model

from .models import (
    Answer,
    Assessment,
    CommitAttempt,
    Developer,
    Question,
    Repository,
)
from .persona import write_summary
from .trace import trace


def _summary_text(summary: dict | None) -> str:
    if not isinstance(summary, dict) or not summary:
        return ""
    parts = []
    for key in ("what_changed", "impact", "key_risks"):
        value = summary.get(key)
        if isinstance(value, str) and value.strip() and value.strip().upper() != "N/A":
            parts.append(value.strip())
    return " ".join(parts)


def _concepts(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    labels = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            labels.append(item.strip()[:200])
    return labels[:8]


def _score(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(100.0, number))


def get_or_create_developer(db: Session, github_email: str) -> Developer:
    email = github_email.strip().lower()
    row = db.execute(select(Developer).where(Developer.github_email == email)).scalar_one_or_none()
    if row is not None:
        return row
    row = Developer(public_id=uuid.uuid4(), github_email=email)
    db.add(row)
    db.flush()
    return row


def get_or_create_repository(db: Session, name: str) -> Repository:
    label = name.strip() or "unknown"
    row = db.execute(
        select(Repository).where(Repository.name == label, Repository.remote_url.is_(None))
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = Repository(name=label)
    db.add(row)
    db.flush()
    return row


def persist_start(db: Session, payload: dict, public: dict, record: dict) -> None:
    """Insert the attempt and, when questions were asked, each question row."""
    developer = get_or_create_developer(db, payload.get("git_email") or "")
    repository = get_or_create_repository(db, payload.get("repository") or "")
    status = record.get("status") or "in_progress"
    questions = list(record.get("questions") or [])
    attempt_id = uuid.UUID(public["defense_id"]) if public.get("defense_id") else uuid.uuid4()
    question_model = get_model() or None
    attempt = CommitAttempt(
        public_id=attempt_id,
        developer_id=developer.public_id,
        repository_id=repository.id,
        branch=(payload.get("branch") or "").strip(),
        status=status,
        question_model=question_model,
        judge_model=get_judge_model() or None,
        standards_failed=bool(payload.get("standards_failed")),
        trivial=bool(payload.get("trivial")),
        question_count=len(questions) if status == "in_progress" else 0,
        answered_count=0,
        summary=_summary_text(record.get("summary")),
    )
    db.add(attempt)
    db.flush()
    if status != "in_progress":
        return
    interview = record.get("interview")
    row_ids = {}
    for ordinal, question in enumerate(questions, 1):
        client_id = str(question.get("question_id") or f"q{ordinal}")
        row = Question(
            attempt_id=attempt.public_id,
            ordinal=ordinal,
            client_question_id=client_id,
            question_text=str(question.get("question") or ""),
            question_type=str(question.get("type") or "").strip() or "Reasoning",
            expected_concepts=_concepts(question.get("expected_concepts")),
            model=question_model,
        )
        db.add(row)
        db.flush()
        row_ids[client_id] = row.id
    if interview is not None:
        interview["developer_id"] = developer.public_id
        interview["question_row_ids"] = row_ids


def persist_answer(db: Session, interview: dict, question: dict, answer_text: str, evaluation, *, finished: bool) -> None:
    """Store one answer and its score, then refresh the attempt counts."""
    client_id = str(question.get("question_id") or "")
    question_row_id = (interview.get("question_row_ids") or {}).get(client_id)
    if question_row_id is None:
        trace("ledger.persist_answer.skipped", source="ledger.persist_answer", question_id=client_id, reason="no stored question row")
        return
    answer = Answer(
        question_id=question_row_id,
        answer_text=answer_text or "",
        timed_out=not (answer_text or "").strip(),
    )
    db.add(answer)
    db.flush()
    covered = _concepts(getattr(evaluation, "covered_concepts", None))
    missing = _concepts(getattr(evaluation, "missing_concepts", None))
    confidence = getattr(evaluation, "confidence", None)
    try:
        confidence = None if confidence is None else float(confidence)
    except (TypeError, ValueError):
        confidence = None
    db.add(Assessment(
        answer_id=answer.id,
        score=_score(getattr(evaluation, "score", 0)),
        evaluation=str(getattr(evaluation, "evaluation", "") or ""),
        confidence=confidence,
        llm_verified=bool(getattr(evaluation, "llm_verified", False)),
        missing_concepts=missing,
        covered_concepts=covered,
        judge_model=get_judge_model() or None,
    ))
    defense_id = interview.get("defense_id")
    if defense_id is None:
        return
    attempt = db.get(CommitAttempt, uuid.UUID(str(defense_id)))
    if attempt is None:
        return
    scores = [_score(item.get("score")) for item in interview.get("scored") or []]
    if not scores:
        scores = [_score(getattr(evaluation, "score", 0))]
    attempt.answered_count = len(scores)
    attempt.mean_score = round(sum(scores) / len(scores), 2)
    if finished:
        attempt.status = "completed"
        db.flush()
        developer_id = interview.get("developer_id") or attempt.developer_id
        write_summary(db, developer_id, interview.get("git_email") or "")
    db.flush()
    trace(
        "ledger.persist_answer",
        source="ledger.persist_answer",
        input={
            "defense_id": str(defense_id),
            "question_id": client_id,
            "answer": answer_text,
            "score": _score(getattr(evaluation, "score", 0)),
            "evaluation": str(getattr(evaluation, "evaluation", "") or ""),
        },
        output={
            "mean_score": float(attempt.mean_score or 0),
            "answered": attempt.answered_count,
            "status": attempt.status,
        },
    )
