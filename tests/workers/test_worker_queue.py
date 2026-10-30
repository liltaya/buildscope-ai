"""Part 18 integration tests using the real migrations and PipelineRunner."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest

from buildscope.database import create_session_factory, create_sqlite_engine
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
def setup(tmp_path: Path) -> Iterator[tuple[Path, WorkerQueue, UUID]]:
    db = tmp_path / "worker.db"
    upgrade_database(db)
    engine = create_sqlite_engine(db)
    factory = create_session_factory(engine)
    with factory.begin() as session:
        repo = Repository(name="worker-example", local_path=str(tmp_path))
        session.add(repo)
        session.flush()
        repo_id = repo.id
    PipelineService(factory).create_version(
        repo_id,
        "name: quality\nstages:\n  - name: checks\n"
        f"    command: [{sys.executable!r}, -c, 'print(17)']\n",
    )
    yield db, WorkerQueue(factory), repo_id
    engine.dispose()


def test_approval_required(setup: tuple[Path, WorkerQueue, UUID]) -> None:
    _, q, repository_id = setup
    with pytest.raises(ExecutionApprovalRequiredError):
        q.enqueue_version(repository_id, "quality", 1, approved=False)


def test_atomic_claim_and_ownership(setup: tuple[Path, WorkerQueue, UUID]) -> None:
    _, q, repository_id = setup
    job = q.enqueue_version(repository_id, "quality", 1, approved=True)
    claimed = q.claim_next("worker-a")
    assert claimed is not None and claimed.id == job.id
    assert q.claim_next("worker-b") is None
    with pytest.raises(JobOwnershipError):
        q.complete(job.id, "worker-b", RunStatus.FAILED)
    q.complete(job.id, "worker-a", RunStatus.SUCCEEDED)
    assert q.get_job(job.id).status == RunStatus.SUCCEEDED


def test_two_sessions_do_not_claim_same_job(setup: tuple[Path, WorkerQueue, UUID]) -> None:
    db, q, repository_id = setup
    q.enqueue_version(repository_id, "quality", 1, approved=True)

    def claim(worker_id: str) -> object:
        engine = create_sqlite_engine(db)
        try:
            return WorkerQueue(create_session_factory(engine)).claim_next(worker_id)
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("a", "b")))
    assert sum(item is not None for item in results) == 1


def test_worker_process_persists_run(setup: tuple[Path, WorkerQueue, UUID]) -> None:
    db, q, repository_id = setup
    job = q.enqueue_version(repository_id, "quality", 1, approved=True)
    result = subprocess.run(
        [sys.executable, "-m", "buildscope.workers.worker", "--database", str(db), "--drain"],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    saved = q.get_job(job.id)
    assert saved.status == RunStatus.SUCCEEDED
    assert saved.pipeline_run_id is not None


def test_spawned_workers_process_queued_jobs(setup: tuple[Path, WorkerQueue, UUID]) -> None:
    db, q, repository_id = setup
    jobs = [q.enqueue_version(repository_id, "quality", 1, approved=True) for _ in range(3)]
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
    )
    assert result.returncode == 0, result.stderr
    assert all(q.get_job(j.id).status == RunStatus.SUCCEEDED for j in jobs)
    assert len({q.get_job(j.id).pipeline_run_id for j in jobs}) == len(jobs)


def test_same_repository_is_serialized(setup: tuple[Path, WorkerQueue, UUID]) -> None:
    _, q, repository_id = setup
    first = q.enqueue_version(repository_id, "quality", 1, approved=True)
    second = q.enqueue_version(repository_id, "quality", 1, approved=True)
    first_claim = q.claim_next("a")
    assert first_claim and first_claim.id == first.id
    assert q.claim_next("b") is None
    q.complete(first.id, "a", RunStatus.SUCCEEDED)
    next_claim = q.claim_next("b")
    assert next_claim and next_claim.id == second.id
