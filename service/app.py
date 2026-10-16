"""Central Understanding Service (Pokerface P3 / Judas ledger).

FastAPI ingestion for hook session payloads: zero-touch JIT provisioning
(users/repos created on first sight from the payload's identity block),
idempotent session upsert, batch drain for the offline queue, and the
proof-of-defense lookup that CI/pre-receive uses to detect unaudited
commits (docs/ENFORCEMENT.md).

Auth (anti-spoofing, V1):
  - X-Team-Token: shared team token (SERVICE_TEAM_TOKEN). Proves "someone
    holding the team secret", not "this developer answered honestly" — the
    documented ceiling.
  - X-Signature: HMAC-SHA256 over the CANONICAL payload (sorted keys, no
    whitespace) with SERVICE_TEAM_SECRET — same canonicalization as the
    hook's server_client. Verified with compare_digest.

Every response carries schema_version so clients can migrate safely.

Privacy/retention (ships with this, not later — see the amended
CENTRALIZED_DATABASE_ARCHITECTURE.md): raw answer text is retained for
SERVICE_RETENTION_DAYS (default 90) then nulled by purge_expired_answers
(keep the scores/aggregates forever, drop the personal raw text).
"""

import datetime
import hmac as hmac_mod
import json
import os

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    Base,
    Organization,
    Repository,
    SessionQuestion,
    SessionRecord,
    Team,
    TeamMember,
    User,
)

API_SCHEMA_VERSION = 1
DEFAULT_RETENTION_DAYS = 90


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = hmac_mod.new(secret.encode("utf-8"), body, "sha256").hexdigest()
    return hmac_mod.compare_digest(expected, signature or "")


def _canonicalize_request(body: bytes) -> bytes:
    """The hook signs the canonical form of the payload; re-derive it."""
    return _canonical(json.loads(body.decode("utf-8")))


class ServiceState:
    """Holds engine/session factory + auth config; swappable in tests."""

    def __init__(self, database_url: str | None = None):
        url = database_url or os.environ.get(
            "SERVICE_DATABASE_URL", "sqlite:///./understanding_ledger.db")
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.team_token = os.environ.get("SERVICE_TEAM_TOKEN", "").strip()
        self.team_secret = os.environ.get("SERVICE_TEAM_SECRET", "").strip()
        self.admin_token = os.environ.get("SERVICE_ADMIN_TOKEN", "").strip()
        try:
            self.retention_days = int(os.environ.get("SERVICE_RETENTION_DAYS",
                                                     str(DEFAULT_RETENTION_DAYS)))
        except ValueError:
            self.retention_days = DEFAULT_RETENTION_DAYS


STATE: ServiceState | None = None


def get_state() -> ServiceState:
    global STATE
    if STATE is None:
        STATE = ServiceState()
    return STATE


def get_db():
    db = get_state().SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SessionPayload(BaseModel):
    model_config = {"extra": "allow"}
    session_id: str
    schema_version: int = 2
    status: str = "FAILED"
    verification: str = "llm-unverified"
    score: float = 0.0
    attempt_number: int = 1
    branch: str = "unknown"
    head_commit: str = ""
    diff_hash: str = ""
    commit_id: str = ""
    questions: list = []


class BatchPayload(BaseModel):
    sessions: list


def _auth(team_token: str | None, x_team_token: str | None):
    if team_token and x_team_token != team_token:
        raise HTTPException(status_code=401, detail="Invalid team token")


def _upsert_session(db: Session, payload: dict) -> SessionRecord:
    """JIT-provision user/repo, then insert-or-update by session_id (idempotent)."""
    identity = payload.get("identity") or {}
    email = (identity.get("user_email") or "unknown@local.dev").lower().strip()
    name = (identity.get("user_name") or "Developer").strip()
    repo_slug = (identity.get("repo_slug") or "unknown/repo").strip()

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        user = User(email=email, full_name=name,
                    system_username=identity.get("system_user", ""))
        db.add(user)
        db.flush()
    else:
        user.last_active_at = func.now()
        if name and user.full_name != name:
            user.full_name = name

    repo = db.execute(
        select(Repository).where(Repository.repo_slug == repo_slug)).scalar_one_or_none()
    if not repo:
        repo = Repository(
            repo_slug=repo_slug,
            platform=identity.get("git_platform", "custom"),
            remote_url=identity.get("remote_url", ""),
        )
        db.add(repo)
        db.flush()

    sid = payload.get("session_id")
    row = db.execute(
        select(SessionRecord).where(SessionRecord.session_id == sid)).scalar_one_or_none()
    if not row:
        row = SessionRecord(session_id=sid)
        db.add(row)

    row.schema_version = payload.get("schema_version", 2)
    row.user_id = user.id
    row.repo_id = repo.id
    row.branch = payload.get("branch", "unknown")
    row.commit_hash = payload.get("commit_id", "")
    row.head_commit = payload.get("head_commit", "")
    row.diff_hash = payload.get("diff_hash", "")
    row.attempt_number = payload.get("attempt_number", 1)
    row.status = payload.get("status", "FAILED")
    row.verification = payload.get("verification", "llm-unverified")
    row.score = payload.get("score", 0.0)
    row.violations_count = payload.get("violations_count", 0)
    row.machine_id = identity.get("machine_id", "")
    row.identity = identity
    row.changed_files = payload.get("changed_files", [])
    row.change_summary = payload.get("change_summary", {})
    row.standards_report = payload.get("standards_report", [])
    row.evidence = payload.get("evidence", {})
    row.analysis = payload.get("analysis", {})

    # Replace per-question audit rows on re-ingest (idempotent shape)
    db.query(SessionQuestion).filter(
        SessionQuestion.session_row_id == row.id).delete()
    db.flush()
    for q in payload.get("questions", []):
        ev = q.get("evaluation") or {}
        raw_score = (ev.get("score") if isinstance(ev, dict) and "score" in ev
                     else q.get("score", 0))
        try:
            q_score = float(raw_score or 0)
        except (TypeError, ValueError):
            q_score = 0.0
        db.add(SessionQuestion(
            session_row_id=row.id,
            question_id=str(q.get("question_id", "")),
            question_text=str(q.get("question", ""))[:2000],
            answer_text=str(q.get("answer", ""))[:8000],
            score=q_score,
            category=str(q.get("category", q.get("type", "general")))[:100],
            evaluation_feedback=str(ev.get("evaluation", ev.get("rationale", "")))[:4000]
            if isinstance(ev, dict) else "",
            answered_in_attempt=int(q.get("answered_in_attempt", 1)),
            llm_verified=bool(ev.get("llm_verified", False)) if isinstance(ev, dict) else False,
        ))
    db.flush()
    return row


def create_app(state: ServiceState | None = None) -> FastAPI:
    global STATE
    if state is not None:
        STATE = state

    app = FastAPI(title="Understanding Central Ledger", version="0.4.0")

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "schema_version": API_SCHEMA_VERSION}

    @app.post("/api/v1/sessions")
    async def record_session(
        request: Request,
        db: Session = Depends(get_db),
        x_team_token: str | None = Header(None),
        x_signature: str | None = Header(None),
    ):
        st = get_state()
        _auth(st.team_token, x_team_token)
        request_body = await request.body()
        if st.team_secret:
            canonical = _canonicalize_request(request_body)
            if not _verify_signature(st.team_secret, canonical, x_signature or ""):
                raise HTTPException(status_code=401, detail="Invalid payload signature")
        try:
            payload = json.loads(request_body.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Malformed payload")
        if not isinstance(payload, dict) or not payload.get("session_id"):
            raise HTTPException(status_code=400, detail="Missing session_id")
        row = _upsert_session(db, payload)
        db.commit()
        return JSONResponse({
            "status": "success",
            "schema_version": API_SCHEMA_VERSION,
            "session_id": row.session_id,
            "user_id": row.user_id,
            "repo_id": row.repo_id,
        })

    @app.post("/api/v1/sessions/batch")
    async def record_batch(
        batch: BatchPayload,
        db: Session = Depends(get_db),
        x_team_token: str | None = Header(None),
        x_signature: str | None = Header(None),
    ):
        st = get_state()
        _auth(st.team_token, x_team_token)
        if st.team_secret:
            # The client signs the canonical form of the batch wrapper
            # ({"sessions": [...]}) — same canonicalization as the hook.
            canonical = _canonical({"sessions": batch.sessions})
            if not _verify_signature(st.team_secret, canonical, x_signature or ""):
                raise HTTPException(status_code=401, detail="Invalid payload signature")
        ingested = 0
        for payload in batch.sessions:
            if not isinstance(payload, dict) or not payload.get("session_id"):
                continue
            _upsert_session(db, payload)
            ingested += 1
        db.commit()
        return JSONResponse({
            "status": "success",
            "schema_version": API_SCHEMA_VERSION,
            "ingested": ingested,
        })

    @app.get("/api/v1/defense")
    def defense(
        repo_slug: str = Query(...),
        frm: str | None = Query(None, alias="from"),
        to: str | None = Query(None, alias="to"),
        db: Session = Depends(get_db),
    ):
        """Proof-of-defense rows for CI/pre-receive (docs/ENFORCEMENT.md):
        one row per audited commit attempt, newest first."""
        repo = db.execute(
            select(Repository).where(Repository.repo_slug == repo_slug)).scalar_one_or_none()
        if not repo:
            return JSONResponse({"schema_version": API_SCHEMA_VERSION, "sessions": []})
        stmt = (
            select(SessionRecord, User.email)
            .join(User, SessionRecord.user_id == User.id, isouter=True)
            .where(SessionRecord.repo_id == repo.id)
            .order_by(SessionRecord.created_at.desc())
        )
        rows = db.execute(stmt).all()
        out = []
        for record, email in rows:
            ts = record.created_at.isoformat() if record.created_at else ""
            if frm and ts and ts < frm:
                continue
            if to and ts and ts > to:
                continue
            out.append({
                "session_id": record.session_id,
                "user_email": email,
                "branch": record.branch,
                "commit_hash": record.commit_hash,
                "head_commit": record.head_commit,
                "diff_hash": record.diff_hash,
                "attempt_number": record.attempt_number,
                "status": record.status,
                "verification": record.verification,
                "score": record.score,
                "created_at": ts,
            })
        return JSONResponse({"schema_version": API_SCHEMA_VERSION, "sessions": out})

    @app.post("/api/v1/admin/repositories")
    async def assign_repo(
        payload: dict,
        db: Session = Depends(get_db),
        x_admin_token: str | None = Header(None),
    ):
        """Assign a repository to a team (org/team JIT-created).
        This is the org-hierarchy wiring: per-team views resolve through it."""
        st = get_state()
        if st.admin_token and x_admin_token != st.admin_token:
            raise HTTPException(status_code=401, detail="Invalid admin token")
        repo_slug = (payload.get("repo_slug") or "").strip()
        org_name = (payload.get("org") or "default").strip()
        team_name = (payload.get("team") or "default").strip()
        if not repo_slug:
            raise HTTPException(status_code=400, detail="Missing repo_slug")
        org = db.execute(
            select(Organization).where(Organization.name == org_name)).scalar_one_or_none()
        if not org:
            org = Organization(name=org_name)
            db.add(org)
            db.flush()
        team = db.execute(
            select(Team).where(Team.org_id == org.id, Team.name == team_name)
        ).scalar_one_or_none()
        if not team:
            team = Team(org_id=org.id, name=team_name)
            db.add(team)
            db.flush()
        repo = db.execute(
            select(Repository).where(Repository.repo_slug == repo_slug)).scalar_one_or_none()
        if not repo:
            repo = Repository(repo_slug=repo_slug)
            db.add(repo)
        repo.team_id = team.id
        db.commit()
        return JSONResponse({
            "status": "success", "schema_version": API_SCHEMA_VERSION,
            "repo_slug": repo_slug, "org": org_name, "team": team_name,
        })

    @app.post("/api/v1/admin/members")
    async def add_member(
        payload: dict,
        db: Session = Depends(get_db),
        x_admin_token: str | None = Header(None),
    ):
        st = get_state()
        if st.admin_token and x_admin_token != st.admin_token:
            raise HTTPException(status_code=401, detail="Invalid admin token")
        email = (payload.get("user_email") or "").lower().strip()
        team_name = (payload.get("team") or "default").strip()
        org_name = (payload.get("org") or "default").strip()
        if not email:
            raise HTTPException(status_code=400, detail="Missing user_email")
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not user:
            user = User(email=email, full_name=payload.get("user_name", ""))
            db.add(user)
            db.flush()
        team = db.execute(select(Team).where(Team.name == team_name)).scalar_one_or_none()
        if not team:
            org = db.execute(
                select(Organization).where(Organization.name == org_name)).scalar_one_or_none()
            if not org:
                org = Organization(name=org_name)
                db.add(org)
                db.flush()
            team = Team(org_id=org.id, name=team_name)
            db.add(team)
            db.flush()
        existing = db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team.id, TeamMember.user_id == user.id)
        ).scalar_one_or_none()
        if not existing:
            db.add(TeamMember(team_id=team.id, user_id=user.id))
        db.commit()
        return JSONResponse({"status": "success", "schema_version": API_SCHEMA_VERSION})

    @app.post("/api/v1/admin/purge")
    async def purge_answers(
        db: Session = Depends(get_db),
        x_admin_token: str | None = Header(None),
    ):
        """Enforce the raw-answer retention policy NOW (also runnable from
        cron). Raw answers older than the retention window are nulled;
        scores/aggregates are kept forever. The service also purges
        automatically on startup."""
        st = get_state()
        if st.admin_token and x_admin_token != st.admin_token:
            raise HTTPException(status_code=401, detail="Invalid admin token")
        purged = purge_expired_answers(db, retention_days=st.retention_days)
        return JSONResponse({
            "status": "success", "schema_version": API_SCHEMA_VERSION,
            "purged": purged, "retention_days": st.retention_days,
        })

    @app.on_event("startup")
    async def startup_purge():
        # Retention is enforced, not just documented: every service start
        # purges raw answers older than the window (scores kept forever).
        st = get_state()
        if st.retention_days > 0:
            db = st.SessionLocal()
            try:
                purge_expired_answers(db, retention_days=st.retention_days)
            except Exception:
                pass
            finally:
                db.close()

    return app


def purge_expired_answers(db: Session, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Privacy retention: null raw answer text older than N days (keep scores).

    Per the amended DB architecture doc: raw answers are retained
    SERVICE_RETENTION_DAYS (default 90); aggregates are kept forever.
    """
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=retention_days)
    rows = db.execute(select(SessionQuestion)).scalars().all()
    purged = 0
    for q in rows:
        session = db.get(SessionRecord, q.session_row_id)
        if not session or not session.created_at:
            continue
        created = session.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=datetime.timezone.utc)
        if q.answer_retained and created < cutoff:
            q.answer_text = ""
            q.answer_retained = False
            purged += 1
    db.commit()
    return purged


app = create_app()
