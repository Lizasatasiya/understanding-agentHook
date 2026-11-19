"""Per-developer persona and the questions they were asked.

Each developer has one persona row and one row per question they were asked.
Those rows are never read back when the next diff is assessed.
"""

import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now():
    return func.now()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    github_username: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    system_username: Mapped[str] = mapped_column(String(100), default="")
    first_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now())
    last_active_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now(), onupdate=_now())


class DeveloperPersona(Base):
    """One running portrait per git author, plus the exchanges that built it."""

    __tablename__ = "developer_personas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    score_sum: Mapped[float] = mapped_column(Float, default=0.0)
    mean_score: Mapped[float] = mapped_column(Float, default=0.0)
    pass_rate: Mapped[float] = mapped_column(Float, default=0.0)
    scores_by_category: Mapped[dict] = mapped_column(JSON, default=dict)
    missing_concepts: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(String(4000), default="")
    last_attempt_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class DeveloperExchange(Base):
    """One question and the answer that developer gave. Not reused on the next commit."""

    __tablename__ = "developer_exchanges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("developer_personas.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    question_text: Mapped[str] = mapped_column(String(2000), default="")
    answer_text: Mapped[str] = mapped_column(String(8000), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    category: Mapped[str] = mapped_column(String(100), default="Reasoning")
    missing_concepts: Mapped[list] = mapped_column(JSON, default=list)
    evaluation_feedback: Mapped[str] = mapped_column(String(4000), default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now())
