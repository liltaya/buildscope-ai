"""Training and leakage-aware holdout checks"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pytest

from buildscope.datasets.failures import (
    FEATURE_COLUMNS,
    SCHEMA_VERSION,
    TARGET_COLUMN,
    TRAIN_COLUMNS,
)
from buildscope.ml.cli import main as cli_main
from buildscope.ml.training import (
    CATEGORICAL_COLUMNS,
    ModelTrainingError,
    train_failure_classifier,
)


def make_dataset(
    root: Path,
    *,
    train_n: int = 40,
    validation_n: int = 8,
    test_n: int = 8,
    one_class_train: bool = False,
    one_class_test: bool = False,
) -> Path:
    """Create a synthetic failure dataset export for testing only."""
    root.mkdir()

    splits = (
        ("train", train_n),
        ("validation", validation_n),
        ("test", test_n),
    )

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "target_column": TARGET_COLUMN,
        "feature_columns": list(FEATURE_COLUMNS),
        "split_method": ("chronological_by_pipeline_queued_at_grouped_by_run"),
        "row_counts": {},
        "positive_counts": {},
        "distinct_runs": (train_n + validation_n + test_n),
        "warnings": [],
    }

    provenance = []
    i = 0

    for name, count in splits:
        records = []

        for _ in range(count):
            y = 1 if i % 4 == 0 else 0

            if name == "train" and one_class_train:
                y = 0

            if name == "test" and one_class_test:
                y = 0

            example_id = f"example-{i:04d}"

            attrs = {
                "git_worktree_state": ("dirty" if i % 2 else "clean"),
                "git_head_state": "branch",
                "prior_failure_rate": ("" if i % 7 == 0 else 0.25),
                "prior_timeout_rate": ("" if i % 7 == 0 else 0.0),
                "prior_retry_recovery_rate": ("" if i % 7 == 0 else 0.1),
                "recent_failure_rate": ("" if i % 7 == 0 else (0.6 if y else 0.2)),
                "last_attempt_failed": ("" if i % 7 == 0 else i % 2),
                "prior_test_failure_rate": ("" if i % 5 == 0 else 0.12),
            }

            if name != "train" and i % 3 == 0:
                attrs["git_head_state"] = "unseen-head-state"

            records.append(
                {
                    "example_id": example_id,
                    **attrs,
                    TARGET_COLUMN: y,
                }
            )

            provenance.append(
                {
                    "example_id": example_id,
                    "split": name,
                    "repository_id": "repo",
                    "pipeline_run_id": f"run-{i}",
                    "stage_attempt_id": f"attempt-{i}",
                    "git_commit_sha": "a" * 40,
                    "queued_at_utc": (
                        datetime(
                            2025,
                            1,
                            1,
                            tzinfo=UTC,
                        )
                        + timedelta(hours=i)
                    ).isoformat(),
                }
            )

            i += 1

        with (root / f"{name}.csv").open(
            "w",
            newline="",
        ) as out:
            writer = csv.DictWriter(
                out,
                fieldnames=TRAIN_COLUMNS,
            )

            writer.writeheader()
            writer.writerows(records)

        manifest["row_counts"][name] = count

        manifest["positive_counts"][name] = sum(
            record[TARGET_COLUMN] == 1 for record in records
        )

    with (root / "provenance.csv").open(
        "w",
        newline="",
    ) as out:
        writer = csv.DictWriter(
            out,
            fieldnames=list(provenance[0]),
        )

        writer.writeheader()
        writer.writerows(provenance)

    (root / "manifest.json").write_text(json.dumps(manifest))

    return root


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    return make_dataset(tmp_path / "dataset")


def test_train_and_save_outputs(
    dataset: Path,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "model-v1"

    result = train_failure_classifier(
        dataset,
        destination,
    )

    assert (
        result.train_rows,
        result.validation_rows,
        result.test_rows,
    ) == (40, 8, 8)

    assert result.directory == destination

    assert {path.name for path in destination.iterdir()} == {
        "model.joblib",
        "metrics.json",
        "metadata.json",
    }

    metrics = json.loads((destination / "metrics.json").read_text())

    assert 0 <= metrics["logistic_regression"]["test"]["f1"] <= 1

    assert metrics["constant_prior_baseline"]["test"]["rows"] == 8

    assert "provenance" not in json.dumps(metrics)

    metadata = json.loads((destination / "metadata.json").read_text())

    assert metadata["dataset_schema_version"] == SCHEMA_VERSION

    assert metadata["threshold"] == 0.5

    assert "example_id" not in metadata["feature_columns"]

    assert "pipeline_run_id" not in metadata["feature_columns"]

    assert metadata["source_file_sha256"]["train.csv"]


def test_local_trusted_model_reload(
    dataset: Path,
    tmp_path: Path,
) -> None:
    result = train_failure_classifier(
        dataset,
        tmp_path / "model",
    )

    model = joblib.load(result.directory / "model.joblib")

    assert hasattr(
        model,
        "predict_proba",
    )

    assert model.classes_.tolist() == [0, 1]


def test_no_refit_on_unseen_validation_categories(
    dataset: Path,
    tmp_path: Path,
) -> None:
    result = train_failure_classifier(
        dataset,
        tmp_path / "model",
    )

    model = joblib.load(result.directory / "model.joblib")

    encoder = model.named_steps["preprocess"].named_transformers_["categories"]

    head_index = CATEGORICAL_COLUMNS.index("git_head_state")

    assert "unseen-head-state" not in encoder.categories_[head_index]


def test_reject_single_class_train(
    tmp_path: Path,
) -> None:
    data = make_dataset(
        tmp_path / "dataset",
        one_class_train=True,
    )

    with pytest.raises(
        ModelTrainingError,
        match="both failure and success",
    ):
        train_failure_classifier(
            data,
            tmp_path / "model",
        )


def test_reject_empty_holdout(
    tmp_path: Path,
) -> None:
    data = make_dataset(
        tmp_path / "dataset",
        validation_n=0,
    )

    with pytest.raises(
        ModelTrainingError,
        match="nonempty",
    ):
        train_failure_classifier(
            data,
            tmp_path / "model",
        )


def test_one_class_test_auc_undefined(
    tmp_path: Path,
) -> None:
    data = make_dataset(
        tmp_path / "dataset",
        one_class_test=True,
    )

    result = train_failure_classifier(
        data,
        tmp_path / "model",
    )

    assert result.test_roc_auc is None

    metrics = json.loads((result.directory / "metrics.json").read_text())

    assert metrics["logistic_regression"]["test"]["roc_auc"] is None


def test_reject_provenance_cross_split(
    dataset: Path,
    tmp_path: Path,
) -> None:
    path = dataset / "provenance.csv"

    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))

    rows[40]["pipeline_run_id"] = rows[0]["pipeline_run_id"]

    with path.open(
        "w",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(rows[0]),
        )

        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(
        ModelTrainingError,
        match="crosses dataset splits",
    ):
        train_failure_classifier(
            dataset,
            tmp_path / "model",
        )


def test_reject_wrong_feature_schema(
    dataset: Path,
    tmp_path: Path,
) -> None:
    path = dataset / "manifest.json"

    manifest = json.loads(path.read_text())

    manifest["feature_columns"].pop()

    path.write_text(json.dumps(manifest))

    with pytest.raises(
        ModelTrainingError,
        match="Feature schema",
    ):
        train_failure_classifier(
            dataset,
            tmp_path / "model",
        )


def test_reject_invalid_numeric(
    dataset: Path,
    tmp_path: Path,
) -> None:
    path = dataset / "train.csv"

    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))

    rows[1]["prior_failure_rate"] = "inf"

    with path.open(
        "w",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=TRAIN_COLUMNS,
        )

        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(
        ModelTrainingError,
        match="Non-finite",
    ):
        train_failure_classifier(
            dataset,
            tmp_path / "model",
        )


def test_reject_last_attempt_flag_outside_zero_and_one(
    dataset: Path,
    tmp_path: Path,
) -> None:
    path = dataset / "train.csv"

    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))

    rows[1]["last_attempt_failed"] = "2"

    with path.open(
        "w",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=TRAIN_COLUMNS,
        )

        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(
        ModelTrainingError,
        match="0 or 1",
    ):
        train_failure_classifier(
            dataset,
            tmp_path / "model",
        )


def test_refuse_overwrite(
    dataset: Path,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "model"

    train_failure_classifier(
        dataset,
        destination,
    )

    with pytest.raises(FileExistsError):
        train_failure_classifier(
            dataset,
            destination,
        )


def test_cli(
    dataset: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli_main(
            [
                "--dataset",
                str(dataset),
                "--output",
                str(tmp_path / "model"),
            ]
        )
        == 0
    )

    output = capsys.readouterr().out

    assert "Validation F1:" in output
    assert "Test ROC AUC:" in output


def test_numeric_all_missing_train_retained(
    dataset: Path,
    tmp_path: Path,
) -> None:
    result = train_failure_classifier(
        dataset,
        tmp_path / "model",
    )

    model = joblib.load(result.directory / "model.joblib")

    assert hasattr(
        model.named_steps["preprocess"],
        "transform",
    )

    assert not np.isnan(float(result.test_f1))
