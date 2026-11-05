"""Train a failure-classification baseline from Part 26 exports"""

from __future__ import annotations

import argparse
from pathlib import Path

from buildscope.ml.training import train_failure_classifier


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train BuildScope failure classifier")

    parser.add_argument(
        "--dataset",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--output",
        required=True,
        type=Path,
    )

    args = parser.parse_args(argv)

    result = train_failure_classifier(
        args.dataset,
        args.output,
    )

    print(f"Model: {result.directory}")

    print(
        f"Rows: train={result.train_rows} "
        f"validation={result.validation_rows} "
        f"test={result.test_rows}"
    )

    print(f"Validation F1: {result.validation_f1:.4f}")

    print(f"Test F1: {result.test_f1:.4f}")

    print(
        "Test ROC AUC: "
        + ("undefined" if result.test_roc_auc is None else f"{result.test_roc_auc:.4f}")
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
