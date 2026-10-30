"""One worker process drains queued jobs and runs approved pipeline versions."""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from buildscope.database import create_session_factory, create_sqlite_engine
from buildscope.pipelines.runner import PipelineRunner
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository  # register parent ORM table
from buildscope.workers.queue import WorkerQueue

_ = Repository

logger = logging.getLogger(__name__)


def run_worker(database_path: Path, *, drain: bool = True, poll_interval: float = 0.3) -> int:
    """Create a new SQLite engine in this process, claim jobs, execute and finalize."""
    database_path = database_path.expanduser().resolve(strict=True)
    worker_id = f"local-{os.getpid()}-{uuid4().hex[:8]}"
    engine = create_sqlite_engine(database_path)
    processed = 0
    try:
        factory = create_session_factory(engine)
        queue = WorkerQueue(factory)
        runner = PipelineRunner(factory)
        while True:
            job = queue.claim_next(worker_id)
            if job is None:
                if drain:
                    break
                time.sleep(poll_interval)
                continue
            try:
                result = runner.run_version(
                    job.repository_id,
                    job.pipeline_name,
                    job.version,
                    approved=True,
                    log_directory=database_path.parent / "execution-logs",
                )
            except Exception as exc:
                logger.exception("Worker %s failed job %s", worker_id, job.id)
                queue.complete(
                    job.id,
                    worker_id,
                    RunStatus.FAILED,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            else:
                queue.complete(job.id, worker_id, result.status, pipeline_run_id=result.id)
            processed += 1
    finally:
        engine.dispose()
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="BuildScope local pipeline worker")
    parser.add_argument("--database", type=Path, default=Path("data/buildscope.db"))
    parser.add_argument(
        "--drain", action="store_true", help="Exit after all queued jobs have been handled"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(f"Worker finished {run_worker(args.database, drain=args.drain)} job(s)")


if __name__ == "__main__":
    main()
