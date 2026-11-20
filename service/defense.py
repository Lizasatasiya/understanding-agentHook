"""In-memory oral defense.

One start call writes a summary and questions for that diff. Each answer is
scored on its own. The interview lives only until it passes, a partial
answer stops it, or the timeout elapses. When it ends, the developer's
persona and the questions and answers from this interview are stored.
The next diff does not read them.
"""

import re
import threading
import time
import uuid

from sqlalchemy.orm import Session

from .interview.gateway import ModelGateway
from .ledger import persist_answer, persist_start
from .settings import ServiceSettings
from .trace import trace
from .trace import trace

_LOCK = threading.Lock()
_INTERVIEWS: dict[str, dict] = {}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DefenseError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _purge_locked(now: float, ttl: int) -> None:
    expired = [key for key, row in _INTERVIEWS.items() if now - row["created"] > ttl]
    for key in expired:
        _INTERVIEWS.pop(key, None)


def _public_question(question: dict, index: int) -> dict:
    return {
        "question_id": question.get("question_id") or f"q{index}",
        "question": question.get("question") or "",
        "type": question.get("type") or "Reasoning",
        "time_limit": question.get("time_limit") or question.get("time_limit_seconds") or 60,
    }


class DefensePipeline:
    """Ordered server steps. The step list comes from ServiceSettings."""

    def __init__(self, settings: ServiceSettings | None = None, gateway: ModelGateway | None = None):
        self.settings = settings or ServiceSettings.from_env()
        self.gateway = gateway or ModelGateway()

    def begin(self, payload: dict) -> tuple[dict, dict]:
        if not isinstance(payload, dict):
            raise DefenseError(400, "Malformed payload")
        git_name = (payload.get("git_name") or "").strip()
        git_email = (payload.get("git_email") or "").strip().lower()
        if not git_name or not _EMAIL.match(git_email):
            raise DefenseError(400, "git user.name and user.email are required")
        context = payload.get("context")
        if not isinstance(context, dict):
            raise DefenseError(400, "Staged change context is required")

        trace(
            "defense.begin",
            source="DefensePipeline",
            input={
                "git_email": git_email,
                "repository": payload.get("repository") or "",
                "branch": payload.get("branch") or "",
                "trivial": bool(payload.get("trivial")),
                "standards_failed": bool(payload.get("standards_failed")),
                "files": len((context or {}).get("structured_changes") or []),
            },
            output={"steps": list(self.settings.start_steps)},
        )
        state = {"summary": {}, "questions": [], "findings": [], "action": "ask", "message": ""}
        for step in self.settings.start_steps:
            handler = getattr(self, f"_step_{step}", None)
            if handler is None:
                raise DefenseError(500, f"Unknown defense step: {step}")
            trace("defense.step", source=f"DefensePipeline._step_{step}", step=step)
            if handler(payload, state):
                trace("defense.step.done", step=step, action=state["action"], message=state["message"])
                break
            trace("defense.step.done", step=step, action=state["action"])

        if state["action"] == "skip":
            return {
                "action": "skip",
                "defense_id": "",
                "questions": [],
                "message": state["message"],
            }, {"summary": state["summary"], "questions": [], "status": "skip"}

        if state["action"] == "no_questions":
            return {
                "action": "no_questions",
                "defense_id": "",
                "questions": [],
                "findings": [],
                "message": state["message"],
            }, {"summary": state["summary"], "questions": [], "status": "no_questions"}

        questions = state["questions"]
        defense_id = uuid.uuid4().hex
        now = time.time()
        interview = {
            "defense_id": defense_id,
            "git_name": git_name,
            "git_email": git_email,
            "questions": questions,
            "context": context,
            "summary": state["summary"],
            "cursor": 0,
            "scored": [],
            "created": now,
        }
        with _LOCK:
            _purge_locked(now, self.settings.interview_ttl_seconds)
            _INTERVIEWS[defense_id] = interview
        return {
            "action": "ask",
            "defense_id": defense_id,
            "findings": state.get("findings") or [],
            "questions": [_public_question(question, index) for index, question in enumerate(questions, 1)],
        }, {
            "summary": state["summary"],
            "questions": questions,
            "status": "in_progress",
            "interview": interview,
        }

    def _step_summary(self, payload: dict, state: dict) -> bool:
        state["summary"] = self.gateway.summarize(payload.get("context") or {})
        return False

    def _step_trivial_gate(self, payload: dict, state: dict) -> bool:
        if not self.settings.bypass_trivial:
            return False
        if payload.get("trivial") and not payload.get("standards_failed"):
            state["action"] = "skip"
            state["message"] = (
                "All staged changes are trivial. No oral defense required — commit allowed."
            )
            trace(
                "defense.trivial_gate",
                source="DefensePipeline",
                input={"trivial": True, "standards_failed": False},
                output={"skipped": True, "action": "skip"},
            )
            return True
        trace(
            "defense.trivial_gate",
            source="DefensePipeline",
            input={
                "trivial": bool(payload.get("trivial")),
                "standards_failed": bool(payload.get("standards_failed")),
            },
            output={"skipped": False},
        )
        return False

    def _step_questions(self, payload: dict, state: dict) -> bool:
        result = self.gateway.questions(payload, state["summary"])
        questions = result.get("questions") or []
        state["findings"] = result.get("findings") or []
        if not questions:
            state["action"] = "no_questions"
            state["message"] = "The model did not return any questions."
            return True
        state["questions"] = questions
        state["action"] = "ask"
        return False


def start_defense(payload: dict, pipeline: DefensePipeline | None = None, db: Session | None = None) -> dict:
    public, record = (pipeline or DefensePipeline()).begin(payload)
    if db is not None:
        trace("ledger.persist_start", source="ledger.persist_start", status=record.get("status"), defense_id=public.get("defense_id") or "")
        persist_start(db, payload, public, record)
    trace("defense.started", action=public.get("action"), defense_id=public.get("defense_id") or "", status=record.get("status"))
    return public


def _score_item(question: dict, evaluation, answer_text: str) -> dict:
    missing = []
    for concept in evaluation.missing_concepts or []:
        if isinstance(concept, str) and concept.strip():
            missing.append(concept.strip()[:200])
    return {
        "question": question.get("question") or "",
        "answer": answer_text,
        "category": question.get("type") or "Reasoning",
        "score": float(evaluation.score),
        "missing_concepts": missing[:8],
        "evaluation": getattr(evaluation, "evaluation", "") or "",
    }


def submit_answer(db: Session, defense_id: str, question_id: str, answer_text: str) -> dict:
    settings = ServiceSettings.from_env()
    threshold = settings.passing_score
    with _LOCK:
        _purge_locked(time.time(), settings.interview_ttl_seconds)
        interview = _INTERVIEWS.get(defense_id)
        if interview is None:
            raise DefenseError(404, "This interview is no longer active")
        cursor = interview["cursor"]
        questions = interview["questions"]
        if cursor >= len(questions):
            raise DefenseError(409, "This interview has already finished")
        question = questions[cursor]
        if question.get("question_id") != question_id:
            raise DefenseError(409, "Answer the current question")
        context = interview["context"]
        summary = interview["summary"]

    gateway = ModelGateway()
    evaluation = gateway.assess(question, answer_text, context, summary)
    scored = _score_item(question, evaluation, answer_text)
    stopped = evaluation.score < threshold
    finished = stopped or cursor + 1 >= len(questions)
    trace(
        "defense.scored",
        source="submit_answer",
        input={"defense_id": defense_id, "question_id": question_id, "answer": answer_text},
        output={
            "score": evaluation.score,
            "threshold": threshold,
            "evaluation": getattr(evaluation, "evaluation", "") or "",
            "stopped": stopped,
            "finished": finished,
        },
    )

    if finished:
        with _LOCK:
            current = _INTERVIEWS.get(defense_id)
            if current is None:
                raise DefenseError(404, "This interview is no longer active")
            current["scored"].append(scored)
        try:
            persist_answer(db, current, question, answer_text, evaluation, finished=True)
        except Exception:
            with _LOCK:
                if current.get("scored") and current["scored"][-1] is scored:
                    current["scored"].pop()
            db.rollback()
            raise
        with _LOCK:
            _INTERVIEWS.pop(defense_id, None)
        return {
            "action": "pass" if not stopped else "stop",
            "score": evaluation.score,
            "question_id": question_id,
        }

    with _LOCK:
        current = _INTERVIEWS.get(defense_id)
        if current is None:
            raise DefenseError(404, "This interview is no longer active")
        current["scored"].append(scored)
    try:
        persist_answer(db, current, question, answer_text, evaluation, finished=False)
    except Exception:
        with _LOCK:
            if current.get("scored") and current["scored"][-1] is scored:
                current["scored"].pop()
        db.rollback()
        raise
    with _LOCK:
        current = _INTERVIEWS.get(defense_id)
        if current is not None:
            current["cursor"] = cursor + 1
    return {"action": "continue", "score": evaluation.score, "question_id": question_id}
