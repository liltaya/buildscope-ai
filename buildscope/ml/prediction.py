"""Load a trusted model and score more features"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import stat
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def _require_private(path: Path) -> None:
    """Refuse model files another local user could have written."""
    try:
        info = path.lstat()
    except OSError as exc:
        raise PredictionError(f"Model path is inaccessible: {path.name}") from exc

    if stat.S_ISLNK(info.st_mode):
        raise PredictionError(f"Model path must not be a symlink: {path.name}")

    if info.st_uid != os.getuid():
        raise PredictionError(f"Model path is owned by another user: {path.name}")

    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise PredictionError(f"Model path is writable by other users: {path.name}")


def _feature_row(
    features: Mapping[str, object],
) -> np.ndarray:
    """Convert live features to the exact training column order."""
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

        _require_private(root)
        _require_private(root / "metadata.json")
        _require_private(root / "model.joblib")

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
            raise PredictionError(
                "Model metadata does not match this BuildScope version"
            )

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
            raw = model_path.read_bytes()

        except OSError as exc:
            raise PredictionError("Model file is missing or inaccessible") from exc

        actual = hashlib.sha256(raw).hexdigest()

        if not isinstance(expected, str) or actual != expected:
            raise PredictionError("Model checksum differs from metadata")

        # joblib unpickles, which can run arbitrary code. The ownership
        # checks above are the real trust boundary: the checksum sits
        # beside the model, so it only catches accidental corruption.
        # Hashing and loading the same bytes avoids a swap in between.
        loaded: Any = joblib.load(io.BytesIO(raw))

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


_CacheKey = tuple[str, tuple[int, int, int], tuple[int, int, int]]

_cache_lock = threading.Lock()
_cached: tuple[_CacheKey, TrustedFailureModel] | None = None


def _signature(path: Path) -> tuple[int, int, int]:
    info = path.stat()
    return (info.st_ino, info.st_size, info.st_mtime_ns)


def load_configured_model(directory: Path) -> TrustedFailureModel:
    """Load the operator-configured model, re-verifying it if it changed."""
    global _cached

    root = directory.expanduser().resolve(strict=True)

    try:
        key: _CacheKey = (
            str(root),
            _signature(root / "metadata.json"),
            _signature(root / "model.joblib"),
        )
    except OSError as exc:
        raise PredictionError("Model files are missing or inaccessible") from exc

    with _cache_lock:
        if _cached is not None and _cached[0] == key:
            return _cached[1]

        model = TrustedFailureModel.load(root, trusted=True)
        _cached = (key, model)

        return model
