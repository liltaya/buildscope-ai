"""Build a realistic, reproducible pipeline history in a fresh database.

The history is recorded, not executed: commands in the demo pipeline are
never run. Git commits, test reports and benchmark reports are real files
that go through the same ingestion code as live runs, so every dashboard
panel is fed by production code paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import yaml

from buildscope.benchmarks.service import BenchmarkService
from buildscope.database import create_session_factory, create_sqlite_engine
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.datasets.failures import export_failure_dataset
from buildscope.ml.training import train_failure_classifier
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.run_models import PipelineRunRecord, StageRunRecord
from buildscope.pipelines.schema import PipelineDefinition
from buildscope.pipelines.service import PipelineService
from buildscope.repositories.models import Repository
from buildscope.testing.service import TestReportService

SEED = 20261107

START = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)

REPOSITORY_NAME = "payments-ledger"

COMMITS = (
    "add ledger entry model",
    "validate currency codes on import",
    "add CSV statement parser",
    "cache exchange rates per batch",
    "add retry backoff to settlement client",
    "split ledger totals by account",
    "document statement import format",
    "tighten decimal parsing for amounts",
    "add settlement reconciliation job",
    "bump httpx to 0.28",
    "log rejected statement rows",
    "parse amounts with a Decimal context per row",
    "add idempotency keys to settlement requests",
    "index ledger entries by posting date",
    "refactor account lookup",
    "add multi-currency totals report",
    "move rounding into ledger.money",
    "switch ledger rounding to banker's rounding",
    "add statement import metrics",
    "rename settlement status fields",
    "add pagination to ledger export",
    "handle empty statement files",
)

# Fixed so documentation and the seed's own hints can show a working request.
DEMO_REPOSITORY_ID = UUID("86d53822-f963-5af9-9823-f028e263a8fa")

REGRESSION_COMMIT = 17

# lint breaks on commit 7 and is fixed on commit 10: a closed regression
# window on the timeline, and the only persistent failure inside the model's
# training window. Without one, training data holds nothing but one-off
# flakes, and no model can learn that a stage failing in its last few runs
# tends to fail again.
FIXED_REGRESSION_COMMITS = range(7, 10)

SLOWDOWN_COMMIT = 11

PIPELINE_V1 = {
    "name": "ci",
    "stages": [
        {"name": "lint", "command": ["ruff", "check", "."]},
        {
            "name": "unit-tests",
            "command": ["pytest", "-q", "tests/unit"],
            "depends_on": ["lint"],
            "test_report": "pytest",
        },
    ],
}

PIPELINE_V2 = {
    "name": "ci",
    "stages": [
        {"name": "lint", "command": ["ruff", "check", "."]},
        {"name": "build", "command": ["python", "-m", "build", "--wheel"]},
        {
            "name": "unit-tests",
            "command": ["pytest", "-q", "tests/unit"],
            "depends_on": ["build"],
            "retry_count": 1,
            "test_report": "pytest",
        },
        {
            "name": "integration-tests",
            "command": ["pytest", "-q", "tests/integration"],
            "depends_on": ["build"],
            "timeout_seconds": 900,
            "test_report": "pytest",
        },
        {
            "name": "bench-statement-parser",
            "command": ["python", "benchmarks/parse_statement.py"],
            "depends_on": ["build"],
            "benchmark_iterations": 9,
            "benchmark_warmups": 1,
        },
        {
            "name": "package",
            "command": ["python", "-m", "build", "--sdist"],
            "depends_on": ["lint", "unit-tests", "integration-tests"],
        },
    ],
}

UNIT_TESTS = (
    "test_currency_code_validation",
    "test_statement_header_parsing",
    "test_amount_parsing_rejects_exponents",
    "test_exchange_rate_cache_expiry",
    "test_account_lookup_by_iban",
    "test_settlement_retry_backoff_jitter",
    "test_idempotency_key_reuse",
    "test_ledger_export_pagination",
)

FLAKY_TEST = "test_settlement_retry_backoff_jitter"

INTEGRATION_TESTS = (
    "test_import_statement_end_to_end",
    "test_reconciliation_matches_settlements",
    "test_ledger_totals_round_half_up",
    "test_multi_currency_totals",
)

REGRESSED_TEST = "test_ledger_totals_round_half_up"

ENVIRONMENT_KEY = hashlib.sha256(b"demo-runner-linux-x86_64").hexdigest()


class DemoSeedError(RuntimeError):
    """The demo history could not be created."""


@dataclass(frozen=True, slots=True)
class DemoPaths:
    database: Path
    workspace: Path
    artifacts: Path
    model: Path

    @classmethod
    def beside(cls, database: Path) -> DemoPaths:
        database = database.expanduser().resolve()
        stem = database.stem

        return cls(
            database=database,
            workspace=database.parent / f"{stem}-workspace",
            artifacts=database.parent / f"{stem}-artifacts",
            model=database.parent / f"{stem}-model",
        )

    def existing(self) -> list[Path]:
        return [
            path
            for path in (self.database, self.workspace, self.artifacts, self.model)
            if path.exists()
        ]


@dataclass(frozen=True, slots=True)
class Commit:
    sha: str
    subject: str
    committed_at: datetime


@dataclass(slots=True)
class SeedSummary:
    paths: DemoPaths
    repository_id: UUID
    runs: int = 0
    test_reports: int = 0
    benchmark_reports: int = 0
    model_trained: bool = False
    notes: list[str] = field(default_factory=list)


def _git(workspace: Path, *args: str, when: datetime | None = None) -> str:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workspace),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Demo Author",
        "GIT_AUTHOR_EMAIL": "demo@example.com",
        "GIT_COMMITTER_NAME": "Demo Author",
        "GIT_COMMITTER_EMAIL": "demo@example.com",
    }

    if when is not None:
        environment["GIT_AUTHOR_DATE"] = when.isoformat()
        environment["GIT_COMMITTER_DATE"] = when.isoformat()

    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _create_workspace(workspace: Path) -> list[Commit]:
    workspace.mkdir(parents=True)
    _git(workspace, "init", "-q", "-b", "main")

    commits: list[Commit] = []

    for index, subject in enumerate(COMMITS):
        when = START + timedelta(hours=7 * index)
        with (workspace / "CHANGES.txt").open("a") as changes:
            changes.write(f"{subject}\n")

        _git(workspace, "add", "CHANGES.txt")
        _git(workspace, "commit", "-q", "-m", subject, when=when)
        commits.append(Commit(_git(workspace, "rev-parse", "HEAD"), subject, when))

    return commits


def _junit(
    path: Path, suite: str, outcomes: dict[str, str], rng: random.Random
) -> None:
    cases = []

    for name, outcome in outcomes.items():
        seconds = f"{rng.uniform(0.004, 0.4):.3f}"
        body = ""

        if outcome == "FAILED":
            body = (
                "<failure message=\"AssertionError: expected Decimal('10.25'), "
                "got Decimal('10.24')\">assert total == expected</failure>"
            )

        cases.append(
            f'<testcase classname="tests.{suite}" name="{name}" time="{seconds}">'
            f"{body}</testcase>"
        )

    failures = sum(outcome == "FAILED" for outcome in outcomes.values())
    path.write_text(
        f'<testsuites><testsuite name="pytest" tests="{len(outcomes)}" '
        f'failures="{failures}">{"".join(cases)}</testsuite></testsuites>'
    )


def _benchmark(
    path: Path, command: list[str], median: float, rng: random.Random
) -> None:
    samples = [round(rng.gauss(median, median * 0.015), 6) for _ in range(9)]
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metric": "process_wall_time",
                "unit": "seconds",
                "iterations": 9,
                "warmups": 1,
                "command_sha256": hashlib.sha256(
                    "\0".join(command).encode()
                ).hexdigest(),
                "environment_key": ENVIRONMENT_KEY,
                "samples": samples,
            }
        )
    )


def _stage_outcomes(
    index: int,
    repeat: int,
    pipeline: PipelineDefinition,
    rng: random.Random,
) -> dict[str, list[str]]:
    """Return the attempt statuses for each stage of one run.

    One-off lint, timeout and cancellation events happen on a commit's
    second run, after its first run passed, so they show up as same-commit
    noise rather than as commit-to-commit regressions.
    """
    outcomes: dict[str, list[str]] = {}

    for stage in pipeline.stages:
        name = stage.name

        if any(
            outcomes[dependency][-1:] != ["SUCCEEDED"]
            for dependency in stage.depends_on
        ):
            outcomes[name] = []
            continue

        if name == "lint":
            broken = index in FIXED_REGRESSION_COMMITS or (index, repeat) in {
                (4, 1),
                (13, 1),
            }
            outcomes[name] = ["FAILED"] if broken else ["SUCCEEDED"]
        elif name == "unit-tests" and rng.random() < 0.3:
            outcomes[name] = ["FAILED", "SUCCEEDED"]
        elif name == "integration-tests" and index >= REGRESSION_COMMIT:
            outcomes[name] = ["FAILED"]
        elif name == "integration-tests" and (index, repeat) == (8, 1):
            outcomes[name] = ["TIMED_OUT"]
        elif name == "integration-tests" and (index, repeat) == (14, 1):
            outcomes[name] = ["CANCELLED"]
        else:
            outcomes[name] = ["SUCCEEDED"]

    return outcomes


def _record_run(
    factory: SessionFactory,
    *,
    paths: DemoPaths,
    repository_id: UUID,
    definition_id: UUID,
    pipeline: PipelineDefinition,
    commit: Commit,
    index: int,
    repeat: int,
    queued_at: datetime,
    rng: random.Random,
    summary: SeedSummary,
) -> None:
    outcomes = _stage_outcomes(index, repeat, pipeline, rng)
    finals = {attempts[-1] for attempts in outcomes.values() if attempts}

    if "CANCELLED" in finals:
        run_status = "CANCELLED"
    elif finals - {"SUCCEEDED"}:
        run_status = "FAILED"
    else:
        run_status = "SUCCEEDED"
    clock = queued_at + timedelta(seconds=rng.randint(2, 20))
    pending_reports: list[tuple[UUID, str, Path]] = []

    with factory.begin() as session:
        run = PipelineRunRecord(
            repository_id=repository_id,
            pipeline_definition_id=definition_id,
            status=run_status,
            queued_at=queued_at,
            started_at=clock,
            git_commit_sha=commit.sha,
            git_branch="main",
            git_is_dirty=False,
            git_commit_at=commit.committed_at,
            git_commit_subject=commit.subject,
        )
        session.add(run)
        session.flush()

        for position, stage in enumerate(pipeline.stages):
            name = stage.name
            command = list(stage.command)
            attempts = outcomes[name]
            stage_started = clock if attempts else None
            stage_record = StageRunRecord(
                pipeline_run_id=run.id,
                stage_name=name,
                position=position,
                command=command,
                status=attempts[-1] if attempts else "BLOCKED",
                started_at=stage_started,
            )
            session.add(stage_record)
            session.flush()

            for number, status in enumerate(attempts, start=1):
                base = {"lint": 6, "build": 41, "unit-tests": 74}.get(name, 160)
                duration = timedelta(seconds=base * rng.uniform(0.85, 1.25))

                if status == "TIMED_OUT":
                    duration = timedelta(seconds=900)
                elif status == "CANCELLED":
                    duration = timedelta(seconds=37)

                attempt = StageAttemptRecord(
                    stage_run_id=stage_record.id,
                    attempt_number=number,
                    status=status,
                    exit_code={"SUCCEEDED": 0, "FAILED": 1}.get(status),
                    started_at=clock,
                    finished_at=clock + duration,
                )
                session.add(attempt)
                session.flush()
                clock += duration

                directory = paths.artifacts / str(run.id)
                directory.mkdir(parents=True, exist_ok=True)

                if name in {"unit-tests", "integration-tests"} and status in {
                    "SUCCEEDED",
                    "FAILED",
                }:
                    report = directory / f"{name}-{number}.xml"
                    tests = UNIT_TESTS if name == "unit-tests" else INTEGRATION_TESTS
                    results = {test: "PASSED" for test in tests}

                    if status == "FAILED" and name == "unit-tests":
                        results[FLAKY_TEST] = "FAILED"
                    if status == "FAILED" and name == "integration-tests":
                        results[REGRESSED_TEST] = "FAILED"

                    _junit(report, name.replace("-", "_"), results, rng)
                    pending_reports.append((attempt.id, "pytest", report))

                if name == "bench-statement-parser" and status == "SUCCEEDED":
                    report = directory / f"{name}-{number}.json"
                    median = 0.205 if index >= SLOWDOWN_COMMIT else 0.142
                    _benchmark(report, command, median, rng)
                    pending_reports.append((attempt.id, "benchmark", report))

            stage_record.finished_at = clock if attempts else None
            stage_record.exit_code = 0 if attempts[-1:] == ["SUCCEEDED"] else None

        run.finished_at = clock

    for attempt_id, kind, report in pending_reports:
        if kind == "benchmark":
            BenchmarkService(factory).ingest(attempt_id, report)
            summary.benchmark_reports += 1
        else:
            TestReportService(factory).ingest(attempt_id, "pytest", report)
            summary.test_reports += 1

    summary.runs += 1


def seed_demo(database: Path, *, train_model: bool = True) -> SeedSummary:
    """Create the demo database, workspace, reports and model."""
    paths = DemoPaths.beside(database)
    existing = paths.existing()

    if existing:
        raise DemoSeedError(
            "Refusing to overwrite existing demo files: "
            + ", ".join(str(path) for path in existing)
        )

    rng = random.Random(SEED)
    commits = _create_workspace(paths.workspace)
    paths.artifacts.mkdir(parents=True)
    upgrade_database(paths.database)
    engine = create_sqlite_engine(paths.database)

    try:
        factory = create_session_factory(engine)

        with factory.begin() as session:
            repository = Repository(
                id=DEMO_REPOSITORY_ID,
                name=REPOSITORY_NAME,
                local_path=str(paths.workspace),
                language="python",
                build_system="setuptools",
                test_framework="pytest",
            )
            session.add(repository)
            session.flush()
            repository_id = repository.id

        summary = SeedSummary(paths=paths, repository_id=repository_id)
        service = PipelineService(factory)
        first = service.create_version(repository_id, yaml.safe_dump(PIPELINE_V1))
        second = service.create_version(repository_id, yaml.safe_dump(PIPELINE_V2))

        for index, commit in enumerate(commits[:5]):
            _record_run(
                factory,
                paths=paths,
                repository_id=repository_id,
                definition_id=first.id,
                pipeline=PipelineDefinition.model_validate(PIPELINE_V1),
                commit=commit,
                index=index,
                repeat=0,
                queued_at=commit.committed_at + timedelta(minutes=3),
                rng=rng,
                summary=summary,
            )

        for index, commit in enumerate(commits):
            # Two runs per commit give same-commit evidence for flakiness.
            for repeat in range(2):
                _record_run(
                    factory,
                    paths=paths,
                    repository_id=repository_id,
                    definition_id=second.id,
                    pipeline=PipelineDefinition.model_validate(PIPELINE_V2),
                    commit=commit,
                    index=index,
                    repeat=repeat,
                    queued_at=commit.committed_at + timedelta(minutes=4 + 95 * repeat),
                    rng=rng,
                    summary=summary,
                )

        if train_model:
            with tempfile.TemporaryDirectory(prefix="buildscope-demo-") as scratch:
                dataset = Path(scratch) / "dataset"
                export_failure_dataset(factory, dataset)
                train_failure_classifier(dataset, paths.model)

            summary.model_trained = True

    finally:
        engine.dispose()

    return summary
