"""Trusted inference, feature history and API checks"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import joblib
import numpy as np
import pytest
import sklearn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy.orm import Session, sessionmaker

import buildscope.ml.live_features as live
from apps.api.routers.predictions import router
from buildscope.datasets.failures import (
    FEATURE_COLUMNS,
    SCHEMA_VERSION,
    _command_info,
)
from buildscope.ml.live_features import (
    StageFeatures,
    build_live_stage_features,
)
from buildscope.ml.prediction import (
    PredictionError,
    StagePrediction,
    TrustedFailureModel,
    _feature_row,
)
from buildscope.ml.service import FailurePredictionService
from buildscope.ml.training import (
    CATEGORICAL_COLUMNS,
    MODEL_SCHEMA_VERSION,
    NUMERIC_COLUMNS,
)


def sample_features(
    *,
    stage: str = "checks",
    prior: int = 0,
) -> dict[str, object]:
    return {
        "pipeline_name": "quality",
        "pipeline_version": 1,
        "stage_name": stage,
        "stage_position": 0,
        "executable": "python",
        "argument_count": 2,
        "command_sha256": "a" * 64,
        "git_worktree_state": "clean",
        "git_head_state": "branch",
        "queued_hour_utc": 12,
        "queued_weekday_utc": 1,
        "prior_attempts": prior,
        "prior_failures": 0,
        "prior_timeouts": 0,
        "prior_failure_rate": (
            None if prior % 3 == 0 else 0.1
        ),
        "prior_retry_recoveries": 0,
        "prior_test_executions": 0,
        "prior_test_failure_rate": (
            None if prior % 5 == 0 else 0.2
        ),
        "prior_benchmark_samples": 0,
        "prior_benchmark_last_median_seconds": (
            None if prior % 4 == 0 else 0.05
        ),
    }


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    """Train a small synthetic model solely for inference tests."""
    features = np.vstack(
        [
            _feature_row(
                sample_features(
                    stage=(
                        "checks"
                        if index % 2
                        else "lint"
                    ),
                    prior=index,
                )
            )
            for index in range(16)
        ]
    )

    labels = np.asarray(
        [
            index % 2
            for index in range(16)
        ]
    )

    categorical_indices = [
        FEATURE_COLUMNS.index(name)
        for name in CATEGORICAL_COLUMNS
    ]

    numeric_indices = [
        FEATURE_COLUMNS.index(name)
        for name in NUMERIC_COLUMNS
    ]

    model = Pipeline(
        [
            (
                "preprocess",
                ColumnTransformer(
                    [
                        (
                            "categories",
                            OneHotEncoder(
                                handle_unknown="ignore"
                            ),
                            categorical_indices,
                        ),
                        (
                            "numbers",
                            Pipeline(
                                [
                                    (
                                        "impute",
                                        SimpleImputer(
                                            strategy="constant",
                                            fill_value=0,
                                            keep_empty_features=True,
                                        ),
                                    ),
                                    (
                                        "scale",
                                        StandardScaler(
                                            with_mean=False
                                        ),
                                    ),
                                ]
                            ),
                            numeric_indices,
                        ),
                    ]
                ),
            ),
            (
                "classifier",
                LogisticRegression(
                    solver="liblinear",
                ),
            ),
        ]
    )

    model.fit(features, labels)

    directory = tmp_path / "model"
    directory.mkdir()

    model_file = directory / "model.joblib"
    joblib.dump(model, model_file)

    checksum = hashlib.sha256(
        model_file.read_bytes()
    ).hexdigest()

    metadata = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "dataset_schema_version": SCHEMA_VERSION,
        "feature_columns": list(FEATURE_COLUMNS),
        "categorical_columns": list(CATEGORICAL_COLUMNS),
        "numeric_columns": list(NUMERIC_COLUMNS),
        "estimator": "LogisticRegression",
        "python_packages": {
            "scikit_learn": sklearn.__version__,
            "numpy": np.__version__,
            "joblib": joblib.__version__,
        },
        "threshold": 0.5,
        "model_joblib_sha256": checksum,
        "trained_at_utc": "2026-11-05T00:00:00+00:00",
    }

    (directory / "metadata.json").write_text(
        json.dumps(metadata)
    )

    return directory


def test_requires_trust(
    model_dir: Path,
) -> None:
    with pytest.raises(
        PredictionError,
        match="trusted",
    ):
        TrustedFailureModel.load(
            model_dir
        )


def test_load_and_predict(
    model_dir: Path,
) -> None:
    scorer = TrustedFailureModel.load(
        model_dir,
        trusted=True,
    )

    result = scorer.predict_one(
        "checks",
        0,
        sample_features(
            stage="checks",
            prior=22,
        ),
    )

    assert 0 <= result.failure_score <= 1

    assert result.predicted_failure == (
        result.failure_score >= 0.5
    )

    assert scorer.info.probability_calibrated is False


def test_invalid_features(
    model_dir: Path,
) -> None:
    scorer = TrustedFailureModel.load(
        model_dir,
        trusted=True,
    )

    missing = sample_features()
    del missing["prior_attempts"]

    with pytest.raises(PredictionError):
        scorer.predict_one(
            "checks",
            0,
            missing,
        )

    wrong_type = sample_features()
    wrong_type["prior_attempts"] = "invalid"

    with pytest.raises(PredictionError):
        scorer.predict_one(
            "checks",
            0,
            wrong_type,
        )

    invalid_rate = sample_features()
    invalid_rate["prior_failure_rate"] = 9

    with pytest.raises(PredictionError):
        scorer.predict_one(
            "checks",
            0,
            invalid_rate,
        )


def test_tampered_model_rejected(
    model_dir: Path,
) -> None:
    (model_dir / "model.joblib").write_bytes(
        b"changed-model-content"
    )

    with pytest.raises(
        PredictionError,
        match="checksum",
    ):
        TrustedFailureModel.load(
            model_dir,
            trusted=True,
        )


def test_package_version_mismatch_rejected(
    model_dir: Path,
) -> None:
    path = model_dir / "metadata.json"

    metadata = json.loads(
        path.read_text()
    )

    metadata["python_packages"][
        "scikit_learn"
    ] = "0.0.0"

    path.write_text(
        json.dumps(metadata)
    )

    with pytest.raises(
        PredictionError,
        match="versions",
    ):
        TrustedFailureModel.load(
            model_dir,
            trusted=True,
        )


class FakeFactory:
    def __init__(self, repository: object) -> None:
        self.repository = repository

    def __call__(self) -> FakeFactory:
        return self

    def __enter__(self) -> FakeFactory:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def get(
        self,
        model: object,
        key: object,
    ) -> object:
        return self.repository


@dataclass
class HistoricalObservation:
    repository_id: object
    pipeline_name: str
    pipeline_version: int
    stage_name: str
    command_sha256: str
    queued_at: datetime
    finished_at: datetime
    run_id: object

    label: int = 1
    attempt_status: str = "FAILED"
    stage_status: str = "SUCCEEDED"

    test_executions: int = 2
    test_failures: int = 1
    benchmark_samples: int = 3
    benchmark_median: float | None = 0.6

    @property
    def history_key(self) -> tuple[object, ...]:
        return (
            self.repository_id,
            self.pipeline_name,
            self.pipeline_version,
            self.stage_name,
            self.command_sha256,
        )


def test_live_features_use_only_completed_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_id = uuid4()

    factory = FakeFactory(
        SimpleNamespace(local_path=str(tmp_path))
    )

    stage = SimpleNamespace(
        name="checks",
        command=("python", "-m"),
    )

    saved = SimpleNamespace(
        name="quality",
        version=1,
        definition=SimpleNamespace(
            ordered_stages=(stage,)
        ),
    )

    monkeypatch.setattr(
        live,
        "PipelineService",
        lambda factory: SimpleNamespace(
            get_version=lambda *args: saved
        ),
    )

    # Correction 1:
    # The test fixture already contains an ordered pipeline plan.
    # Avoid calling the real graph planner, which requires .stages.
    monkeypatch.setattr(
        live,
        "build_pipeline_plan",
        lambda definition: definition,
    )

    monkeypatch.setattr(
        live,
        "capture_git_snapshot",
        lambda root: SimpleNamespace(
            commit_sha="a" * 40,
            branch="main",
            is_dirty=False,
        ),
    )

    fingerprint, _, _ = _command_info(
        stage.command
    )

    timestamp = datetime(
        2026,
        1,
        1,
        tzinfo=UTC,
    )

    earlier = HistoricalObservation(
        repository_id,
        "quality",
        1,
        "checks",
        fingerprint,
        timestamp - timedelta(days=3),
        timestamp - timedelta(days=2),
        uuid4(),
    )

    still_running_at_prediction_time = (
        HistoricalObservation(
            repository_id,
            "quality",
            1,
            "checks",
            fingerprint,
            timestamp - timedelta(days=1),
            timestamp + timedelta(days=2),
            uuid4(),
        )
    )

    monkeypatch.setattr(
        live,
        "_load_observations",
        lambda factory, max_rows: [
            earlier,
            still_running_at_prediction_time,
        ],
    )

    # Correction 2:
    # FakeFactory is an intentional test double, not a SQLAlchemy
    # sessionmaker. The cast satisfies static type checking.
    result = build_live_stage_features(
        cast(sessionmaker[Session], factory),
        repository_id,
        "quality",
        1,
        as_of=timestamp,
    )[0]

    assert result.values["prior_attempts"] == 1
    assert result.values["prior_failure_rate"] == 1.0
    assert result.values["prior_retry_recoveries"] == 1
    assert result.values["prior_test_failure_rate"] == 0.5
    assert result.values["git_worktree_state"] == "clean"

    assert set(result.values) == set(FEATURE_COLUMNS)


def test_service_scores_stage_features(
    monkeypatch: pytest.MonkeyPatch,
    model_dir: Path,
) -> None:
    import buildscope.ml.service as service_module

    monkeypatch.setattr(
        service_module,
        "build_live_stage_features",
        lambda *args: (
            StageFeatures(
                "checks",
                0,
                sample_features(),
            ),
        ),
    )

    model = TrustedFailureModel.load(
        model_dir,
        trusted=True,
    )

    result = FailurePredictionService(
        None,  # type: ignore[arg-type]
        model,
    ).predict_pipeline(
        uuid4(),
        "quality",
        1,
    )

    assert len(result.predictions) == 1
    assert result.predictions[0].stage_name == "checks"


def test_api_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "BUILDSCOPE_ENABLE_LOCAL_PREDICTIONS",
        raising=False,
    )

    app = FastAPI()
    app.include_router(router)

    client = TestClient(
        app,
        client=("127.0.0.1", 50000),
    )

    response = client.post(
        "/predictions/stages",
        json={
            "repository_id": str(uuid4()),
            "pipeline_name": "quality",
            "version": 1,
        },
    )

    assert response.status_code == 503


def test_api_returns_readonly_scores(
    monkeypatch: pytest.MonkeyPatch,
    model_dir: Path,
    tmp_path: Path,
) -> None:
    import apps.api.routers.predictions as api

    monkeypatch.setenv(
        "BUILDSCOPE_ENABLE_LOCAL_PREDICTIONS",
        "1",
    )

    monkeypatch.setenv(
        "BUILDSCOPE_TRUSTED_MODEL_DIR",
        str(model_dir),
    )

    database = tmp_path / "database.db"
    database.write_text("")

    monkeypatch.setenv(
        "BUILDSCOPE_DATABASE_PATH",
        str(database),
    )

    class FakeEngine:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(
        api,
        "create_sqlite_engine",
        lambda path: FakeEngine(),
    )

    monkeypatch.setattr(
        api,
        "create_session_factory",
        lambda engine: None,
    )

    model = TrustedFailureModel.load(
        model_dir,
        trusted=True,
    )

    monkeypatch.setattr(
        api,
        "_trusted_local_model",
        lambda path: model,
    )

    class FakeService:
        def __init__(self, *args: object) -> None:
            pass

        def predict_pipeline(
            self,
            repository_id: object,
            pipeline: str,
            version: int,
        ) -> object:
            return SimpleNamespace(
                repository_id=repository_id,
                pipeline_name=pipeline,
                pipeline_version=version,
                model=model.info,
                predictions=(
                    StagePrediction(
                        "checks",
                        0,
                        0.67,
                        True,
                    ),
                ),
            )

    monkeypatch.setattr(
        api,
        "FailurePredictionService",
        FakeService,
    )

    app = FastAPI()
    app.include_router(router)

    response = TestClient(
        app,
        client=("127.0.0.1", 50000),
    ).post(
        "/predictions/stages",
        json={
            "repository_id": str(uuid4()),
            "pipeline_name": "quality",
            "version": 1,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert (
        body["predictions"][0]["failure_score"]
        == 0.67
    )

    assert body["probability_calibrated"] is False
    assert "command" not in str(body)


def test_cli_requires_trusted_model_flag() -> None:
    from buildscope.ml.predict_cli import main

    with pytest.raises(
        SystemExit,
    ) as error:
        main(
            [
                "--model",
                "/somewhere/model",
                "--repository",
                str(uuid4()),
                "--pipeline",
                "quality",
                "--version",
                "1",
            ]
        )

    assert error.value.code == 2


def test_api_rejects_remote_clients() -> None:
    app = FastAPI()
    app.include_router(router)

    response = TestClient(
        app,
        client=("192.0.2.10", 50000),
    ).post(
        "/predictions/stages",
        json={
            "repository_id": str(uuid4()),
            "pipeline_name": "quality",
            "version": 1,
        },
    )

    assert response.status_code == 403
