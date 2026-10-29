"""Persistent pipeline and stage execution history"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
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


class PipelineRunRecord(Base):
    """One execution of a specific saved pipeline version."""

    __tablename__ = "pipeline_runs"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    repository_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repositories.id"),
        nullable=False,
    )

    pipeline_definition_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("pipeline_definitions.id"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="QUEUED",
    )

    queued_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )


class StageRunRecord(Base):
    """One stage execution within a pipeline run."""

    __tablename__ = "stage_runs"

    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id",
            "stage_name",
            name="uq_stage_runs_run_name",
        ),
        UniqueConstraint(
            "pipeline_run_id",
            "position",
            name="uq_stage_runs_run_position",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    pipeline_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "pipeline_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    stage_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    command: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="QUEUED",
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
