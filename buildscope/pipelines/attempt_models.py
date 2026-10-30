"""Persist one record for every command execution attempt"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from buildscope.database.base import Base
from buildscope.database.types import UTCDateTime


class StageAttemptRecord(Base):
    """One execution attempt belonging to a stage run."""

    __tablename__ = "stage_attempts"

    __table_args__ = (
        UniqueConstraint(
            "stage_run_id",
            "attempt_number",
            name="uq_stage_attempts_run_number",
        ),
        CheckConstraint(
            "attempt_number >= 1",
            name="ck_stage_attempt_number_positive",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    stage_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "stage_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    attempt_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )

    exit_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )

    stdout_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    stderr_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
