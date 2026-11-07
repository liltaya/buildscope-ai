"""Read-only reliability analysis for individual test cases

Reports observations, not probabilities or proven root causes.
Retry attempts are correlated by design.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select

from buildscope.database.session import SessionFactory
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.pipelines.state import RunStatus
from buildscope.testing.models import (
    TestCaseRecord,
    TestReportRecord,
)

MAX_OBSERVATIONS = 100_000

_OUTCOMES = frozenset({"PASSED", "FAILED", "ERROR", "SKIPPED"})

_FAILURES = frozenset({"FAILED", "ERROR"})


class ReliabilityDataError(ValueError):
    """Historical data is inconsistent or exceeds analysis bounds."""


class ReliabilityClassification(StrEnum):
    RETRY_FLAKY = "RETRY_FLAKY"
    SAME_COMMIT_VARIABILITY = "SAME_COMMIT_VARIABILITY"
    MIXED_UNATTRIBUTED = "MIXED_UNATTRIBUTED"
    CONSISTENT_FAILURE = "CONSISTENT_FAILURE"
    STABLE_PASS = "STABLE_PASS"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True, order=True)
class TestIdentity:
    """Identify a test within a stage and pipeline version."""

    stage_name: str
    suite_name: str
    class_name: str
    test_name: str

    @property
    def display_name(self) -> str:
        name = ".".join(
            value
            for value in (
                self.class_name,
                self.test_name,
            )
            if value
        )

        return f"{self.stage_name} / {self.suite_name} / {name}"


@dataclass(frozen=True, slots=True)
class TestReliabilitySummary:
    identity: TestIdentity
    classification: ReliabilityClassification
    observations: int
    distinct_runs: int
    clean_commits: int
    passed: int
    failed: int
    errors: int
    skipped: int
    failure_rate: float | None
    retry_recoveries: int
    same_commit_mixed_groups: int
    latest_outcome: str

    @property
    def failing_observations(self) -> int:
        return self.failed + self.errors


@dataclass(frozen=True, slots=True)
class _Observation:
    """Internal case-level observation."""

    identity: TestIdentity
    run_id: UUID
    stage_id: UUID
    attempt_number: int
    outcome: str
    commit_sha: str | None
    branch: str | None
    is_clean: bool


def _retry_recovery_count(
    items: list[_Observation],
) -> int:
    """Count stage runs where failure precedes a passing retry."""
    by_stage: dict[
        tuple[UUID, UUID],
        list[_Observation],
    ] = defaultdict(list)

    for item in items:
        by_stage[(item.run_id, item.stage_id)].append(item)

    count = 0

    for observations in by_stage.values():
        failures = [
            row.attempt_number for row in observations if row.outcome in _FAILURES
        ]

        passes = [row.attempt_number for row in observations if row.outcome == "PASSED"]

        if failures and passes and min(failures) < max(passes):
            count += 1

    return count


def _same_commit_mixed_groups(
    items: list[_Observation],
) -> int:
    """Count clean revisions with conflicting results across runs.

    Different pipeline run IDs are required.
    Retries inside one run alone do not qualify here.
    """
    groups: dict[
        tuple[str, str],
        dict[UUID, set[str]],
    ] = defaultdict(lambda: defaultdict(set))

    for item in items:
        if (
            not item.is_clean
            or item.branch is None
            or item.commit_sha is None
            or item.outcome == "SKIPPED"
        ):
            continue

        groups[(item.branch, item.commit_sha)][item.run_id].add(item.outcome)

    count = 0

    for runs in groups.values():
        passing_runs = {run_id for run_id, values in runs.items() if "PASSED" in values}

        failing_runs = {run_id for run_id, values in runs.items() if values & _FAILURES}

        if (
            passing_runs
            and failing_runs
            and any(
                passing_id != failing_id
                for passing_id in passing_runs
                for failing_id in failing_runs
            )
        ):
            count += 1

    return count


def _classification(
    passed: int,
    failures: int,
    retry_recoveries: int,
    same_commit_mixed: int,
) -> ReliabilityClassification:
    if retry_recoveries:
        return ReliabilityClassification.RETRY_FLAKY

    if same_commit_mixed:
        return ReliabilityClassification.SAME_COMMIT_VARIABILITY

    if passed and failures:
        return ReliabilityClassification.MIXED_UNATTRIBUTED

    if failures >= 2 and not passed:
        return ReliabilityClassification.CONSISTENT_FAILURE

    if passed >= 2 and not failures:
        return ReliabilityClassification.STABLE_PASS

    return ReliabilityClassification.INSUFFICIENT_DATA


class TestReliabilityService:
    """Analyze parsed cases for one immutable pipeline version."""

    # The "Test" prefix describes the domain; it is not a pytest test class.
    __test__ = False

    def __init__(
        self,
        session_factory: SessionFactory,
    ) -> None:
        self._factory = session_factory

    def analyze(
        self,
        repository_id: UUID,
        pipeline_name: str,
        version: int,
        *,
        branch: str | None = None,
        max_runs: int = 200,
    ) -> tuple[TestReliabilitySummary, ...]:
        """Return test summaries ranked by instability evidence.

        Only terminal pipeline runs and terminal attempts count.
        Only PARSED reports contribute cases.
        SKIPPED is excluded from the failure-rate denominator.
        """
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")

        if (
            isinstance(max_runs, bool)
            or not isinstance(max_runs, int)
            or not 1 <= max_runs <= 1000
        ):
            raise ValueError("max_runs must be between 1 and 1000")

        if not pipeline_name.strip():
            raise ValueError("pipeline_name cannot be blank")

        terminal = (
            RunStatus.SUCCEEDED.value,
            RunStatus.FAILED.value,
            RunStatus.TIMED_OUT.value,
            RunStatus.CANCELLED.value,
        )

        with self._factory() as session:
            runs_query = (
                select(PipelineRunRecord.id)
                .join(
                    PipelineDefinitionRecord,
                    PipelineRunRecord.pipeline_definition_id
                    == PipelineDefinitionRecord.id,
                )
                .where(
                    PipelineRunRecord.repository_id == repository_id,
                    PipelineDefinitionRecord.name == pipeline_name,
                    PipelineDefinitionRecord.version == version,
                    PipelineRunRecord.status.in_(terminal),
                )
            )

            if branch is not None:
                runs_query = runs_query.where(PipelineRunRecord.git_branch == branch)

            run_ids = list(
                session.scalars(
                    runs_query.order_by(
                        PipelineRunRecord.queued_at.desc(),
                        PipelineRunRecord.id.desc(),
                    ).limit(max_runs)
                ).all()
            )

            if not run_ids:
                return ()

            rows = session.execute(
                select(
                    TestCaseRecord,
                    StageAttemptRecord,
                    StageRunRecord,
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
                    PipelineRunRecord.id.in_(run_ids),
                    TestReportRecord.parse_status == "PARSED",
                    StageAttemptRecord.status.in_(terminal),
                )
                .order_by(
                    PipelineRunRecord.queued_at,
                    PipelineRunRecord.id,
                    StageRunRecord.position,
                    StageAttemptRecord.attempt_number,
                    TestCaseRecord.position,
                )
                .limit(MAX_OBSERVATIONS + 1)
            ).all()

            if len(rows) > MAX_OBSERVATIONS:
                raise ReliabilityDataError(
                    f"Analysis exceeds {MAX_OBSERVATIONS} test-case observations; reduce max_runs"
                )

            observations: dict[
                TestIdentity,
                list[_Observation],
            ] = defaultdict(list)

            for case, attempt, stage, run in rows:
                if case.outcome not in _OUTCOMES:
                    raise ReliabilityDataError(
                        f"Unknown test outcome: {case.outcome!r}"
                    )

                identity = TestIdentity(
                    stage_name=stage.stage_name,
                    suite_name=case.suite_name,
                    class_name=case.class_name,
                    test_name=case.test_name,
                )

                observations[identity].append(
                    _Observation(
                        identity=identity,
                        run_id=run.id,
                        stage_id=stage.id,
                        attempt_number=attempt.attempt_number,
                        outcome=case.outcome,
                        commit_sha=run.git_commit_sha,
                        branch=run.git_branch,
                        is_clean=run.git_is_dirty is False,
                    )
                )

        summaries: list[TestReliabilitySummary] = []

        for identity, items in observations.items():
            passed = sum(item.outcome == "PASSED" for item in items)

            failed = sum(item.outcome == "FAILED" for item in items)

            errors = sum(item.outcome == "ERROR" for item in items)

            skipped = sum(item.outcome == "SKIPPED" for item in items)

            failures = failed + errors
            executed = passed + failures

            retry_recoveries = _retry_recovery_count(items)

            mixed_groups = _same_commit_mixed_groups(items)

            clean_commits = {
                (item.branch, item.commit_sha)
                for item in items
                if (
                    item.is_clean
                    and item.branch is not None
                    and item.commit_sha is not None
                )
            }

            summaries.append(
                TestReliabilitySummary(
                    identity=identity,
                    classification=_classification(
                        passed,
                        failures,
                        retry_recoveries,
                        mixed_groups,
                    ),
                    observations=len(items),
                    distinct_runs=len({item.run_id for item in items}),
                    clean_commits=len(clean_commits),
                    passed=passed,
                    failed=failed,
                    errors=errors,
                    skipped=skipped,
                    failure_rate=(failures / executed if executed else None),
                    retry_recoveries=retry_recoveries,
                    same_commit_mixed_groups=mixed_groups,
                    latest_outcome=items[-1].outcome,
                )
            )

        ordering = {
            ReliabilityClassification.RETRY_FLAKY: 0,
            ReliabilityClassification.SAME_COMMIT_VARIABILITY: 1,
            ReliabilityClassification.MIXED_UNATTRIBUTED: 2,
            ReliabilityClassification.CONSISTENT_FAILURE: 3,
            ReliabilityClassification.INSUFFICIENT_DATA: 4,
            ReliabilityClassification.STABLE_PASS: 5,
        }

        return tuple(
            sorted(
                summaries,
                key=lambda item: (
                    ordering[item.classification],
                    -(item.failure_rate if item.failure_rate is not None else -1),
                    item.identity,
                ),
            )
        )
