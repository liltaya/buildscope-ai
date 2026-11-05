"""Persist test reports against individual stage execution attempts"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from buildscope.database.session import SessionFactory
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.testing.models import (
    TestCaseRecord,
    TestReportRecord,
)
from buildscope.testing.reports import (
    ReportKind,
    TestReportError,
    parse_test_report,
)


@dataclass(frozen=True, slots=True)
class TestReportSummary:
    id: UUID
    stage_attempt_id: UUID
    kind: str
    report_path: str
    parse_status: str
    case_count: int
    error_message: str | None
    parsed_at: datetime


@dataclass(frozen=True, slots=True)
class TestCaseSummary:
    position: int
    suite_name: str
    class_name: str
    test_name: str
    outcome: str
    duration_seconds: float | None
    message: str | None


@dataclass(frozen=True, slots=True)
class FailedTestDetail:
    run_id: UUID
    commit_sha: str | None
    stage_name: str
    attempt_number: int
    test_name: str
    class_name: str
    outcome: str
    duration_seconds: float | None
    message: str | None


class TestReportService:
    """Store and retrieve parsed test results."""

    def __init__(
        self,
        session_factory: SessionFactory,
    ) -> None:
        self._factory = session_factory

    def ingest(
        self,
        attempt_id: UUID,
        kind: ReportKind,
        path: Path,
    ) -> TestReportSummary:
        """Persist one report per attempt.

        Missing and invalid XML are recorded instead of discarded.
        """
        try:
            cases = parse_test_report(path)
            status = "PARSED"
            error = None

        except TestReportError as exc:
            cases = ()

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
                select(TestReportRecord.id).where(
                    TestReportRecord.stage_attempt_id == attempt_id
                )
            )

            if previous is not None:
                raise ValueError(
                    f"Test report already ingested for attempt {attempt_id}"
                )

            report = TestReportRecord(
                stage_attempt_id=attempt_id,
                kind=kind,
                report_path=str(path),
                parse_status=status,
                case_count=len(cases),
                error_message=error,
            )

            session.add(report)
            session.flush()

            for index, case in enumerate(cases):
                session.add(
                    TestCaseRecord(
                        test_report_id=report.id,
                        position=index,
                        suite_name=case.suite_name,
                        class_name=case.class_name,
                        test_name=case.test_name,
                        outcome=case.outcome,
                        duration_seconds=(case.duration_seconds),
                        message=case.message,
                    )
                )

            result = TestReportSummary(
                id=report.id,
                stage_attempt_id=attempt_id,
                kind=kind,
                report_path=str(path),
                parse_status=status,
                case_count=len(cases),
                error_message=error,
                parsed_at=report.parsed_at,
            )

        return result

    def get_report(
        self,
        attempt_id: UUID,
    ) -> TestReportSummary | None:
        """Retrieve the report metadata for an attempt."""
        with self._factory() as session:
            row = session.scalar(
                select(TestReportRecord).where(
                    TestReportRecord.stage_attempt_id == attempt_id
                )
            )

            if row is None:
                return None

            return TestReportSummary(
                id=row.id,
                stage_attempt_id=row.stage_attempt_id,
                kind=row.kind,
                report_path=row.report_path,
                parse_status=row.parse_status,
                case_count=row.case_count,
                error_message=row.error_message,
                parsed_at=row.parsed_at,
            )

    def list_cases(
        self,
        attempt_id: UUID,
    ) -> tuple[TestCaseSummary, ...]:
        """Read every test case belonging to one attempt."""
        with self._factory() as session:
            rows = session.scalars(
                select(TestCaseRecord)
                .join(
                    TestReportRecord,
                    TestCaseRecord.test_report_id == TestReportRecord.id,
                )
                .where(TestReportRecord.stage_attempt_id == attempt_id)
                .order_by(TestCaseRecord.position)
            ).all()

            return tuple(
                TestCaseSummary(
                    position=row.position,
                    suite_name=row.suite_name,
                    class_name=row.class_name,
                    test_name=row.test_name,
                    outcome=row.outcome,
                    duration_seconds=row.duration_seconds,
                    message=row.message,
                )
                for row in rows
            )

    def list_failed_for_run(
        self,
        run_id: UUID,
    ) -> tuple[FailedTestDetail, ...]:
        """Find failing cases with attempt and Git revision."""
        with self._factory() as session:
            rows = session.execute(
                select(
                    TestCaseRecord,
                    StageRunRecord,
                    StageAttemptRecord,
                    PipelineRunRecord,
                )
                .join(
                    TestReportRecord,
                    TestCaseRecord.test_report_id == TestReportRecord.id,
                )
                .join(
                    StageAttemptRecord,
                    TestReportRecord.stage_attempt_id == StageAttemptRecord.id,
                )
                .join(
                    StageRunRecord,
                    StageAttemptRecord.stage_run_id == StageRunRecord.id,
                )
                .join(
                    PipelineRunRecord,
                    StageRunRecord.pipeline_run_id == PipelineRunRecord.id,
                )
                .where(
                    PipelineRunRecord.id == run_id,
                    TestCaseRecord.outcome.in_(("FAILED", "ERROR")),
                )
                .order_by(
                    StageRunRecord.position,
                    StageAttemptRecord.attempt_number,
                    TestCaseRecord.position,
                )
            ).all()

            return tuple(
                FailedTestDetail(
                    run_id=run.id,
                    commit_sha=run.git_commit_sha,
                    stage_name=stage.stage_name,
                    attempt_number=attempt.attempt_number,
                    test_name=case.test_name,
                    class_name=case.class_name,
                    outcome=case.outcome,
                    duration_seconds=case.duration_seconds,
                    message=case.message,
                )
                for case, stage, attempt, run in rows
            )
