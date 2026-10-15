"""Central ledger schema (Pokerface P3 / Judas).

SQLAlchemy 2.0 models; SQLite for dev/tests (SERVICE_DATABASE_URL=
sqlite:///...), PostgreSQL in org deployments. JSON columns use the
portable sa.JSON type (JSONB on Postgres via the dialect where it matters
for querying is a later optimization; the schema is the contract).

Org hierarchy — the per-developer / per-team / per-repo answer:
  organizations 1..* teams 1..* team_members *..* users
  repositories.team_id  → every session row resolves to
  user_id (per developer), repo_id (per repo), repo.team_id (per team).
  No free-text attribution anywhere; every query funnels through these FKs.
"""

import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now():
    return func.now()


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now())


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_team_per_org"),)

    @property
    def full_name(self) -> str:
        return f"{self.name}"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    system_username: Mapped[str] = mapped_column(String(100), default="")
    first_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now())
    last_active_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now(), onupdate=_now())


class TeamMember(Base):
    __tablename__ = "team_members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_member_per_team"),)


class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(50), default="custom")
    remote_url: Mapped[str] = mapped_column(String(1000), default="")
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now())


class SessionRecord(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=2)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))
    repo_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"))
    branch: Mapped[str] = mapped_column(String(255), default="unknown")
    commit_hash: Mapped[str] = mapped_column(String(64), default="")
    head_commit: Mapped[str] = mapped_column(String(64), default="")
    diff_hash: Mapped[str] = mapped_column(String(64), default="")
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="FAILED")
    verification: Mapped[str] = mapped_column(String(50), default="llm-unverified")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    violations_count: Mapped[int] = mapped_column(Integer, default=0)
    machine_id: Mapped[str] = mapped_column(String(50), default="")
    # JSONB-shaped fidelity columns (never raw diff content)
    identity: Mapped[dict] = mapped_column(JSON, default=dict)
    changed_files: Mapped[list] = mapped_column(JSON, default=list)
    change_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    standards_report: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now())


class SessionQuestion(Base):
    __tablename__ = "session_questions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_row_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"))
    question_id: Mapped[str] = mapped_column(String(100), default="")
    question_text: Mapped[str] = mapped_column(String(2000), default="")
    answer_text: Mapped[str] = mapped_column(String(8000), default="")
    answer_retained: Mapped[bool] = mapped_column(default=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    category: Mapped[str] = mapped_column(String(100), default="general")
    evaluation_feedback: Mapped[str] = mapped_column(String(4000), default="")
    answered_in_attempt: Mapped[int] = mapped_column(Integer, default=1)
    llm_verified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now())
