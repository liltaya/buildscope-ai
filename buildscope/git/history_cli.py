"""Read-only CLI for pipeline execution history by Git revision"""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import UUID

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.git.regressions import (
    GitRegressionHistoryService,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="BuildScope Git run history")

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
        "--branch",
        default=None,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=100,
    )

    parser.add_argument(
        "action",
        choices=(
            "history",
            "regressions",
        ),
    )

    args = parser.parse_args()

    engine = create_sqlite_engine(args.database)

    try:
        service = GitRegressionHistoryService(create_session_factory(engine))

        if args.action == "history":
            for run in service.list_history(
                args.repository,
                args.pipeline,
                branch=args.branch,
                limit=args.limit,
            ):
                print(
                    f"{run.queued_at.isoformat()} "
                    f"{run.status.value:<9} "
                    f"{(run.branch or 'DETACHED'):<20} "
                    f"{run.commit_sha[:12]} "
                    f"v{run.pipeline_version} "
                    f"{'DIRTY' if run.is_dirty else 'clean'} "
                    f"run={run.run_id}"
                )

        else:
            found = service.find_regressions(
                args.repository,
                args.pipeline,
                branch=args.branch,
                limit=args.limit,
            )

            for regression in found:
                print(
                    f"REGRESSION {regression.branch}: "
                    f"{regression.previous.commit_sha[:12]} "
                    f"(pass) -> "
                    f"{regression.failing.commit_sha[:12]} "
                    f"(fail), version "
                    f"{regression.failing.pipeline_version}, "
                    f"run={regression.failing.run_id}"
                )

            print(f"Detected {len(found)} regression transition(s).")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
