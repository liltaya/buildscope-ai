"""Read-only benchmark slowdown report CLI"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from buildscope.benchmarks.analysis import (
    BenchmarkAnalysisService,
    SlowdownAssessment,
)
from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BuildScope benchmark regression analysis")

    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/buildscope.db"),
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

    parser.add_argument("--branch")

    parser.add_argument(
        "--max-runs",
        type=int,
        default=200,
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=0.20,
    )

    parser.add_argument(
        "--slowdowns-only",
        action="store_true",
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args(argv)

    engine = create_sqlite_engine(args.database.expanduser().resolve(strict=True))

    try:
        service = BenchmarkAnalysisService(create_session_factory(engine))

        comparisons = service.compare(
            args.repository,
            args.pipeline,
            args.version,
            branch=args.branch,
            max_runs=args.max_runs,
            slowdown_threshold=args.threshold,
        )

        if args.slowdowns_only:
            comparisons = tuple(
                item for item in comparisons if item.assessment == SlowdownAssessment.SLOWDOWN
            )

        if args.json:
            print(
                json.dumps(
                    [asdict(item) for item in comparisons],
                    indent=2,
                )
            )

        else:
            print(f"Benchmark comparisons: {len(comparisons)}")

            for item in comparisons:
                print(
                    f"{item.assessment.value} "
                    f"{item.stage_name} {item.branch} "
                    f"{item.baseline_commit[:12]} -> "
                    f"{item.current_commit[:12]} "
                    f"median="
                    f"{item.baseline_median_seconds:.6f}s -> "
                    f"{item.current_median_seconds:.6f}s "
                    f"change={item.slowdown_percent:+.1f}% "
                    f"n={item.baseline_samples}/"
                    f"{item.current_samples}"
                )

        return 0

    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
