"""Command line for the demo history: python -m buildscope.demo."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from buildscope.demo.seed import DemoSeedError, seed_demo


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m buildscope.demo",
        description="Create a reproducible demo history for the dashboard.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser(
        "seed",
        help="record commits, runs, reports and a trained model",
    )
    seed.add_argument(
        "--database",
        type=Path,
        default=Path("data") / "demo.db",
    )
    seed.add_argument(
        "--no-model",
        action="store_true",
        help="skip training the failure model",
    )
    arguments = parser.parse_args(argv)

    try:
        summary = seed_demo(arguments.database, train_model=not arguments.no_model)
    except DemoSeedError as exc:
        parser.error(str(exc))

    paths = summary.paths
    print(f"database:  {paths.database}")
    print(f"workspace: {paths.workspace}")
    print(
        f"recorded {summary.runs} runs, {summary.test_reports} test reports, "
        f"{summary.benchmark_reports} benchmark reports"
    )

    serve = f"buildscope serve --database {paths.database}"

    if summary.model_trained:
        print(f"model:     {paths.model}")
        serve += f" --model {paths.model}"

    print(f"\nOpen the dashboard with:\n  {serve}\n  http://127.0.0.1:8000/dashboard")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
