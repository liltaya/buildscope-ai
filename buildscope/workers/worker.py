"""Local worker with heartbeats and fenced job completion"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import UUID, uuid4

from sqlalchemy.exc import OperationalError

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.execution import CppStageExecutor
from buildscope.pipelines.runner import PipelineRunner
from buildscope.pipelines.state import RunStatus
from buildscope.workers.queue import (
    JobOwnershipError,
    WorkerQueue,
)
from buildscope.workers.scheduler import SchedulingPolicy

logger = logging.getLogger(__name__)


def run_worker(
    database_path: Path,
    *,
    drain: bool = True,
    poll_interval: float = 0.3,
    lease_seconds: int = 15,
    heartbeat_interval: float = 2.0,
    max_running_jobs: int = 2,
    cpp_worker: Path | None = None,
) -> int:
    """Process approved jobs with heartbeat-based lease fencing."""

    if not 0 < heartbeat_interval < lease_seconds / 2:
        raise ValueError(
            "heartbeat_interval must be positive and less than half the lease"
        )

    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    SchedulingPolicy(max_running_jobs=max_running_jobs)

    database_path = database_path.expanduser().resolve(strict=True)

    executor = CppStageExecutor(cpp_worker) if cpp_worker is not None else None

    worker_id = f"local-{os.getpid()}-{uuid4().hex[:8]}"

    engine = create_sqlite_engine(database_path)

    processed = 0
    stopped = Event()
    guard = Lock()

    active: tuple[UUID, UUID, Event] | None = None

    try:
        factory = create_session_factory(engine)
        queue = WorkerQueue(factory)

        runner = PipelineRunner(
            factory,
            executor=executor,
        )

        queue.register_worker(
            worker_id,
            pid=os.getpid(),
        )

        def heartbeat_loop() -> None:
            while not stopped.wait(heartbeat_interval):
                with guard:
                    current = active

                try:
                    if current is None:
                        queue.heartbeat(
                            worker_id,
                            lease_seconds=lease_seconds,
                        )

                    else:
                        job_id, token, _cancellation = current

                        queue.heartbeat(
                            worker_id,
                            job_id=job_id,
                            claim_token=token,
                            lease_seconds=lease_seconds,
                        )

                except JobOwnershipError:
                    with guard:
                        still_active = active is current

                    if still_active:
                        logger.error(
                            "Worker %s lost lease/ownership",
                            worker_id,
                        )

                        if current is not None:
                            current[2].set()

                        stopped.set()
                        return

                except OperationalError as exc:
                    if "locked" in str(exc.orig).lower():
                        logger.warning(
                            "Worker %s heartbeat lock retry exhausted",
                            worker_id,
                        )

                    else:
                        logger.exception(
                            "Worker %s heartbeat database error",
                            worker_id,
                        )

                except Exception:
                    logger.exception(
                        "Worker %s heartbeat failed",
                        worker_id,
                    )

        thread = Thread(
            target=heartbeat_loop,
            name="buildscope-heartbeat",
            daemon=True,
        )

        thread.start()

        try:
            while not stopped.is_set():
                job = queue.claim_next(
                    worker_id,
                    lease_seconds=lease_seconds,
                    max_running_jobs=max_running_jobs,
                )

                if job is None:
                    if drain:
                        break

                    stopped.wait(poll_interval)
                    continue

                cancellation = Event()

                with guard:
                    active = (
                        job.id,
                        job.claim_token,
                        cancellation,
                    )

                job_id = job.id
                claim_token = job.claim_token

                def on_run_created(
                    run_id: UUID,
                    *,
                    _job_id: UUID = job_id,
                    _claim_token: UUID = claim_token,
                ) -> None:
                    queue.attach_run(
                        _job_id,
                        worker_id,
                        _claim_token,
                        run_id,
                    )

                try:
                    result = runner.run_version(
                        job.repository_id,
                        job.pipeline_name,
                        job.version,
                        approved=True,
                        cancel_event=cancellation,
                        log_directory=(database_path.parent / "execution-logs"),
                        on_run_created=on_run_created,
                    )

                except Exception as exc:
                    logger.exception(
                        "Worker %s failed job %s",
                        worker_id,
                        job.id,
                    )

                    try:
                        queue.complete(
                            job.id,
                            worker_id,
                            RunStatus.FAILED,
                            claim_token=job.claim_token,
                            error_message=(f"{type(exc).__name__}: {exc}"),
                        )

                    except JobOwnershipError:
                        logger.warning(
                            "Stale worker could not finalize job %s",
                            job.id,
                        )

                else:
                    try:
                        queue.complete(
                            job.id,
                            worker_id,
                            result.status,
                            claim_token=job.claim_token,
                            pipeline_run_id=result.id,
                        )

                    except JobOwnershipError:
                        logger.warning(
                            "Stale worker could not finalize job %s",
                            job.id,
                        )

                finally:
                    with guard:
                        active = None

                processed += 1

        finally:
            stopped.set()

            thread.join(
                timeout=max(
                    heartbeat_interval * 2,
                    1.0,
                )
            )

            queue.stop_worker(worker_id)

    finally:
        engine.dispose()

    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="BuildScope local pipeline worker")

    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/buildscope.db"),
    )

    parser.add_argument(
        "--drain",
        action="store_true",
    )

    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=15,
    )

    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--max-running-jobs",
        type=int,
        default=2,
        help="Global maximum simultaneously running jobs",
    )

    parser.add_argument(
        "--cpp-worker",
        type=Path,
        default=None,
        help="Path to the BuildScope C++20 stage launcher",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    count = run_worker(
        args.database,
        drain=args.drain,
        lease_seconds=args.lease_seconds,
        heartbeat_interval=args.heartbeat_interval,
        max_running_jobs=args.max_running_jobs,
        cpp_worker=args.cpp_worker,
    )

    print(f"Worker finished {count} job(s)")


if __name__ == "__main__":
    main()
