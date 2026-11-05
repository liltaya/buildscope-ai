"""Read-only CLI for test reliability and retry recovery history"""

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
from buildscope.testing.reliability import (
    ReliabilityClassification,
    TestReliabilityService,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BuildScope test reliability analysis")

    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/buildscope.db"),
    )

    parser.add_argument(
        "--repository",
        required=True,
        type=UUID,
    )

    parser.add_argument(
        "--pipeline",
        required=True,
    )

    parser.add_argument(
        "--version",
        required=True,
        type=int,
    )

    parser.add_argument(
        "--branch",
    )

    parser.add_argument(
        "--max-runs",
        type=int,
        default=200,
    )

    parser.add_argument(
        "--suspected-only",
        action="store_true",
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args(argv)

    engine = create_sqlite_engine(args.database)

    try:
        service = TestReliabilityService(create_session_factory(engine))

        summaries = service.analyze(
            args.repository,
            args.pipeline,
            args.version,
            branch=args.branch,
            max_runs=args.max_runs,
        )

        if args.suspected_only:
            summaries = tuple(
                item
                for item in summaries
                if item.classification
                in {
                    ReliabilityClassification.RETRY_FLAKY,
                    ReliabilityClassification.SAME_COMMIT_VARIABILITY,
                }
            )

        if args.json:
            print(
                json.dumps(
                    [asdict(item) for item in summaries],
                    indent=2,
                )
            )

        else:
            print(f"Test identities: {len(summaries)}")

            for item in summaries:
                rate = (
                    f"{item.failure_rate:.1%}"
                    if item.failure_rate is not None
                    else "n/a"
                )

                print(f"\n{item.classification.value}  {item.identity.display_name}")

                print(
                    f"  pass={item.passed} "
                    f"fail={item.failed} "
                    f"error={item.errors} "
                    f"skip={item.skipped} "
                    f"failure_rate={rate}"
                )

                print(
                    f"  runs={item.distinct_runs} "
                    f"clean_commits={item.clean_commits} "
                    f"retry_recoveries={item.retry_recoveries} "
                    f"same_commit_mixed_groups="
                    f"{item.same_commit_mixed_groups} "
                    f"latest={item.latest_outcome}"
                )

        return 0

    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
