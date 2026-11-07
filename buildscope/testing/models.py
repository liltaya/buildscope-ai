"""Persistent test reports and individual test-case results"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from buildscope.database.base import Base
from buildscope.database.types import UTCDateTime


class TestReportRecord(Base):
    """One XML report belonging to one stage execution attempt."""

    __tablename__ = "test_reports"

    __table_args__ = (
        UniqueConstraint(
            "stage_attempt_id",
            name="uq_test_reports_attempt",
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

    kind: Mapped[str] = mapped_column(
        String(16),
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

    case_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
    )

    parsed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class TestCaseRecord(Base):
    """One test case extracted from a report."""

    __tablename__ = "test_cases"

    __table_args__ = (
        Index(
            "ix_test_cases_report_outcome",
            "test_report_id",
            "outcome",
        ),
        UniqueConstraint(
            "test_report_id",
            "position",
            name="uq_test_cases_position",
        ),
        CheckConstraint(
            "position >= 0",
            name="ck_test_cases_position",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_test_cases_duration",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    test_report_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "test_reports.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    suite_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    class_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    test_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    outcome: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    duration_seconds: Mapped[float | None] = mapped_column(
        Float,
    )

    message: Mapped[str | None] = mapped_column(
        Text,
    )
