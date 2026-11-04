"""Failure datasets and leakage protection"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from buildscope.benchmarks.models import (
    BenchmarkReportRecord as BenchReport,
)
from buildscope.benchmarks.models import (
    BenchmarkSampleRecord as BenchSample,
)
from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.datasets.cli import main as dataset_main
from buildscope.datasets.failures import (
    FailureDatasetError,
    build_failure_examples,
    export_failure_dataset,
)
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.pipelines.service import PipelineService
from buildscope.repositories.models import Repository
from buildscope.testing.models import (
    TestCaseRecord as CaseRecord,
)
from buildscope.testing.models import (
    TestReportRecord as ReportRecord,
)

BASE = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Context:
    factory: SessionFactory
    repository_id: UUID
    definition_id: UUID
    directory: Path


@pytest.fixture
def context(
    tmp_path: Path,
) -> Iterator[Context]:
    database_path = tmp_path / "history.db"

    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)
    factory = create_session_factory(engine)

    with factory.begin() as session:
        repo = Repository(
            name="Dataset Fixture",
            local_path=str(tmp_path),
        )

        session.add(repo)
        session.flush()

        repository_id = repo.id

    saved = PipelineService(factory).create_version(
        repository_id,
        "name: quality\nstages:\n  - name: tests\n    command: [pytest, -q]\n",
    )

    yield Context(
        factory,
        repository_id,
        saved.id,
        tmp_path,
    )

    engine.dispose()


def add_run(
    context: Context,
    *,
    day: float,
    status: str,
    finished_day: float | None,
    final_stage_status: str | None = None,
    later_retry: str | None = None,
    run_status: str | None = None,
    command: tuple[str, ...] = ("pytest", "-q"),
    report_outcomes: tuple[str, ...] = (),
    benchmark_seconds: tuple[float, ...] = (),
) -> UUID:
    """Construct a completed run and optionally its result reports."""
    with context.factory.begin() as session:
        run = PipelineRunRecord(
            repository_id=context.repository_id,
            pipeline_definition_id=context.definition_id,
            status=(run_status or final_stage_status or later_retry or status),
            queued_at=BASE + timedelta(days=day),
            finished_at=(BASE + timedelta(days=finished_day) if finished_day is not None else None),
            git_commit_sha="a" * 40,
            git_branch="main",
            git_is_dirty=False,
        )

        session.add(run)
        session.flush()

        stage = StageRunRecord(
            pipeline_run_id=run.id,
            stage_name="tests",
            position=0,
            command=list(command),
            status=(final_stage_status or later_retry or status),
        )

        session.add(stage)
        session.flush()

        first = StageAttemptRecord(
            stage_run_id=stage.id,
            attempt_number=1,
            status=status,
        )

        session.add(first)
        session.flush()

        if later_retry is not None:
            session.add(
                StageAttemptRecord(
                    stage_run_id=stage.id,
                    attempt_number=2,
                    status=later_retry,
                )
            )

        if report_outcomes:
            report = ReportRecord(
                stage_attempt_id=first.id,
                kind="pytest",
                report_path="/tmp/example.xml",
                parse_status="PARSED",
                case_count=len(report_outcomes),
                parsed_at=datetime.now(UTC),
            )

            session.add(report)
            session.flush()

            for index, outcome in enumerate(report_outcomes):
                session.add(
                    CaseRecord(
                        test_report_id=report.id,
                        position=index,
                        suite_name="pytest",
                        class_name="tests.test_example",
                        test_name=f"case_{index}",
                        outcome=outcome,
                    )
                )

        if benchmark_seconds:
            benchmark = BenchReport(
                stage_attempt_id=first.id,
                report_path="/tmp/benchmark.json",
                parse_status="PARSED",
                ingested_at=datetime.now(UTC),
            )

            session.add(benchmark)
            session.flush()

            for index, seconds in enumerate(
                benchmark_seconds,
                start=1,
            ):
                session.add(
                    BenchSample(
                        benchmark_report_id=benchmark.id,
                        iteration=index,
                        duration_seconds=seconds,
                    )
                )

        return run.id


def test_first_attempt_label_and_previous_retry(
    context: Context,
) -> None:
    add_run(
        context,
        day=0,
        status="FAILED",
        later_retry="SUCCEEDED",
        finished_day=1,
    )

    add_run(
        context,
        day=2,
        status="SUCCEEDED",
        finished_day=3,
    )

    examples = build_failure_examples(context.factory)

    assert [example.label for example in examples] == [1, 0]

    assert examples[0].features["prior_attempts"] == 0
    assert examples[1].features["prior_attempts"] == 1

    assert examples[1].features["prior_failure_rate"] == 1.0

    assert examples[1].features["prior_retry_recoveries"] == 1


def test_in_flight_result_excluded(
    context: Context,
) -> None:
    add_run(
        context,
        day=0,
        status="FAILED",
        finished_day=5,
    )

    add_run(
        context,
        day=2,
        status="SUCCEEDED",
        finished_day=3,
    )

    examples = build_failure_examples(context.factory)

    # The first run had not finished when the second was queued.
    assert examples[1].features["prior_attempts"] == 0


def test_previous_test_and_benchmark_features(
    context: Context,
) -> None:
    add_run(
        context,
        day=0,
        status="FAILED",
        finished_day=1,
        report_outcomes=(
            "FAILED",
            "PASSED",
            "SKIPPED",
        ),
        benchmark_seconds=(
            0.1,
            0.2,
            0.3,
        ),
    )

    add_run(
        context,
        day=2,
        status="SUCCEEDED",
        finished_day=3,
    )

    features = build_failure_examples(context.factory)[1].features

    assert features["prior_test_executions"] == 2
    assert features["prior_test_failure_rate"] == 0.5
    assert features["prior_benchmark_samples"] == 3

    assert features["prior_benchmark_last_median_seconds"] == pytest.approx(0.2)


def test_timeout_positive_and_cancelled_excluded(
    context: Context,
) -> None:
    add_run(
        context,
        day=0,
        status="TIMED_OUT",
        finished_day=1,
    )

    add_run(
        context,
        day=2,
        status="CANCELLED",
        finished_day=3,
    )

    examples = build_failure_examples(context.factory)

    assert len(examples) == 1
    assert examples[0].label == 1


def test_command_arguments_are_not_exported(
    context: Context,
) -> None:
    add_run(
        context,
        day=0,
        status="SUCCEEDED",
        finished_day=1,
        command=(
            "python",
            "--password",
            "secret-password",
        ),
    )

    features = build_failure_examples(context.factory)[0].features

    assert "secret-password" not in json.dumps(features)

    assert features["argument_count"] == 3


def test_chronological_splits_and_provenance(
    context: Context,
) -> None:
    for day in range(7):
        add_run(
            context,
            day=day,
            finished_day=day + 0.5,
            status=("FAILED" if day % 2 else "SUCCEEDED"),
        )

    destination = context.directory / "dataset-v1"

    result = export_failure_dataset(
        context.factory,
        destination,
    )

    assert result.examples == 7
    assert sum(result.split_counts.values()) == 7

    with (destination / "provenance.csv").open(newline="") as handle:
        provenance = list(csv.DictReader(handle))

    assert {row["split"] for row in provenance} == {
        "train",
        "validation",
        "test",
    }

    for name in (
        "train.csv",
        "validation.csv",
        "test.csv",
    ):
        content = (destination / name).read_text()

        assert "pipeline_run_id" not in content
        assert "git_commit_sha" not in content

    manifest = json.loads((destination / "manifest.json").read_text())

    assert manifest["schema_version"] == "stage-first-attempt-v1"

    with pytest.raises(FileExistsError):
        export_failure_dataset(
            context.factory,
            destination,
        )


def test_small_history_has_no_fake_evaluation(
    context: Context,
) -> None:
    add_run(
        context,
        day=0,
        status="SUCCEEDED",
        finished_day=1,
    )

    result = export_failure_dataset(
        context.factory,
        context.directory / "small",
    )

    assert result.split_counts == {
        "train": 1,
        "validation": 0,
        "test": 0,
    }

    assert result.warnings


def test_empty_history_rejected(
    context: Context,
) -> None:
    with pytest.raises(FailureDatasetError):
        export_failure_dataset(
            context.factory,
            context.directory / "empty",
        )


def test_bounded_export(
    context: Context,
) -> None:
    add_run(
        context,
        day=0,
        status="SUCCEEDED",
        finished_day=1,
    )

    add_run(
        context,
        day=2,
        status="FAILED",
        finished_day=3,
    )

    with pytest.raises(FailureDatasetError):
        build_failure_examples(
            context.factory,
            max_rows=1,
        )


def test_invalid_max_rows(
    context: Context,
) -> None:
    with pytest.raises(ValueError):
        build_failure_examples(
            context.factory,
            max_rows=0,
        )


def test_cli_exports(
    context: Context,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_run(
        context,
        day=0,
        status="SUCCEEDED",
        finished_day=1,
    )

    destination = context.directory / "from-cli"

    assert (
        dataset_main(
            [
                "--database",
                str(context.directory / "history.db"),
                "--output",
                str(destination),
            ]
        )
        == 0
    )

    assert (destination / "train.csv").exists()

    assert "Examples: 1" in (capsys.readouterr().out)


def test_command_fingerprint_partitions_history(
    context: Context,
) -> None:
    add_run(
        context,
        day=0,
        status="FAILED",
        finished_day=1,
        command=(
            "pytest",
            "-q",
            "tests/api",
        ),
    )

    add_run(
        context,
        day=2,
        status="SUCCEEDED",
        finished_day=3,
        command=(
            "pytest",
            "-q",
            "tests/repositories",
        ),
    )

    examples = build_failure_examples(context.factory)

    assert examples[1].features["prior_attempts"] == 0

    assert examples[0].features["command_sha256"] != examples[1].features["command_sha256"]


def test_history_only_after_completion_not_same_run(
    context: Context,
) -> None:
    with context.factory.begin() as session:
        run = PipelineRunRecord(
            repository_id=context.repository_id,
            pipeline_definition_id=context.definition_id,
            status="FAILED",
            queued_at=BASE,
            finished_at=(BASE + timedelta(hours=1)),
            git_commit_sha=None,
            git_branch=None,
            git_is_dirty=None,
        )

        session.add(run)
        session.flush()

        for position, status in enumerate(
            (
                "FAILED",
                "SUCCEEDED",
            )
        ):
            stage = StageRunRecord(
                pipeline_run_id=run.id,
                stage_name=f"tests-{position}",
                position=position,
                command=["pytest", "-q"],
                status=status,
            )

            session.add(stage)
            session.flush()

            session.add(
                StageAttemptRecord(
                    stage_run_id=stage.id,
                    attempt_number=1,
                    status=status,
                )
            )

    examples = build_failure_examples(context.factory)

    assert len(examples) == 2

    assert all(example.features["prior_attempts"] == 0 for example in examples)

    assert examples[0].features["git_worktree_state"] == "unknown"
