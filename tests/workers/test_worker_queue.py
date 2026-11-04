"""Part 18 integration tests using the real migrations and PipelineRunner"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository
from buildscope.workers.queue import (
    ExecutionApprovalRequiredError,
    JobOwnershipError,
    WorkerQueue,
)


@pytest.fixture
def setup(
    tmp_path: Path,
) -> Iterator[tuple[Path, WorkerQueue, UUID]]:
    """Create an isolated database, repository, and pipeline."""

    db = tmp_path / "worker.db"
    upgrade_database(db)

    engine = create_sqlite_engine(db)
    factory = create_session_factory(engine)

    try:
        with factory.begin() as session:
            repo = Repository(
                name="worker-example",
                local_path=str(tmp_path),
            )

            session.add(repo)
            session.flush()

            repo_id = repo.id

        PipelineService(factory).create_version(
            repo_id,
            "name: quality\n"
            "stages:\n"
            "  - name: checks\n"
            f"    command: [{sys.executable!r}, -c, 'print(17)']\n",
        )

        yield db, WorkerQueue(factory), repo_id

    finally:
        engine.dispose()


def test_approval_required(
    setup: tuple[Path, WorkerQueue, UUID],
) -> None:
    """Reject jobs without explicit execution approval."""

    _, q, repository_id = setup

    with pytest.raises(ExecutionApprovalRequiredError):
        q.enqueue_version(
            repository_id,
            "quality",
            1,
            approved=False,
        )


def test_atomic_claim_and_ownership(
    setup: tuple[Path, WorkerQueue, UUID],
) -> None:
    """Ensure jobs are claimed atomically and ownership is enforced."""

    _, q, repository_id = setup

    job = q.enqueue_version(
        repository_id,
        "quality",
        1,
        approved=True,
    )

    claimed = q.claim_next("worker-a")

    assert claimed is not None
    assert claimed.id == job.id

    assert q.claim_next("worker-b") is None

    with pytest.raises(JobOwnershipError):
        q.complete(
            job.id,
            "worker-b",
            RunStatus.FAILED,
            claim_token=claimed.claim_token,
        )

    q.complete(
        job.id,
        "worker-a",
        RunStatus.SUCCEEDED,
        claim_token=claimed.claim_token,
    )

    saved = q.get_job(job.id)

    assert saved.status == RunStatus.SUCCEEDED


def test_two_sessions_do_not_claim_same_job(
    setup: tuple[Path, WorkerQueue, UUID],
) -> None:
    """Ensure concurrent database sessions cannot claim one job twice."""

    db, q, repository_id = setup

    q.enqueue_version(
        repository_id,
        "quality",
        1,
        approved=True,
    )

    def claim(worker_id: str) -> object:
        engine = create_sqlite_engine(db)

        try:
            factory = create_session_factory(engine)
            worker_queue = WorkerQueue(factory)

            return worker_queue.claim_next(worker_id)

        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                claim,
                ("a", "b"),
            )
        )

    assert sum(item is not None for item in results) == 1


def test_worker_process_persists_run(
    setup: tuple[Path, WorkerQueue, UUID],
) -> None:
    """Verify one subprocess worker executes and persists a pipeline run."""

    db, q, repository_id = setup

    job = q.enqueue_version(
        repository_id,
        "quality",
        1,
        approved=True,
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "buildscope.workers.worker",
            "--database",
            str(db),
            "--drain",
        ],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    assert result.returncode == 0, (
        f"Worker process exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    saved = q.get_job(job.id)

    assert saved.status == RunStatus.SUCCEEDED, (
        f"Worker job failed:\n"
        f"job_id={saved.id}\n"
        f"status={saved.status.value}\n"
        f"error={saved.error_message}\n"
        f"pipeline_run_id={saved.pipeline_run_id}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    assert saved.pipeline_run_id is not None


def test_spawned_workers_process_queued_jobs(
    setup: tuple[Path, WorkerQueue, UUID],
) -> None:
    """Verify spawned workers process queued jobs and persist their runs."""

    db, q, repository_id = setup

    jobs = [
        q.enqueue_version(
            repository_id,
            "quality",
            1,
            approved=True,
        )
        for _ in range(3)
    ]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "buildscope.workers.launcher",
            "--database",
            str(db),
            "--workers",
            "2",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    assert result.returncode == 0, (
        f"Worker launcher exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    saved_jobs = [q.get_job(job.id) for job in jobs]

    failures = [
        {
            "id": str(job.id),
            "status": job.status.value,
            "error": job.error_message,
            "pipeline_run_id": (
                str(job.pipeline_run_id) if job.pipeline_run_id is not None else None
            ),
        }
        for job in saved_jobs
        if job.status != RunStatus.SUCCEEDED
    ]

    assert not failures, (
        f"Worker job failures: {failures}\n"
        f"Worker stdout:\n{result.stdout}\n"
        f"Worker stderr:\n{result.stderr}"
    )

    # Every successfully completed job must have a run ID.
    run_ids = [job.pipeline_run_id for job in saved_jobs]

    assert all(run_id is not None for run_id in run_ids), (
        f"One or more jobs have no pipeline run ID: {run_ids}"
    )

    assert len(set(run_ids)) == len(jobs), (
        "Multiple jobs reference the same pipeline run.\n"
        f"Job IDs: {[str(job.id) for job in saved_jobs]}\n"
        f"Run IDs: {run_ids}"
    )


def test_same_repository_is_serialized(
    setup: tuple[Path, WorkerQueue, UUID],
) -> None:
    """Ensure two workers do not execute jobs for one repository concurrently."""

    _, q, repository_id = setup

    first = q.enqueue_version(
        repository_id,
        "quality",
        1,
        approved=True,
    )

    second = q.enqueue_version(
        repository_id,
        "quality",
        1,
        approved=True,
    )

    first_claim = q.claim_next("a")

    assert first_claim is not None
    assert first_claim.id == first.id

    assert q.claim_next("b") is None

    q.complete(
        first.id,
        "a",
        RunStatus.SUCCEEDED,
        claim_token=first_claim.claim_token,
    )

    next_claim = q.claim_next("b")

    assert next_claim is not None
    assert next_claim.id == second.id
