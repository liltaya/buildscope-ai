"""Database history to exported dataset to trained model and holdout metrics"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from buildscope.database import create_session_factory, create_sqlite_engine
from buildscope.database.migrate import upgrade_database
from buildscope.datasets.failures import export_failure_dataset
from buildscope.ml.training import train_failure_classifier
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.run_models import PipelineRunRecord, StageRunRecord
from buildscope.pipelines.service import PipelineService
from buildscope.repositories.models import Repository

BASE = datetime(2026, 2, 1, tzinfo=UTC)

# Failure probability per stage; the model can only beat a constant
# prior by learning that these stages differ.
FAILURE_RATES = {"build": 0.05, "integration": 0.65}

RUNS = 80


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("e2e")
    database = root / "history.db"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    rng = random.Random(7)

    try:
        factory = create_session_factory(engine)

        with factory.begin() as session:
            repository = Repository(name="e2e", local_path=str(root))
            session.add(repository)
            session.flush()
            repository_id = repository.id

        saved = PipelineService(factory).create_version(
            repository_id,
            "name: ci\nstages:\n"
            "  - name: build\n    command: [make]\n"
            "  - name: integration\n    command: [pytest, tests/integration]\n"
            "    depends_on: [build]\n",
        )

        with factory.begin() as session:
            for index in range(RUNS):
                queued_at = BASE + timedelta(hours=index)
                failures = {
                    name: rng.random() < rate for name, rate in FAILURE_RATES.items()
                }
                run = PipelineRunRecord(
                    repository_id=repository_id,
                    pipeline_definition_id=saved.id,
                    status="FAILED" if any(failures.values()) else "SUCCEEDED",
                    queued_at=queued_at,
                    started_at=queued_at,
                    finished_at=queued_at + timedelta(minutes=20),
                    git_commit_sha=f"{index:040x}",
                    git_branch="main",
                    git_is_dirty=False,
                )
                session.add(run)
                session.flush()

                for position, (name, failed) in enumerate(failures.items()):
                    status = "FAILED" if failed else "SUCCEEDED"
                    stage = StageRunRecord(
                        pipeline_run_id=run.id,
                        stage_name=name,
                        position=position,
                        command=(
                            ["make"]
                            if name == "build"
                            else ["pytest", "tests/integration"]
                        ),
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

        destination = root / "dataset"
        export_failure_dataset(factory, destination)

    finally:
        engine.dispose()

    return destination


def test_training_writes_metrics_and_beats_constant_baseline(
    exported: Path,
    tmp_path: Path,
) -> None:
    result = train_failure_classifier(exported, tmp_path / "model")

    assert result.train_rows + result.validation_rows + result.test_rows == RUNS * 2

    metrics = json.loads((tmp_path / "model" / "metrics.json").read_text())
    model = metrics["logistic_regression"]["test"]
    baseline = metrics["constant_prior_baseline"]["test"]

    assert model["rows"] == baseline["rows"] == result.test_rows
    assert model["positive_count"] == baseline["positive_count"] > 0

    # A constant prior below 0.5 never predicts a failure.
    assert baseline["recall"] == 0.0
    assert baseline["f1"] == 0.0

    assert model["f1"] > baseline["f1"]
    assert model["brier_score"] < baseline["brier_score"]
    assert model["roc_auc"] is not None
    assert model["roc_auc"] > 0.75


def test_training_is_reproducible(
    exported: Path,
    tmp_path: Path,
) -> None:
    train_failure_classifier(exported, tmp_path / "first")
    train_failure_classifier(exported, tmp_path / "second")

    first = (tmp_path / "first" / "metrics.json").read_text()
    second = (tmp_path / "second" / "metrics.json").read_text()

    assert first == second
