"""Postgres ledger for one developer, their commit interviews, and the scores.

Tables are created from these models. Pass or fail on an answer is the
generated column assessments.passed (score >= 75).
"""

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Developer(Base):
    __tablename__ = "developers"
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    github_email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    github_username: Mapped[str | None] = mapped_column(CITEXT, unique=True, nullable=True)


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        Index(
            "repositories_name_when_no_remote",
            "name",
            unique=True,
            postgresql_where=text("remote_url IS NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    remote_url: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)


class CommitAttempt(Base):
    __tablename__ = "commit_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'skip', 'no_questions', 'expired', 'error')",
            name="commit_attempts_status_chk",
        ),
        Index("commit_attempts_developer_idx", "developer_id"),
    )
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    developer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("developers.public_id"), nullable=False)
    repository_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("repositories.id"), nullable=True)
    branch: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'in_progress'"))
    question_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    standards_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    trivial: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    answered_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    mean_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("attempt_id", "ordinal", name="questions_attempt_ordinal_uniq"),
        UniqueConstraint("attempt_id", "client_question_id", name="questions_attempt_client_id_uniq"),
        Index("questions_attempt_idx", "attempt_id", "ordinal"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("commit_attempts.public_id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    client_question_id: Mapped[str] = mapped_column(Text, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(Text, nullable=False)
    expected_concepts: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    model: Mapped[str | None] = mapped_column(Text, nullable=True)


class Answer(Base):
    __tablename__ = "answers"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, unique=True)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="assessments_score_chk"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    answer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("answers.id", ondelete="CASCADE"), nullable=False, unique=True)
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, Computed("score >= 75", persisted=True))
    evaluation: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    llm_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    missing_concepts: Mapped[list] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'"))
    covered_concepts: Mapped[list] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'"))
    judge_model: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeveloperPersona(Base):
    """Prose portrait only. Counts are computed from the ledger tables."""

    __tablename__ = "developer_personas"
    developer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("developers.public_id", ondelete="CASCADE"),
        primary_key=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))


def create_tables(engine) -> None:
    """Create any ledger tables that are not already in the database.

    citext and pgcrypto are column types, not tables. Postgres needs those
    extensions before create_all can build developers.github_email and the
    uuid defaults. Views are removed if a previous startup created them.
    """
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.execute(text("DROP VIEW IF EXISTS developer_persona_stats"))
        conn.execute(text("DROP VIEW IF EXISTS commit_judgements"))
        conn.execute(text("DROP TYPE IF EXISTS commit_outcome"))
    Base.metadata.create_all(engine)
