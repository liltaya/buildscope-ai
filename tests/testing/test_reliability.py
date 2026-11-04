"""Integration tests for test reliability and flakiness evidence"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository
from buildscope.testing.flaky_cli import main as flaky_main
from buildscope.testing.models import (
    TestCaseRecord as CaseRecord,
)
from buildscope.testing.models import (
    TestReportRecord as ReportRecord,
)
from buildscope.testing.reliability import (
    ReliabilityClassification as Category,
)
from buildscope.testing.reliability import (
    TestReliabilityService as ReliabilityService,
)


@dataclass(frozen=True)
class Context:
    factory: SessionFactory
    repository_id: UUID
    database_path: Path


@pytest.fixture
def context(tmp_path: Path) -> Iterator[Context]:
    database_path = tmp_path / "history.db"

    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)
    factory = create_session_factory(engine)

    with factory.begin() as session:
        repository = Repository(
            name="Reliability Demo",
            local_path=str(tmp_path),
        )

        session.add(repository)
        session.flush()

        repository_id = repository.id

    service = PipelineService(factory)

    service.create_version(
        repository_id,
        "name: quality\n"
        "stages:\n"
        "  - name: tests\n"
        "    command: [pytest, -q]\n",
    )

    service.create_version(
        repository_id,
        "name: quality\n"
        "stages:\n"
        "  - name: tests\n"
        "    command: [pytest, -q, tests]\n",
    )

    service.create_version(
        repository_id,
        "name: other\n"
        "stages:\n"
        "  - name: checks\n"
        "    command: [pytest, -q]\n",
    )

    yield Context(
        factory,
        repository_id,
        database_path,
    )

    engine.dispose()


def add_run(
    context: Context,
    outcomes: list[str],
    *,
    sha: str | None = "a" * 40,
    branch: str | None = "main",
    dirty: bool | None = False,
    version: int = 1,
    pipeline: str = "quality",
    stage_name: str = "tests",
    test_name: str = "test_x",
    hour: int = 0,
    parse_status: str = "PARSED",
) -> None:
    """Insert a terminal run with one case per attempt."""

    with context.factory.begin() as session:
        definition = session.scalar(
            select(PipelineDefinitionRecord).where(
                PipelineDefinitionRecord.repository_id
                == context.repository_id,
                PipelineDefinitionRecord.name == pipeline,
                PipelineDefinitionRecord.version == version,
            )
        )

        assert definition is not None

        succeeded = outcomes[-1] in {
            "PASSED",
            "SKIPPED",
        }

        status = (
            RunStatus.SUCCEEDED
            if succeeded
            else RunStatus.FAILED
        )

        run = PipelineRunRecord(
            repository_id=context.repository_id,
            pipeline_definition_id=definition.id,
            status=status.value,
            queued_at=(
                datetime(2026, 1, 1, tzinfo=UTC)
                + timedelta(hours=hour)
            ),
            git_commit_sha=sha,
            git_branch=branch,
            git_is_dirty=dirty,
        )

        session.add(run)
        session.flush()

        stage = StageRunRecord(
            pipeline_run_id=run.id,
            stage_name=stage_name,
            position=0,
            command=["pytest", "-q"],
            status=status.value,
        )

        session.add(stage)
        session.flush()

        for attempt_number, outcome in enumerate(
            outcomes,
            start=1,
        ):
            attempt = StageAttemptRecord(
                stage_run_id=stage.id,
                attempt_number=attempt_number,
                status=(
                    RunStatus.FAILED.value
                    if outcome in {"FAILED", "ERROR"}
                    else RunStatus.SUCCEEDED.value
                ),
            )

            session.add(attempt)
            session.flush()

            report = ReportRecord(
                stage_attempt_id=attempt.id,
                kind="pytest",
                report_path=(
                    f"/tmp/demo/{run.id}/{attempt_number}.xml"
                ),
                parse_status=parse_status,
                case_count=(
                    1 if parse_status == "PARSED" else 0
                ),
                parsed_at=datetime.now(UTC),
            )

            session.add(report)
            session.flush()

            if parse_status == "PARSED":
                session.add(
                    CaseRecord(
                        test_report_id=report.id,
                        position=0,
                        suite_name="pytest",
                        class_name="tests.test_sample",
                        test_name=test_name,
                        outcome=outcome,
                        duration_seconds=0.01,
                    )
                )


def analyze(
    context: Context,
    *,
    branch: str | None = None,
    max_runs: int = 200,
) -> tuple[Any, ...]:
    """Analyze reliability for quality pipeline version 1."""

    return ReliabilityService(context.factory).analyze(
        context.repository_id,
        "quality",
        1,
        branch=branch,
        max_runs=max_runs,
    )


def test_retry_fail_then_pass(context: Context) -> None:
    add_run(
        context,
        ["FAILED", "PASSED"],
    )

    result = analyze(context)[0]

    assert result.classification == Category.RETRY_FLAKY
    assert result.retry_recoveries == 1
    assert result.same_commit_mixed_groups == 0
    assert result.failure_rate == 0.5
    assert result.distinct_runs == 1


def test_conflicting_clean_runs_same_commit(
    context: Context,
) -> None:
    add_run(
        context,
        ["PASSED"],
        hour=0,
    )

    add_run(
        context,
        ["FAILED"],
        hour=1,
    )

    result = analyze(context)[0]

    assert (
        result.classification
        == Category.SAME_COMMIT_VARIABILITY
    )

    assert result.same_commit_mixed_groups == 1


def test_different_commits_not_automatically_flaky(
    context: Context,
) -> None:
    add_run(
        context,
        ["PASSED"],
        sha="a" * 40,
    )

    add_run(
        context,
        ["FAILED"],
        sha="b" * 40,
        hour=1,
    )

    result = analyze(context)[0]

    assert result.classification == Category.MIXED_UNATTRIBUTED
    assert result.clean_commits == 2


def test_dirty_runs_not_same_commit_evidence(
    context: Context,
) -> None:
    add_run(
        context,
        ["PASSED"],
        dirty=True,
    )

    add_run(
        context,
        ["FAILED"],
        dirty=True,
        hour=1,
    )

    result = analyze(context)[0]

    assert result.classification == Category.MIXED_UNATTRIBUTED
    assert result.same_commit_mixed_groups == 0


def test_passes_only(context: Context) -> None:
    add_run(context, ["PASSED"], hour=0)
    add_run(context, ["PASSED"], hour=1)

    assert (
        analyze(context)[0].classification
        == Category.STABLE_PASS
    )


def test_failures_only(context: Context) -> None:
    add_run(context, ["FAILED"], hour=0)
    add_run(context, ["ERROR"], hour=1)

    result = analyze(context)[0]

    assert result.classification == Category.CONSISTENT_FAILURE
    assert result.failing_observations == 2
    assert result.failure_rate == 1.0


def test_one_failure_is_insufficient(
    context: Context,
) -> None:
    add_run(context, ["FAILED"])

    assert (
        analyze(context)[0].classification
        == Category.INSUFFICIENT_DATA
    )


def test_skips_not_in_failure_rate(
    context: Context,
) -> None:
    add_run(context, ["SKIPPED"], hour=0)
    add_run(context, ["FAILED"], hour=1)

    result = analyze(context)[0]

    assert result.skipped == 1
    assert result.failure_rate == 1.0


def test_only_skips_have_no_failure_rate(
    context: Context,
) -> None:
    add_run(context, ["SKIPPED"], hour=0)
    add_run(context, ["SKIPPED"], hour=1)

    assert analyze(context)[0].failure_rate is None


def test_invalid_reports_excluded(
    context: Context,
) -> None:
    add_run(
        context,
        ["FAILED"],
        parse_status="INVALID",
    )

    assert analyze(context) == ()


def test_pipeline_version_isolation(
    context: Context,
) -> None:
    add_run(
        context,
        ["PASSED"],
        version=1,
    )

    add_run(
        context,
        ["FAILED"],
        version=2,
        hour=1,
    )

    assert analyze(context)[0].passed == 1

    version_2 = ReliabilityService(
        context.factory
    ).analyze(
        context.repository_id,
        "quality",
        2,
    )

    assert version_2[0].failed == 1


def test_pipeline_name_isolation(
    context: Context,
) -> None:
    add_run(
        context,
        ["PASSED"],
        pipeline="quality",
    )

    add_run(
        context,
        ["FAILED"],
        pipeline="other",
        hour=1,
    )

    assert analyze(context)[0].observations == 1


def test_branch_filter(context: Context) -> None:
    add_run(
        context,
        ["PASSED"],
        branch="main",
    )

    add_run(
        context,
        ["FAILED"],
        branch="feature",
        hour=1,
    )

    result = analyze(
        context,
        branch="main",
    )[0]

    assert result.passed == 1
    assert result.failed == 0


def test_max_runs_uses_recent_runs(
    context: Context,
) -> None:
    add_run(context, ["PASSED"], hour=0)
    add_run(context, ["FAILED"], hour=1)

    result = analyze(
        context,
        max_runs=1,
    )[0]

    assert result.failed == 1
    assert result.observations == 1


def test_stage_identity_is_distinct(
    context: Context,
) -> None:
    add_run(
        context,
        ["PASSED"],
        stage_name="python",
        hour=0,
    )

    add_run(
        context,
        ["FAILED"],
        stage_name="native",
        hour=1,
    )

    assert len(analyze(context)) == 2


def test_invalid_options(context: Context) -> None:
    service = ReliabilityService(context.factory)

    for version in (0, True):
        with pytest.raises(ValueError):
            service.analyze(
                context.repository_id,
                "quality",
                version,
            )

    for max_runs in (0, 1001, True):
        with pytest.raises(ValueError):
            service.analyze(
                context.repository_id,
                "quality",
                1,
                max_runs=max_runs,
            )


def test_cli_text_and_json(
    context: Context,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_run(
        context,
        ["FAILED", "PASSED"],
    )

    args = [
        "--database",
        str(context.database_path),
        "--repository",
        str(context.repository_id),
        "--pipeline",
        "quality",
        "--version",
        "1",
        "--suspected-only",
    ]

    assert flaky_main(args) == 0

    assert "RETRY_FLAKY" in (
        capsys.readouterr().out
    )

    assert flaky_main(args + ["--json"]) == 0

    assert '"retry_recoveries": 1' in (
        capsys.readouterr().out
    )
