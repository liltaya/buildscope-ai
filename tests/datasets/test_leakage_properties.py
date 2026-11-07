"""Leakage properties checked over a seeded history of overlapping runs"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from buildscope.database import create_session_factory, create_sqlite_engine
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.datasets.failures import (
    RECENT_WINDOW,
    build_failure_examples,
    export_failure_dataset,
)
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.run_models import PipelineRunRecord, StageRunRecord
from buildscope.pipelines.service import PipelineService
from buildscope.repositories.models import Repository

BASE = datetime(2026, 3, 1, tzinfo=UTC)

STAGES = ("build", "tests")

RUNS = 40


@dataclass(frozen=True, slots=True)
class Observation:
    run_id: UUID
    attempt_id: UUID
    stage_name: str
    queued_at: datetime
    finished_at: datetime
    failed: bool


@pytest.fixture
def seeded(tmp_path: Path) -> Iterator[tuple[SessionFactory, list[Observation]]]:
    database = tmp_path / "leakage.db"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    factory = create_session_factory(engine)
    rng = random.Random(20261107)

    with factory.begin() as session:
        repository = Repository(name="leakage", local_path=str(tmp_path))
        session.add(repository)
        session.flush()
        repository_id = repository.id

    saved = PipelineService(factory).create_version(
        repository_id,
        "name: quality\nstages:\n"
        "  - name: build\n    command: [make]\n"
        "  - name: tests\n    command: [pytest, -q]\n    depends_on: [build]\n",
    )

    observations: list[Observation] = []

    with factory.begin() as session:
        for index in range(RUNS):
            queued_at = BASE + timedelta(hours=index)

            # Durations up to 3.5 h make runs overlap, so many runs are
            # still in flight when a later run is queued.
            finished_at = queued_at + timedelta(minutes=rng.randint(12, 210))

            if index == 10:
                # Finishes at exactly the moment run 11 is queued.
                finished_at = BASE + timedelta(hours=11)

            outcomes = {name: rng.random() < 0.3 for name in STAGES}

            run = PipelineRunRecord(
                repository_id=repository_id,
                pipeline_definition_id=saved.id,
                status="FAILED" if any(outcomes.values()) else "SUCCEEDED",
                queued_at=queued_at,
                started_at=queued_at,
                finished_at=finished_at,
                git_commit_sha=f"{index:040x}",
                git_branch="main",
                git_is_dirty=False,
            )
            session.add(run)
            session.flush()

            for position, name in enumerate(STAGES):
                status = "FAILED" if outcomes[name] else "SUCCEEDED"
                stage = StageRunRecord(
                    pipeline_run_id=run.id,
                    stage_name=name,
                    position=position,
                    command=["make"] if name == "build" else ["pytest", "-q"],
                    status=status,
                )
                session.add(stage)
                session.flush()

                attempt = StageAttemptRecord(
                    stage_run_id=stage.id,
                    attempt_number=1,
                    status=status,
                )
                session.add(attempt)
                session.flush()

                observations.append(
                    Observation(
                        run_id=run.id,
                        attempt_id=attempt.id,
                        stage_name=name,
                        queued_at=queued_at,
                        finished_at=finished_at,
                        failed=outcomes[name],
                    )
                )

    try:
        yield factory, observations
    finally:
        engine.dispose()


def test_every_example_uses_only_runs_finished_before_it_was_queued(
    seeded: tuple[SessionFactory, list[Observation]],
) -> None:
    factory, observations = seeded
    by_attempt = {item.attempt_id: item for item in observations}

    examples = build_failure_examples(factory)

    assert len(examples) == len(observations)

    for example in examples:
        target = by_attempt[example.attempt_id]
        visible = [
            item
            for item in observations
            if item.stage_name == target.stage_name
            and item.finished_at < target.queued_at
        ]

        assert example.prior_attempts == len(visible), target

        failures = sum(item.failed for item in visible)
        assert example.features["prior_failure_rate"] == (
            failures / len(visible) if visible else None
        ), target

        # History is ordered by when runs finished, not when they were queued.
        recent = sorted(visible, key=lambda item: (item.finished_at, str(item.run_id)))
        recent = recent[-RECENT_WINDOW:]
        assert example.features["recent_failure_rate"] == (
            sum(item.failed for item in recent) / len(recent) if recent else None
        ), target
        assert example.features["last_attempt_failed"] == (
            int(recent[-1].failed) if recent else None
        ), target


def test_history_excludes_runs_still_in_flight(
    seeded: tuple[SessionFactory, list[Observation]],
) -> None:
    factory, observations = seeded
    examples = build_failure_examples(factory)

    in_flight_at_queue = [
        example
        for example in examples
        if any(
            other.queued_at < example.queued_at <= other.finished_at
            for other in observations
        )
    ]

    # The property above would be vacuous without overlapping runs.
    assert len(in_flight_at_queue) > RUNS // 2


def test_run_finishing_at_queue_instant_is_not_history(
    seeded: tuple[SessionFactory, list[Observation]],
) -> None:
    factory, observations = seeded
    boundary = BASE + timedelta(hours=11)
    examples = [
        example
        for example in build_failure_examples(factory)
        if example.queued_at == boundary and example.stage_name == "build"
    ]

    assert len(examples) == 1

    strictly_before = [
        item
        for item in observations
        if item.stage_name == "build" and item.finished_at < boundary
    ]
    at_instant = [
        item
        for item in observations
        if item.stage_name == "build" and item.finished_at == boundary
    ]

    assert len(at_instant) == 1
    assert examples[0].prior_attempts == len(strictly_before)


def test_splits_are_disjoint_by_run_and_chronological(
    seeded: tuple[SessionFactory, list[Observation]],
    tmp_path: Path,
) -> None:
    factory, _ = seeded
    destination = tmp_path / "export"

    result = export_failure_dataset(factory, destination)

    with (destination / "provenance.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert len(rows) == result.examples == RUNS * len(STAGES)

    splits_by_run: dict[str, set[str]] = defaultdict(set)
    queued_by_split: dict[str, list[datetime]] = defaultdict(list)

    for row in rows:
        splits_by_run[row["pipeline_run_id"]].add(row["split"])
        queued_by_split[row["split"]].append(
            datetime.fromisoformat(row["queued_at_utc"])
        )

    assert all(len(splits) == 1 for splits in splits_by_run.values())
    assert set(queued_by_split) == {"train", "validation", "test"}
    assert max(queued_by_split["train"]) < min(queued_by_split["validation"])
    assert max(queued_by_split["validation"]) < min(queued_by_split["test"])

    for split in ("train", "validation", "test"):
        with (destination / f"{split}.csv").open(newline="") as stream:
            ids = {row["example_id"] for row in csv.DictReader(stream)}

        expected = {row["example_id"] for row in rows if row["split"] == split}
        assert ids == expected
