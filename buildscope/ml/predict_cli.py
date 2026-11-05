"""Predict failures without executing a saved pipeline"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.ml.prediction import TrustedFailureModel
from buildscope.ml.service import FailurePredictionService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a saved BuildScope pipeline")

    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/buildscope.db"),
    )

    parser.add_argument(
        "--model",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--trusted-model",
        action="store_true",
        help="Acknowledge that the joblib artifact is trusted",
    )

    parser.add_argument(
        "--repository",
        type=UUID,
        required=True,
    )

    parser.add_argument(
        "--pipeline",
        required=True,
    )

    parser.add_argument(
        "--version",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args(argv)

    if not args.trusted_model:
        parser.error("--trusted-model is required: joblib artifacts must be trusted")

    model = TrustedFailureModel.load(
        args.model,
        trusted=True,
    )

    engine = create_sqlite_engine(args.database.expanduser().resolve(strict=True))

    try:
        result = FailurePredictionService(
            create_session_factory(engine),
            model,
        ).predict_pipeline(
            args.repository,
            args.pipeline,
            args.version,
        )

    finally:
        engine.dispose()

    if args.json:
        print(
            json.dumps(
                asdict(result),
                indent=2,
                default=str,
            )
        )

    else:
        print(f"Pipeline: {result.pipeline_name} v{result.pipeline_version}")

        print(
            f"Model: {result.model.schema_version}, "
            f"sha256={result.model.model_sha256[:12]}"
        )

        print("Scores are not calibrated failure probabilities.")

        for row in result.predictions:
            print(
                f"  {row.stage_position:02d} "
                f"{row.stage_name}: "
                f"score={row.failure_score:.3f} "
                f"flag={int(row.predicted_failure)}"
            )

        print("Read-only analysis. No jobs were queued or approved.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
