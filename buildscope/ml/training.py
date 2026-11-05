"""Reproducible, leakage-aware baseline model training

Consumes only the train/validation/test CSV and manifest/provenance
produced by Part 26. It does not query SQLite or read post-run data.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import (
    OneHotEncoder,
    StandardScaler,
)

from buildscope.datasets.failures import (
    FEATURE_COLUMNS,
    SCHEMA_VERSION,
    TARGET_COLUMN,
    TRAIN_COLUMNS,
)


MODEL_SCHEMA_VERSION = "failure-logreg-v1"
THRESHOLD = 0.5
SEED = 42

CATEGORICAL_COLUMNS = (
    "pipeline_name",
    "stage_name",
    "executable",
    "command_sha256",
    "git_worktree_state",
    "git_head_state",
)

NUMERIC_COLUMNS = tuple(
    name for name in FEATURE_COLUMNS if name not in CATEGORICAL_COLUMNS
)

INTEGER_COLUMNS = frozenset(
    {
        "pipeline_version",
        "stage_position",
        "argument_count",
        "queued_hour_utc",
        "queued_weekday_utc",
        "prior_attempts",
        "prior_failures",
        "prior_timeouts",
        "prior_retry_recoveries",
        "prior_test_executions",
        "prior_benchmark_samples",
    }
)

RATE_COLUMNS = frozenset(
    {
        "prior_failure_rate",
        "prior_test_failure_rate",
    }
)


class ModelTrainingError(ValueError):
    """Input data violates the dataset or training contract."""


@dataclass(frozen=True, slots=True)
class TrainingResult:
    directory: Path
    train_rows: int
    validation_rows: int
    test_rows: int
    validation_f1: float
    test_f1: float
    test_roc_auc: float | None


@dataclass(frozen=True, slots=True)
class _Split:
    ids: tuple[str, ...]
    features: np.ndarray
    labels: np.ndarray


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))

    except (OSError, ValueError) as exc:
        raise ModelTrainingError(f"Cannot read {path.name}: {exc}") from exc

    if not isinstance(value, dict):
        raise ModelTrainingError(f"{path.name} must contain a JSON object")

    return value


def _numeric(value: str, column: str) -> float:
    if not value.strip():
        return float("nan")

    try:
        number = float(value)

    except ValueError as exc:
        raise ModelTrainingError(f"Invalid number in {column}: {value!r}") from exc

    if not math.isfinite(number):
        raise ModelTrainingError(f"Non-finite number in {column}")

    if column in INTEGER_COLUMNS and (number < 0 or not number.is_integer()):
        raise ModelTrainingError(f"Invalid nonnegative integer in {column}")

    if column == "queued_hour_utc" and number > 23:
        raise ModelTrainingError("queued_hour_utc must be 0-23")

    if column == "queued_weekday_utc" and number > 6:
        raise ModelTrainingError("queued_weekday_utc must be 0-6")

    if column in RATE_COLUMNS and not 0 <= number <= 1:
        raise ModelTrainingError(f"{column} must be between 0 and 1")

    if column == "prior_benchmark_last_median_seconds" and number < 0:
        raise ModelTrainingError("Benchmark median cannot be negative")

    return number


def _load_split(path: Path) -> _Split:
    ids: list[str] = []
    xs: list[list[object]] = []
    ys: list[int] = []

    try:
        with path.open(
            newline="",
            encoding="utf-8",
        ) as stream:
            reader = csv.DictReader(stream)

            if reader.fieldnames != list(TRAIN_COLUMNS):
                raise ModelTrainingError(f"Invalid columns in {path.name}")

            for row in reader:
                if set(row) != set(TRAIN_COLUMNS) or any(
                    value is None for value in row.values()
                ):
                    raise ModelTrainingError(f"Malformed CSV row in {path.name}")

                example_id = row["example_id"]

                if not example_id or len(example_id) > 128:
                    raise ModelTrainingError("Missing or invalid example_id")

                target = row[TARGET_COLUMN]

                if target not in {"0", "1"}:
                    raise ModelTrainingError(f"Invalid target: {target!r}")

                assert target is not None

                features: list[object] = []

                for column in FEATURE_COLUMNS:
                    value = row[column]

                    assert value is not None

                    if column in CATEGORICAL_COLUMNS:
                        if len(value) > 2048:
                            raise ModelTrainingError(f"Overlong value in {column}")

                        features.append(value if value else "__MISSING__")

                    else:
                        features.append(_numeric(value, column))

                ids.append(example_id)
                xs.append(features)
                ys.append(int(target))

    except (
        OSError,
        UnicodeError,
        csv.Error,
    ) as exc:
        raise ModelTrainingError(f"Cannot parse {path.name}: {exc}") from exc

    features_array = np.asarray(
        xs,
        dtype=object,
    ).reshape(
        -1,
        len(FEATURE_COLUMNS),
    )

    return _Split(
        tuple(ids),
        features_array,
        np.asarray(ys, dtype=int),
    )


def _validate_dataset(
    source: Path,
) -> tuple[
    dict[str, Any],
    dict[str, _Split],
    dict[str, str],
]:
    manifest = _read_json(source / "manifest.json")

    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ModelTrainingError("Unsupported Part 26 dataset schema")

    if manifest.get("target_column") != TARGET_COLUMN:
        raise ModelTrainingError("Unexpected target column")

    if manifest.get("feature_columns") != list(FEATURE_COLUMNS):
        raise ModelTrainingError("Feature schema/order does not match Part 26")

    if manifest.get("split_method") != (
        "chronological_by_pipeline_queued_at_grouped_by_run"
    ):
        raise ModelTrainingError("Expected chronological run-grouped splits")

    names = (
        "train",
        "validation",
        "test",
    )

    splits = {name: _load_split(source / f"{name}.csv") for name in names}

    counts = manifest.get(
        "row_counts",
        {},
    )

    positives = manifest.get(
        "positive_counts",
        {},
    )

    seen_ids: set[str] = set()

    for name, split in splits.items():
        if counts.get(name) != len(split.labels):
            raise ModelTrainingError(f"Manifest row count mismatch: {name}")

        if positives.get(name) != int(split.labels.sum()):
            raise ModelTrainingError(f"Manifest positive count mismatch: {name}")

        if len(set(split.ids)) != len(split.ids) or seen_ids.intersection(split.ids):
            raise ModelTrainingError("Duplicate example_id across splits")

        seen_ids.update(split.ids)

    by_id: dict[
        str,
        tuple[str, str, datetime],
    ] = {}

    try:
        with (source / "provenance.csv").open(
            newline="",
            encoding="utf-8",
        ) as stream:
            reader = csv.DictReader(stream)

            required = {
                "example_id",
                "split",
                "pipeline_run_id",
                "queued_at_utc",
            }

            if not required.issubset(reader.fieldnames or []):
                raise ModelTrainingError("Missing provenance columns")

            for row in reader:
                example_id = row["example_id"]

                if not example_id:
                    raise ModelTrainingError("Missing provenance example_id")

                if example_id in by_id:
                    raise ModelTrainingError("Duplicate provenance example_id")

                timestamp_text = row["queued_at_utc"]

                if timestamp_text is None:
                    raise ModelTrainingError("Missing provenance timestamp")

                try:
                    queued_at = datetime.fromisoformat(timestamp_text)

                except (
                    TypeError,
                    ValueError,
                ) as exc:
                    raise ModelTrainingError("Invalid provenance timestamp") from exc

                if queued_at.tzinfo is None:
                    raise ModelTrainingError(
                        "Provenance timestamps must be timezone-aware"
                    )

                by_id[example_id] = (
                    row["split"] or "",
                    row["pipeline_run_id"] or "",
                    queued_at.astimezone(UTC),
                )

    except (
        OSError,
        UnicodeError,
        csv.Error,
    ) as exc:
        raise ModelTrainingError(f"Cannot read provenance: {exc}") from exc

    if set(by_id) != seen_ids:
        raise ModelTrainingError("Provenance and feature rows differ")

    runs: dict[str, str] = {}

    dates: dict[
        str,
        list[datetime],
    ] = {name: [] for name in names}

    for name, split in splits.items():
        for example_id in split.ids:
            recorded_split, run_id, queued_at = by_id[example_id]

            if recorded_split != name or not run_id:
                raise ModelTrainingError("Provenance split mismatch")

            if run_id in runs and runs[run_id] != name:
                raise ModelTrainingError("Pipeline run crosses dataset splits")

            runs[run_id] = name
            dates[name].append(queued_at)

    if manifest.get("distinct_runs") != len(runs):
        raise ModelTrainingError("Manifest distinct run count mismatch")

    if (
        dates["train"]
        and dates["validation"]
        and max(dates["train"]) > min(dates["validation"])
    ):
        raise ModelTrainingError("Training data occurs after validation data")

    if (
        dates["validation"]
        and dates["test"]
        and max(dates["validation"]) > min(dates["test"])
    ):
        raise ModelTrainingError("Validation data occurs after test data")

    hashes = {
        name: _sha256(source / name)
        for name in (
            "manifest.json",
            "train.csv",
            "validation.csv",
            "test.csv",
            "provenance.csv",
        )
    }

    return manifest, splits, hashes


def _model() -> Pipeline:
    categorical_indices = [
        FEATURE_COLUMNS.index(column) for column in CATEGORICAL_COLUMNS
    ]

    numeric_indices = [FEATURE_COLUMNS.index(column) for column in NUMERIC_COLUMNS]

    preprocess = ColumnTransformer(
        transformers=[
            (
                "categories",
                OneHotEncoder(
                    handle_unknown="ignore",
                    min_frequency=2,
                    max_categories=128,
                    sparse_output=True,
                ),
                categorical_indices,
            ),
            (
                "numbers",
                Pipeline(
                    steps=[
                        (
                            "impute",
                            SimpleImputer(
                                strategy="median",
                                add_indicator=True,
                                keep_empty_features=True,
                            ),
                        ),
                        (
                            "scale",
                            StandardScaler(with_mean=False),
                        ),
                    ]
                ),
                numeric_indices,
            ),
        ],
        sparse_threshold=0.3,
    )

    return Pipeline(
        steps=[
            (
                "preprocess",
                preprocess,
            ),
            (
                "classifier",
                LogisticRegression(
                    solver="liblinear",
                    class_weight="balanced",
                    max_iter=2000,
                    random_state=SEED,
                ),
            ),
        ]
    )


def _metrics(
    labels: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, Any]:
    predicted = (probabilities >= THRESHOLD).astype(int)

    tn, fp, fn, tp = (
        int(number)
        for number in confusion_matrix(
            labels,
            predicted,
            labels=[0, 1],
        ).ravel()
    )

    has_two_classes = len(set(labels.tolist())) == 2

    return {
        "rows": int(len(labels)),
        "positive_count": int(labels.sum()),
        "decision_threshold": THRESHOLD,
        "accuracy": float(
            accuracy_score(
                labels,
                predicted,
            )
        ),
        "precision": float(
            precision_score(
                labels,
                predicted,
                zero_division=0,
            )
        ),
        "recall": float(
            recall_score(
                labels,
                predicted,
                zero_division=0,
            )
        ),
        "f1": float(
            f1_score(
                labels,
                predicted,
                zero_division=0,
            )
        ),
        "roc_auc": (
            float(
                roc_auc_score(
                    labels,
                    probabilities,
                )
            )
            if has_two_classes
            else None
        ),
        "brier_score": float(
            brier_score_loss(
                labels,
                probabilities,
            )
        ),
        "confusion_matrix": {
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "tp": tp,
        },
    }


def _write_json(
    path: Path,
    content: dict[str, Any],
) -> None:
    path.write_text(
        json.dumps(
            content,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    os.chmod(path, 0o600)


def train_failure_classifier(
    dataset_directory: Path,
    output_directory: Path,
) -> TrainingResult:
    """Fit one immutable baseline from Part 26 data."""
    source = dataset_directory.expanduser().resolve(strict=True)

    if not source.is_dir():
        raise ModelTrainingError("Dataset path must be a directory")

    destination = output_directory.expanduser().resolve()

    if destination.exists():
        raise FileExistsError(f"Model destination exists: {destination}")

    manifest, splits, hashes = _validate_dataset(source)

    train = splits["train"]
    validation = splits["validation"]
    test = splits["test"]

    if len(train.labels) < 10:
        raise ModelTrainingError("Need at least 10 training examples")

    if set(train.labels.tolist()) != {0, 1}:
        raise ModelTrainingError(
            "Training set requires both failure and success labels"
        )

    if not len(validation.labels) or not len(test.labels):
        raise ModelTrainingError(
            "Need nonempty chronological validation and test splits"
        )

    model = _model()

    model.fit(
        train.features,
        train.labels,
    )

    probabilities = {
        name: model.predict_proba(split.features)[:, 1]
        for name, split in splits.items()
    }

    base_prior = float(train.labels.mean())

    model_metrics = {
        name: _metrics(
            split.labels,
            probabilities[name],
        )
        for name, split in splits.items()
    }

    baseline_metrics = {
        name: _metrics(
            split.labels,
            np.full(
                len(split.labels),
                base_prior,
            ),
        )
        for name, split in splits.items()
    }

    notes: list[str] = list(manifest.get("warnings", []))

    for name in (
        "validation",
        "test",
    ):
        if len(set(splits[name].labels.tolist())) < 2:
            notes.append(f"{name} has one class: ROC AUC is undefined")

        if len(splits[name].labels) < 30:
            notes.append(f"{name} has fewer than 30 rows: metrics are unstable")

    notes.append(
        "Model must not be deployed solely from these holdout metrics"
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    staging = Path(
        tempfile.mkdtemp(
            prefix=".failure-model-",
            dir=destination.parent,
        )
    )

    os.chmod(staging, 0o700)

    try:
        model_path = staging / "model.joblib"

        joblib.dump(
            model,
            model_path,
            compress=3,
        )

        os.chmod(
            model_path,
            0o600,
        )

        metrics = {
            "schema_version": MODEL_SCHEMA_VERSION,
            "logistic_regression": model_metrics,
            "constant_prior_baseline": baseline_metrics,
        }

        metadata = {
            "schema_version": MODEL_SCHEMA_VERSION,
            "dataset_schema_version": SCHEMA_VERSION,
            "trained_at_utc": datetime.now(UTC).isoformat(),
            "estimator": "LogisticRegression",
            "seed": SEED,
            "threshold": THRESHOLD,
            "class_weight": "balanced",
            "train_prior_positive": base_prior,
            "feature_columns": list(FEATURE_COLUMNS),
            "categorical_columns": list(CATEGORICAL_COLUMNS),
            "numeric_columns": list(NUMERIC_COLUMNS),
            "target_column": TARGET_COLUMN,
            "source_file_sha256": hashes,
            "model_joblib_sha256": _sha256(model_path),
            "python_packages": {
                "scikit_learn": sklearn.__version__,
                "numpy": np.__version__,
                "joblib": joblib.__version__,
            },
            "counts": {
                name: len(split.labels) for name, split in splits.items()
            },
            "warnings": notes,
            "security": (
                "joblib uses pickle: never load "
                "untrusted model files; use "
                "compatible package versions"
            ),
        }

        _write_json(
            staging / "metrics.json",
            metrics,
        )

        _write_json(
            staging / "metadata.json",
            metadata,
        )

        if destination.exists():
            raise FileExistsError(f"Model destination exists: {destination}")

        staging.rename(destination)

    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return TrainingResult(
        directory=destination,
        train_rows=len(train.labels),
        validation_rows=len(validation.labels),
        test_rows=len(test.labels),
        validation_f1=model_metrics["validation"]["f1"],
        test_f1=model_metrics["test"]["f1"],
        test_roc_auc=model_metrics["test"]["roc_auc"],
    )
