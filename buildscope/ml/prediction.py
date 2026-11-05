"""Load a trusted model and score more features"""

from __future__ import annotations

import hashlib
import json
import math

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import joblib
import numpy as np
import sklearn

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from buildscope.datasets.failures import (
    FEATURE_COLUMNS,
    SCHEMA_VERSION,
)
from buildscope.ml.training import (
    CATEGORICAL_COLUMNS,
    MODEL_SCHEMA_VERSION,
    NUMERIC_COLUMNS,
    _numeric,
)


class PredictionError(ValueError):
    """Model or inference input is invalid."""


@dataclass(frozen=True, slots=True)
class StagePrediction:
    stage_name: str
    stage_position: int
    failure_score: float
    predicted_failure: bool


@dataclass(frozen=True, slots=True)
class ModelInfo:
    schema_version: str
    model_sha256: str
    trained_at_utc: str
    decision_threshold: float
    probability_calibrated: bool = False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def _feature_row(
    features: Mapping[str, object],
) -> np.ndarray:
    """Convert live features to the exact Part 27 model order."""
    if set(features) != set(FEATURE_COLUMNS):
        missing = set(FEATURE_COLUMNS) - set(features)
        extra = set(features) - set(FEATURE_COLUMNS)

        raise PredictionError(
            f"Feature schema mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
        )

    ordered: list[object] = []

    for name in FEATURE_COLUMNS:
        value = features[name]

        if name in CATEGORICAL_COLUMNS:
            if not isinstance(value, str) or len(value) > 2048:
                raise PredictionError(f"Invalid categorical feature: {name}")

            ordered.append(value or "__MISSING__")

        else:
            if value is None:
                ordered.append(float("nan"))

            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PredictionError(f"Invalid numeric feature: {name}")

            else:
                try:
                    ordered.append(_numeric(str(value), name))

                except ValueError as exc:
                    raise PredictionError(str(exc)) from exc

    return np.asarray(
        [ordered],
        dtype=object,
    )


class TrustedFailureModel:
    """Read-only scorer for a trusted local model artifact."""

    def __init__(
        self,
        model: Pipeline,
        info: ModelInfo,
    ) -> None:
        self._model = model
        self.info = info

    @classmethod
    def load(
        cls,
        directory: Path,
        *,
        trusted: bool = False,
    ) -> TrustedFailureModel:
        if not trusted:
            raise PredictionError("Model loading requires explicit trusted=True")

        root = directory.expanduser().resolve(strict=True)

        if not root.is_dir():
            raise PredictionError("Model location is not a directory")

        try:
            metadata: Any = json.loads(
                (root / "metadata.json").read_text(encoding="utf-8")
            )

        except (OSError, ValueError) as exc:
            raise PredictionError("Model metadata could not be read") from exc

        if not isinstance(metadata, dict):
            raise PredictionError("Model metadata must be an object")

        if (
            metadata.get("schema_version") != MODEL_SCHEMA_VERSION
            or metadata.get("dataset_schema_version") != SCHEMA_VERSION
            or metadata.get("feature_columns") != list(FEATURE_COLUMNS)
            or metadata.get("categorical_columns") != list(CATEGORICAL_COLUMNS)
            or metadata.get("numeric_columns") != list(NUMERIC_COLUMNS)
            or metadata.get("estimator") != "LogisticRegression"
        ):
            raise PredictionError("Model metadata is not compatible with Part 27")

        versions = metadata.get("python_packages")

        if (
            not isinstance(versions, dict)
            or versions.get("scikit_learn") != sklearn.__version__
            or versions.get("numpy") != np.__version__
            or versions.get("joblib") != joblib.__version__
        ):
            raise PredictionError(
                "ML package versions differ from the training environment"
            )

        threshold = metadata.get("threshold")

        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0 < threshold < 1
        ):
            raise PredictionError("Invalid model threshold")

        model_path = root / "model.joblib"

        try:
            expected = metadata.get("model_joblib_sha256")

            actual = _sha256(model_path)

        except OSError as exc:
            raise PredictionError("Model file is missing or inaccessible") from exc

        if not isinstance(expected, str) or actual != expected:
            raise PredictionError("Model checksum differs from metadata")

        # SECURITY:
        # joblib uses pickle-based deserialization.
        # Only load an artifact from a trusted directory.
        # A checksum stored beside the model is NOT
        # proof of authenticity.
        loaded: Any = joblib.load(model_path)

        if (
            not isinstance(loaded, Pipeline)
            or "classifier" not in loaded.named_steps
            or not isinstance(
                loaded.named_steps["classifier"],
                LogisticRegression,
            )
            or list(loaded.classes_) != [0, 1]
        ):
            raise PredictionError("Unexpected fitted estimator structure")

        trained_at = metadata.get("trained_at_utc")

        if not isinstance(trained_at, str):
            raise PredictionError("Missing model training timestamp")

        return cls(
            loaded,
            ModelInfo(
                schema_version=MODEL_SCHEMA_VERSION,
                model_sha256=actual,
                trained_at_utc=trained_at,
                decision_threshold=float(threshold),
            ),
        )

    def predict_one(
        self,
        stage_name: str,
        stage_position: int,
        features: Mapping[str, object],
    ) -> StagePrediction:
        row = _feature_row(features)

        probabilities = self._model.predict_proba(row)

        score = float(probabilities[0, 1])

        if not math.isfinite(score) or not 0 <= score <= 1:
            raise PredictionError("Model produced an invalid score")

        return StagePrediction(
            stage_name=stage_name,
            stage_position=stage_position,
            failure_score=score,
            predicted_failure=(score >= self.info.decision_threshold),
        )
