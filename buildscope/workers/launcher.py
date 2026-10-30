"""Launch independent workers through Python's spawn multiprocessing context."""

from __future__ import annotations

import argparse
from multiprocessing import get_context
from pathlib import Path

from buildscope.workers.worker import run_worker


def launch_workers(database_path: Path, workers: int = 2) -> None:
    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4 for this SQLite prototype")
    database_path = database_path.expanduser().resolve(strict=True)
    ctx = get_context("spawn")
    processes = [
        ctx.Process(target=run_worker, args=(database_path,), kwargs={"drain": True})
        for _ in range(workers)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join()
    failures = [(p.pid, p.exitcode) for p in processes if p.exitcode != 0]
    if failures:
        raise RuntimeError(f"Worker process failures: {failures}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/buildscope.db"))
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    launch_workers(args.database, workers=args.workers)


if __name__ == "__main__":
    main()
