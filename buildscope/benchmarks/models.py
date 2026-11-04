"""Persist immutable benchmark observations per stage attempt"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Float,
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


class BenchmarkReportRecord(Base):
    __tablename__ = "benchmark_reports"

    __table_args__ = (
        UniqueConstraint(
            "stage_attempt_id",
            name="uq_benchmark_reports_attempt",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    stage_attempt_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "stage_attempts.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    report_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    parse_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    metric: Mapped[str | None] = mapped_column(
        String(64),
    )

    unit: Mapped[str | None] = mapped_column(
        String(32),
    )

    iterations: Mapped[int | None] = mapped_column(
        Integer,
    )

    warmups: Mapped[int | None] = mapped_column(
        Integer,
    )

    command_sha256: Mapped[str | None] = mapped_column(
        String(64),
    )

    environment_key: Mapped[str | None] = mapped_column(
        String(64),
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
    )

    ingested_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class BenchmarkSampleRecord(Base):
    __tablename__ = "benchmark_samples"

    __table_args__ = (
        UniqueConstraint(
            "benchmark_report_id",
            "iteration",
            name="uq_benchmark_sample_iteration",
        ),
        CheckConstraint(
            "iteration >= 1",
            name="ck_benchmark_iteration_positive",
        ),
        CheckConstraint(
            "duration_seconds > 0",
            name="ck_benchmark_duration_positive",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    benchmark_report_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "benchmark_reports.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    iteration: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    duration_seconds: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
