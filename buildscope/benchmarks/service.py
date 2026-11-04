"""Ingest benchmark reports without changing stage execution status"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from buildscope.benchmarks.models import (
    BenchmarkReportRecord,
    BenchmarkSampleRecord,
)
from buildscope.benchmarks.reports import (
    BenchmarkReportError,
    parse_benchmark_report,
)
from buildscope.database.session import SessionFactory
from buildscope.pipelines.attempt_models import StageAttemptRecord


@dataclass(frozen=True, slots=True)
class BenchmarkReportDetails:
    id: UUID
    stage_attempt_id: UUID
    parse_status: str
    report_path: str
    sample_count: int
    error_message: str | None


class BenchmarkService:
    """Store and retrieve benchmark observations."""

    def __init__(
        self,
        session_factory: SessionFactory,
    ) -> None:
        self._factory = session_factory

    def ingest(
        self,
        attempt_id: UUID,
        path: Path,
    ) -> BenchmarkReportDetails:
        """Persist one report for a stage attempt."""
        try:
            parsed = parse_benchmark_report(path)
            status = "PARSED"
            error = None

        except BenchmarkReportError as exc:
            parsed = None
            status = "MISSING" if not path.exists() else "INVALID"
            error = str(exc)[:2000]

        with self._factory.begin() as session:
            if (
                session.get(
                    StageAttemptRecord,
                    attempt_id,
                )
                is None
            ):
                raise LookupError(f"Stage attempt not found: {attempt_id}")

            previous = session.scalar(
                select(BenchmarkReportRecord.id).where(
                    BenchmarkReportRecord.stage_attempt_id == attempt_id
                )
            )

            if previous is not None:
                raise ValueError(f"Benchmark already ingested for attempt {attempt_id}")

            report = BenchmarkReportRecord(
                stage_attempt_id=attempt_id,
                report_path=str(path),
                parse_status=status,
                metric=(parsed.metric if parsed else None),
                unit=(parsed.unit if parsed else None),
                iterations=(parsed.iterations if parsed else None),
                warmups=(parsed.warmups if parsed else None),
                command_sha256=(parsed.command_sha256 if parsed else None),
                environment_key=(parsed.environment_key if parsed else None),
                error_message=error,
            )

            session.add(report)
            session.flush()

            if parsed is not None:
                session.add_all(
                    BenchmarkSampleRecord(
                        benchmark_report_id=report.id,
                        iteration=index,
                        duration_seconds=seconds,
                    )
                    for index, seconds in enumerate(
                        parsed.samples,
                        start=1,
                    )
                )

            details = BenchmarkReportDetails(
                id=report.id,
                stage_attempt_id=attempt_id,
                parse_status=status,
                report_path=str(path),
                sample_count=(len(parsed.samples) if parsed else 0),
                error_message=error,
            )

        return details

    def get_report(
        self,
        attempt_id: UUID,
    ) -> BenchmarkReportDetails | None:
        """Retrieve a persisted report and sample count."""
        with self._factory() as session:
            row = session.scalar(
                select(BenchmarkReportRecord).where(
                    BenchmarkReportRecord.stage_attempt_id == attempt_id
                )
            )

            if row is None:
                return None

            count = len(
                session.scalars(
                    select(BenchmarkSampleRecord.id).where(
                        BenchmarkSampleRecord.benchmark_report_id == row.id
                    )
                ).all()
            )

            return BenchmarkReportDetails(
                id=row.id,
                stage_attempt_id=row.stage_attempt_id,
                parse_status=row.parse_status,
                report_path=row.report_path,
                sample_count=count,
                error_message=row.error_message,
            )
