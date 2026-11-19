"""Defense service.

Summarizes a staged change, generates questions, and scores one answer at a
time. Each request is scored on its own. A finished interview updates that
developer's persona and stores the questions and answers from this interview.
"""

import hmac as hmac_mod
import json
import os

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .defense import DefenseError, start_defense, submit_answer
from .persona import fold_persona
from .models import (
    Base,
    Organization,
    Repository,
    Team,
    TeamMember,
    User,
)

API_SCHEMA_VERSION = 1


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = hmac_mod.new(secret.encode("utf-8"), body, "sha256").hexdigest()
    return hmac_mod.compare_digest(expected, signature or "")


def _canonicalize_request(body: bytes) -> bytes:
    """The hook signs the canonical form of the payload; re-derive it."""
    return _canonical(json.loads(body.decode("utf-8")))


def _database_url(explicit: str | None) -> str:
    """Postgres URL for the ledger. Read from the argument, the environment, then .env."""
    url = (explicit or os.environ.get("SERVICE_DATABASE_URL") or "").strip()
    if not url:
        env_path = os.path.join(os.getcwd(), ".env")
        try:
            with open(env_path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() == "SERVICE_DATABASE_URL":
                        url = value.strip().strip('"').strip("'")
                        break
        except OSError:
            url = ""
    if not url.startswith("postgresql"):
        raise RuntimeError(
            "Set SERVICE_DATABASE_URL in .env or the server environment to a postgresql+psycopg:// URL."
        )
    return url


class ServiceState:
    """Holds engine/session factory + auth config; swappable in tests."""

    def __init__(self, database_url: str | None = None):
        url = _database_url(database_url)
        self.engine = create_engine(url)
        Base.metadata.create_all(self.engine)
        self._ensure_github_username()
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.team_token = os.environ.get("SERVICE_TEAM_TOKEN", "").strip()
        self.team_secret = os.environ.get("SERVICE_TEAM_SECRET", "").strip()
        self.admin_token = os.environ.get("SERVICE_ADMIN_TOKEN", "").strip()

    def _ensure_github_username(self) -> None:
        """Add users.github_username on databases created before that column."""
        insp = inspect(self.engine)
        if "users" not in insp.get_table_names():
            return
        columns = {column["name"] for column in insp.get_columns("users")}
        if "github_username" in columns:
            return
        with self.engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN github_username VARCHAR(255)"))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_github_username "
                "ON users (github_username)"
            ))


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


def _auth(team_token: str | None, x_team_token: str | None):
    if team_token and x_team_token != team_token:
        raise HTTPException(status_code=401, detail="Invalid team token")


def create_app(state: ServiceState | None = None) -> FastAPI:
    global STATE
    if state is not None:
        STATE = state

    app = FastAPI(title="Understanding Central Ledger", version="0.4.0")

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "schema_version": API_SCHEMA_VERSION}

    @app.on_event("startup")
    async def startup():
        get_state()

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

    @app.post("/api/v1/defense/start")
    async def defense_start(
        request: Request,
        x_team_token: str | None = Header(None),
        x_signature: str | None = Header(None),
    ):
        st = get_state()
        _auth(st.team_token, x_team_token)
        body = await request.body()
        if st.team_secret:
            canonical = _canonicalize_request(body)
            if not _verify_signature(st.team_secret, canonical, x_signature or ""):
                raise HTTPException(status_code=401, detail="Invalid payload signature")
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Malformed payload")
        try:
            result = start_defense(payload)
        except DefenseError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)
        return JSONResponse(result)

    @app.post("/api/v1/persona")
    async def record_persona(
        request: Request,
        db: Session = Depends(get_db),
        x_team_token: str | None = Header(None),
        x_signature: str | None = Header(None),
    ):
        """Store this interview on the developer's persona. Does not affect the next diff."""
        st = get_state()
        _auth(st.team_token, x_team_token)
        body = await request.body()
        if st.team_secret:
            canonical = _canonicalize_request(body)
            if not _verify_signature(st.team_secret, canonical, x_signature or ""):
                raise HTTPException(status_code=401, detail="Invalid payload signature")
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Malformed payload")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Malformed payload")
        email = str(payload.get("git_email") or "").strip().lower()
        name = str(payload.get("git_name") or "").strip()
        scored = payload.get("scored")
        if not email or not isinstance(scored, list):
            raise HTTPException(status_code=400, detail="git_email and scored are required")
        persona = fold_persona(
            db,
            email,
            name or email,
            passed=bool(payload.get("passed")),
            scored=scored,
        )
        db.commit()
        return JSONResponse({
            "status": "success",
            "schema_version": API_SCHEMA_VERSION,
            "git_email": persona.github_username,
        })

    @app.post("/api/v1/defense/{defense_id}/answer")
    async def defense_answer(
        defense_id: str,
        request: Request,
        db: Session = Depends(get_db),
        x_team_token: str | None = Header(None),
        x_signature: str | None = Header(None),
    ):
        st = get_state()
        _auth(st.team_token, x_team_token)
        body = await request.body()
        if st.team_secret:
            canonical = _canonicalize_request(body)
            if not _verify_signature(st.team_secret, canonical, x_signature or ""):
                raise HTTPException(status_code=401, detail="Invalid payload signature")
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Malformed payload")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Malformed payload")
        try:
            result = submit_answer(
                db,
                defense_id,
                str(payload.get("question_id") or ""),
                str(payload.get("answer") or ""),
            )
        except DefenseError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)
        return JSONResponse(result)

    return app


app = create_app()
