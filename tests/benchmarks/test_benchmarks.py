"""Benchmark collection and regression integration tests"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from buildscope.benchmarks.analysis import (
    BenchmarkAnalysisService,
    SlowdownAssessment,
)
from buildscope.benchmarks.reports import (
    BenchmarkReportError,
    parse_benchmark_report,
    prepare_benchmark_command,
)
from buildscope.benchmarks.service import BenchmarkService
from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository

type History = tuple[
    SessionFactory,
    UUID,
    UUID,
    Path,
]


@pytest.fixture
def history(
    tmp_path: Path,
) -> Iterator[History]:
    database_path = tmp_path / "bench.db"

    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)
    factory = create_session_factory(engine)

    with factory.begin() as session:
        repository = Repository(
            name="Benchmark Fixture",
            local_path=str(tmp_path),
        )

        session.add(repository)
        session.flush()

        repo_id = repository.id

    saved = PipelineService(factory).create_version(
        repo_id,
        "name: perf\nstages:\n  - name: measure\n    command: [python, --version]\n",
    )

    yield (
        factory,
        repo_id,
        saved.id,
        tmp_path,
    )

    engine.dispose()


def add_sample_run(
    history: History,
    sha: str,
    samples: list[float],
    *,
    day: int,
    dirty: bool = False,
    environment: str = "2" * 64,
) -> UUID:
    """Create one successful run and a persisted benchmark report."""
    factory, repo_id, version_id, root = history

    with factory.begin() as session:
        run = PipelineRunRecord(
            repository_id=repo_id,
            pipeline_definition_id=version_id,
            status=RunStatus.SUCCEEDED.value,
            queued_at=(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day)),
            git_commit_sha=sha,
            git_branch="main",
            git_is_dirty=dirty,
        )

        session.add(run)
        session.flush()

        stage = StageRunRecord(
            pipeline_run_id=run.id,
            stage_name="measure",
            position=0,
            command=["python", "--version"],
            status=RunStatus.SUCCEEDED.value,
        )

        session.add(stage)
        session.flush()

        attempt = StageAttemptRecord(
            stage_run_id=stage.id,
            attempt_number=1,
            status=RunStatus.SUCCEEDED.value,
        )

        session.add(attempt)
        session.flush()

        attempt_id = attempt.id

    report_path = root / f"{day}-{sha[:4]}.json"

    report_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metric": "process_wall_time",
                "unit": "seconds",
                "iterations": len(samples),
                "warmups": 1,
                "command_sha256": "1" * 64,
                "environment_key": environment,
                "samples": samples,
            }
        )
    )

    BenchmarkService(factory).ingest(
        attempt_id,
        report_path,
    )

    return attempt_id


def test_real_measurement_writes_json(
    tmp_path: Path,
) -> None:
    command, path = prepare_benchmark_command(
        (
            sys.executable,
            "-c",
            "pass",
        ),
        iterations=5,
        warmups=1,
        log_directory=tmp_path / "logs",
    )

    result = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    parsed = parse_benchmark_report(path)

    assert len(parsed.samples) == 5

    assert all(sample > 0 for sample in parsed.samples)


def test_failed_command_has_no_complete_report(
    tmp_path: Path,
) -> None:
    command, path = prepare_benchmark_command(
        (
            sys.executable,
            "-c",
            "raise SystemExit(7)",
        ),
        iterations=5,
        warmups=1,
        log_directory=tmp_path,
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 7

    with pytest.raises(BenchmarkReportError):
        parse_benchmark_report(path)


@pytest.mark.parametrize(
    "invalid",
    [
        0,
        -1,
        True,
        float("nan"),
        float("inf"),
    ],
)
def test_invalid_samples_rejected(
    tmp_path: Path,
    invalid: object,
) -> None:
    path = tmp_path / "invalid.json"

    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metric": "process_wall_time",
                "unit": "seconds",
                "iterations": 5,
                "warmups": 1,
                "command_sha256": "1" * 64,
                "environment_key": "2" * 64,
                "samples": [
                    invalid,
                    0.1,
                    0.1,
                    0.1,
                    0.1,
                ],
            }
        )
    )

    with pytest.raises(BenchmarkReportError):
        parse_benchmark_report(path)


def test_ingest_and_reject_duplicate(
    history: History,
) -> None:
    factory, _, _, root = history

    attempt_id = add_sample_run(
        history,
        "a" * 40,
        [0.1] * 5,
        day=0,
    )

    service = BenchmarkService(factory)

    details = service.get_report(attempt_id)

    assert details is not None
    assert details.parse_status == "PARSED"
    assert details.sample_count == 5

    with pytest.raises(ValueError):
        service.ingest(
            attempt_id,
            root / "0-aaaa.json",
        )


def test_detect_significant_slowdown(
    history: History,
) -> None:
    factory, repo_id, _, _ = history

    add_sample_run(
        history,
        "a" * 40,
        [0.1] * 5,
        day=0,
    )

    add_sample_run(
        history,
        "b" * 40,
        [0.16] * 5,
        day=1,
    )

    comparisons = BenchmarkAnalysisService(factory).compare(
        repo_id,
        "perf",
        1,
    )

    assert len(comparisons) == 1

    assert comparisons[0].assessment == SlowdownAssessment.SLOWDOWN

    assert comparisons[0].slowdown_percent == pytest.approx(60.0)


def test_no_clear_slowdown_when_change_is_small(
    history: History,
) -> None:
    factory, repo_id, _, _ = history

    add_sample_run(
        history,
        "a" * 40,
        [0.1] * 5,
        day=0,
    )

    add_sample_run(
        history,
        "b" * 40,
        [0.11] * 5,
        day=1,
    )

    comparisons = BenchmarkAnalysisService(factory).compare(
        repo_id,
        "perf",
        1,
    )

    assert comparisons[0].assessment == SlowdownAssessment.NO_CLEAR_SLOWDOWN


def test_dirty_run_is_excluded(
    history: History,
) -> None:
    factory, repo_id, _, _ = history

    add_sample_run(
        history,
        "a" * 40,
        [0.1] * 5,
        day=0,
    )

    add_sample_run(
        history,
        "b" * 40,
        [0.5] * 5,
        day=1,
        dirty=True,
    )

    assert (
        BenchmarkAnalysisService(factory).compare(
            repo_id,
            "perf",
            1,
        )
        == ()
    )


def test_different_environment_is_not_compared(
    history: History,
) -> None:
    factory, repo_id, _, _ = history

    add_sample_run(
        history,
        "a" * 40,
        [0.1] * 5,
        day=0,
    )

    add_sample_run(
        history,
        "b" * 40,
        [0.5] * 5,
        day=1,
        environment="3" * 64,
    )

    assert (
        BenchmarkAnalysisService(factory).compare(
            repo_id,
            "perf",
            1,
        )
        == ()
    )


def test_above_threshold_but_too_noisy(
    history: History,
) -> None:
    factory, repo_id, _, _ = history

    add_sample_run(
        history,
        "a" * 40,
        [0.08, 0.09, 0.10, 0.11, 0.12],
        day=0,
    )

    add_sample_run(
        history,
        "b" * 40,
        [0.11, 0.13, 0.14, 0.15, 0.17],
        day=1,
    )

    result = BenchmarkAnalysisService(factory).compare(
        repo_id,
        "perf",
        1,
    )

    assert len(result) == 1

    assert result[0].slowdown_percent == pytest.approx(40.0)

    assert result[0].assessment == SlowdownAssessment.NO_CLEAR_SLOWDOWN


def test_actual_pipeline_writes_per_attempt_report(
    tmp_path: Path,
) -> None:
    """Full runner -> executor -> report -> SQLite integration."""
    from buildscope.pipelines.runner import PipelineRunner

    project = tmp_path / "project"
    project.mkdir()

    database_path = tmp_path / "integration.db"
    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)
    factory = create_session_factory(engine)

    try:
        with factory.begin() as session:
            repo = Repository(
                name="Process Benchmark",
                local_path=str(project),
            )

            session.add(repo)
            session.flush()

            repo_id = repo.id

        content = yaml.safe_dump(
            {
                "name": "performance-integration",
                "stages": [
                    {
                        "name": "measure",
                        "command": [
                            sys.executable,
                            "-c",
                            "pass",
                        ],
                        "benchmark_iterations": 5,
                        "benchmark_warmups": 1,
                        "timeout_seconds": 30,
                    }
                ],
            }
        )

        saved = PipelineService(factory).create_version(
            repo_id,
            content,
        )

        result = PipelineRunner(factory).run_version(
            repo_id,
            saved.name,
            saved.version,
            approved=True,
            log_directory=tmp_path / "logs",
        )

        assert result.status == RunStatus.SUCCEEDED

        assert len(result.stages[0].attempts) == 1

        attempt_id = result.stages[0].attempts[0].id

        report = BenchmarkService(factory).get_report(attempt_id)

        assert report is not None
        assert report.parse_status == "PARSED"
        assert report.sample_count == 5

    finally:
        engine.dispose()
