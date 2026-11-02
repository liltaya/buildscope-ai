"""Launch a bounded pool of independent local Python workers"""

from __future__ import annotations

import argparse
from multiprocessing import get_context
from pathlib import Path

from buildscope.workers.scheduler import SchedulingPolicy
from buildscope.workers.worker import run_worker


def launch_workers(
    database_path: Path,
    *,
    workers: int = 2,
    max_running_jobs: int = 2,
    drain: bool = True,
) -> None:
    """Start worker processes with a common scheduling policy."""
    if (
        isinstance(workers, bool)
        or not isinstance(workers, int)
        or not 1 <= workers <= 4
    ):
        raise ValueError(
            "workers must be between 1 and 4"
        )

    SchedulingPolicy(
        max_running_jobs=max_running_jobs
    )

    database_path = (
        database_path.expanduser().resolve(strict=True)
    )

    context = get_context("spawn")

    processes = [
        context.Process(
            target=run_worker,
            args=(database_path,),
            kwargs={
                "drain": drain,
                "max_running_jobs": max_running_jobs,
            },
            name=f"buildscope-worker-{index + 1}",
        )
        for index in range(workers)
    ]

    for process in processes:
        process.start()

    try:
        for process in processes:
            process.join()

    except KeyboardInterrupt:
        for process in processes:
            if process.is_alive():
                process.terminate()

        for process in processes:
            process.join()

        raise

    failures = [
        (process.pid, process.exitcode)
        for process in processes
        if process.exitcode != 0
    ]

    if failures:
        raise RuntimeError(
            f"Worker process failures: {failures}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BuildScope local worker pool"
    )

    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/buildscope.db"),
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--max-running-jobs",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Keep polling instead of draining only ready jobs",
    )

    args = parser.parse_args()

    launch_workers(
        args.database,
        workers=args.workers,
        max_running_jobs=args.max_running_jobs,
        drain=not args.continuous,
    )


if __name__ == "__main__":
    main()
