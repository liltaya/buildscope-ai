"""Durable local worker job queue model."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from buildscope.database.base import Base
from buildscope.database.types import UTCDateTime


class WorkerJobRecord(Base):
    __tablename__ = "worker_jobs"
    __table_args__ = (Index("ix_worker_jobs_status_queued", "status", "queued_at"),)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    repository_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("repositories.id"), nullable=False
    )
    pipeline_definition_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pipeline_definitions.id"), nullable=False
    )
    pipeline_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pipeline_runs.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=lambda: datetime.now(UTC)
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
