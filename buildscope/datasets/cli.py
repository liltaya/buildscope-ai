"""Command-line export for leakage-aware failure datasets"""

from __future__ import annotations

import argparse
from pathlib import Path

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.datasets.failures import export_failure_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export stage first-attempt failures")

    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/buildscope.db"),
    )

    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--max-rows",
        type=int,
        default=50_000,
    )

    args = parser.parse_args(argv)

    database = args.database.expanduser().resolve(strict=True)

    if not database.is_file():
        parser.error("--database must be an existing SQLite file")

    engine = create_sqlite_engine(database)

    try:
        result = export_failure_dataset(
            create_session_factory(engine),
            args.output,
            max_rows=args.max_rows,
        )

    finally:
        engine.dispose()

    print(f"Dataset: {result.destination}")

    print(f"Examples: {result.examples}; distinct runs: {result.distinct_runs}")

    for split, count in result.split_counts.items():
        print(f"  {split}: {count}")

    for warning in result.warnings:
        print(f"WARNING: {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
