"""Tests for worker leases, heartbeat ownership, and recovery"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository
from buildscope.workers.models import (
    WorkerHeartbeatRecord,
    WorkerJobRecord,
)
from buildscope.workers.queue import (
    JobOwnershipError,
    WorkerQueue,
)


@pytest.fixture
def setup(
    tmp_path: Path,
) -> Iterator[tuple[Path, WorkerQueue, SessionFactory, UUID]]:
    database = tmp_path / "jobs.db"

    upgrade_database(database)

    engine = create_sqlite_engine(database)
    factory = create_session_factory(engine)

    with factory.begin() as session:
        repository = Repository(
            name="lease-example",
            local_path=str(tmp_path),
        )

        session.add(repository)
        session.flush()
        repo_id = repository.id

    PipelineService(factory).create_version(
        repo_id,
        "name: quality\nstages:\n"
        "  - name: checks\n"
        f"    command: [{sys.executable!r}, -c, 'print(17)']\n",
    )

    yield database, WorkerQueue(factory), factory, repo_id

    engine.dispose()


def enqueue(
    q: WorkerQueue,
    repository_id: UUID,
) -> UUID:
    return q.enqueue_version(
        repository_id,
        "quality",
        1,
        approved=True,
    ).id


def age_claim(
    factory: SessionFactory,
    job_id: UUID,
    worker_id: str,
) -> None:
    """Simulate an expired lease and missing heartbeat."""
    old = datetime.now(UTC) - timedelta(minutes=10)

    with factory.begin() as session:
        job = session.get(
            WorkerJobRecord,
            job_id,
        )

        worker = session.get(
            WorkerHeartbeatRecord,
            worker_id,
        )

        assert job is not None
        assert worker is not None

        job.lease_expires_at = old
        worker.last_seen_at = old


def test_claim_creates_lease_and_heartbeat(
    setup: tuple[Path, WorkerQueue, SessionFactory, UUID],
) -> None:
    _, q, factory, repo_id = setup

    job_id = enqueue(q, repo_id)

    claim = q.claim_next(
        "worker-a",
        lease_seconds=15,
    )

    assert claim is not None
    assert claim.id == job_id
    assert claim.claim_token
    assert claim.lease_expires_at > datetime.now(UTC)

    with factory() as session:
        worker = session.get(
            WorkerHeartbeatRecord,
            "worker-a",
        )

        assert worker is not None
        assert worker.status == "ACTIVE"


def test_heartbeat_renews_current_token_only(
    setup: tuple[Path, WorkerQueue, SessionFactory, UUID],
) -> None:
    _, q, _, repo_id = setup

    enqueue(q, repo_id)

    claim = q.claim_next(
        "worker-a",
        lease_seconds=15,
    )

    assert claim is not None

    q.heartbeat(
        "worker-a",
        job_id=claim.id,
        claim_token=claim.claim_token,
        lease_seconds=30,
    )

    renewed = q.get_job(claim.id)

    assert renewed.lease_expires_at is not None
    assert renewed.lease_expires_at > (claim.lease_expires_at)

    with pytest.raises(JobOwnershipError):
        q.heartbeat(
            "worker-a",
            job_id=claim.id,
            claim_token=uuid4(),
        )

    with pytest.raises(JobOwnershipError):
        q.complete(
            claim.id,
            "worker-a",
            RunStatus.SUCCEEDED,
            claim_token=uuid4(),
        )

    q.complete(
        claim.id,
        "worker-a",
        RunStatus.SUCCEEDED,
        claim_token=claim.claim_token,
    )

    assert q.get_job(claim.id).status == RunStatus.SUCCEEDED


def test_recovery_requires_confirmed_dead_worker(
    setup: tuple[Path, WorkerQueue, SessionFactory, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, q, factory, repo_id = setup

    job_id = enqueue(q, repo_id)
    claim = q.claim_next("worker-a")

    assert claim is not None

    age_claim(
        factory,
        job_id,
        "worker-a",
    )

    monkeypatch.setattr(
        "buildscope.workers.queue._pid_alive",
        lambda pid: True,
    )

    assert q.recover_stale(stale_after_seconds=30) == []

    assert q.get_job(job_id).status == RunStatus.RUNNING


def test_recovery_marks_job_failed_without_replaying(
    setup: tuple[Path, WorkerQueue, SessionFactory, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, q, factory, repo_id = setup

    job_id = enqueue(q, repo_id)
    claim = q.claim_next("worker-a")

    assert claim is not None

    age_claim(
        factory,
        job_id,
        "worker-a",
    )

    monkeypatch.setattr(
        "buildscope.workers.queue._pid_alive",
        lambda pid: False,
    )

    assert q.recover_stale(stale_after_seconds=30) == [job_id]

    assert q.get_job(job_id).status == RunStatus.FAILED

    assert q.recover_stale(stale_after_seconds=30) == []

    with pytest.raises(JobOwnershipError):
        q.complete(
            job_id,
            "worker-a",
            RunStatus.SUCCEEDED,
            claim_token=claim.claim_token,
        )

    assert q.claim_next("worker-b") is None


def test_recovery_reconciles_running_stages(
    setup: tuple[Path, WorkerQueue, SessionFactory, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, q, factory, repo_id = setup

    job_id = enqueue(q, repo_id)
    claim = q.claim_next("worker-a")

    assert claim is not None

    definition_id = q.get_job(job_id).pipeline_definition_id

    with factory.begin() as session:
        run = PipelineRunRecord(
            repository_id=repo_id,
            pipeline_definition_id=definition_id,
            status=RunStatus.RUNNING.value,
            queued_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )

        session.add(run)
        session.flush()
        run_id = run.id

        session.add(
            StageRunRecord(
                pipeline_run_id=run_id,
                stage_name="checks",
                position=0,
                command=["python"],
                status=RunStatus.RUNNING.value,
                started_at=datetime.now(UTC),
            )
        )

    q.attach_run(
        job_id,
        "worker-a",
        claim.claim_token,
        run_id,
    )

    age_claim(
        factory,
        job_id,
        "worker-a",
    )

    monkeypatch.setattr(
        "buildscope.workers.queue._pid_alive",
        lambda pid: False,
    )

    assert q.recover_stale() == [job_id]

    with factory() as session:
        saved_run = session.get(
            PipelineRunRecord,
            run_id,
        )

        assert saved_run is not None
        assert saved_run.status == RunStatus.FAILED.value

        from sqlalchemy import select

        stage = session.scalars(
            select(StageRunRecord).where(StageRunRecord.pipeline_run_id == run_id)
        ).one()

        assert stage.status == RunStatus.FAILED.value
        assert saved_run.finished_at is not None


def test_recovery_preserves_already_completed_pipeline(
    setup: tuple[Path, WorkerQueue, SessionFactory, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, q, factory, repo_id = setup

    job_id = enqueue(q, repo_id)
    claim = q.claim_next("worker-a")

    assert claim is not None

    with factory.begin() as session:
        run = PipelineRunRecord(
            repository_id=repo_id,
            pipeline_definition_id=q.get_job(job_id).pipeline_definition_id,
            status=RunStatus.SUCCEEDED.value,
            queued_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )

        session.add(run)
        session.flush()
        run_id = run.id

    q.attach_run(
        job_id,
        "worker-a",
        claim.claim_token,
        run_id,
    )

    age_claim(
        factory,
        job_id,
        "worker-a",
    )

    monkeypatch.setattr(
        "buildscope.workers.queue._pid_alive",
        lambda pid: False,
    )

    assert q.recover_stale() == [job_id]

    assert q.get_job(job_id).status == RunStatus.SUCCEEDED


def test_worker_renews_during_real_long_command(
    setup: tuple[Path, WorkerQueue, SessionFactory, UUID],
) -> None:
    db, q, _, repo_id = setup

    engine = create_sqlite_engine(db)

    try:
        PipelineService(create_session_factory(engine)).create_version(
            repo_id,
            "name: quality\nstages:\n"
            "  - name: checks\n"
            f"    command: [{sys.executable!r}, -c, "
            "'import time; time.sleep(4)']\n",
        )

    finally:
        engine.dispose()

    job = q.enqueue_version(
        repo_id,
        "quality",
        2,
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
            "--lease-seconds",
            "3",
            "--heartbeat-interval",
            "0.3",
        ],
        text=True,
        capture_output=True,
        timeout=40,
    )

    assert result.returncode == 0, result.stderr

    assert q.get_job(job.id).status == RunStatus.SUCCEEDED

    assert q.get_job(job.id).pipeline_run_id is not None
