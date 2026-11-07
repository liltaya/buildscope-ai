"""Recovery after a worker process is killed in the middle of a job"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from sqlalchemy import select

from buildscope.database import create_session_factory, create_sqlite_engine
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.run_models import PipelineRunRecord, StageRunRecord
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository
from buildscope.workers.queue import WorkerQueue

LEASE_SECONDS = 3


def wait_for(condition: Callable[[], bool], *, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)

    pytest.fail(f"timed out waiting for {what}")


@pytest.fixture
def crash_setup(
    tmp_path: Path,
) -> Iterator[tuple[Path, SessionFactory, UUID, Path]]:
    database = tmp_path / "crash.db"
    stage_pid_file = tmp_path / "stage.pid"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    factory = create_session_factory(engine)

    with factory.begin() as session:
        repository = Repository(name="crash", local_path=str(tmp_path))
        session.add(repository)
        session.flush()
        repository_id = repository.id

    long_stage = (
        "import os, pathlib, sys, time; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpgrp())); "
        "time.sleep(60)"
    )

    PipelineService(factory).create_version(
        repository_id,
        yaml.safe_dump(
            {
                "name": "quality",
                "stages": [
                    {"name": "build", "command": [sys.executable, "-c", "print(1)"]},
                    {
                        "name": "test",
                        "command": [
                            sys.executable,
                            "-c",
                            long_stage,
                            str(stage_pid_file),
                        ],
                        "depends_on": ["build"],
                        "timeout_seconds": 120,
                    },
                    {
                        "name": "package",
                        "command": [sys.executable, "-c", "print(3)"],
                        "depends_on": ["test"],
                    },
                ],
            }
        ),
    )

    try:
        yield database, factory, repository_id, stage_pid_file
    finally:
        if stage_pid_file.exists():
            # A killed worker cannot stop its stage, so the test does.
            try:
                os.killpg(int(stage_pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, ValueError):
                pass

        engine.dispose()


def stage_states(
    factory: SessionFactory,
    run_id: UUID,
) -> dict[str, tuple[str, list[tuple[str, str | None]]]]:
    with factory() as session:
        stages = session.scalars(
            select(StageRunRecord).where(StageRunRecord.pipeline_run_id == run_id)
        ).all()

        return {
            stage.stage_name: (
                stage.status,
                [
                    (attempt.status, attempt.error_message)
                    for attempt in session.scalars(
                        select(StageAttemptRecord)
                        .where(StageAttemptRecord.stage_run_id == stage.id)
                        .order_by(StageAttemptRecord.attempt_number)
                    )
                ],
            )
            for stage in stages
        }


def test_sigkilled_worker_is_reconciled_without_replay(
    crash_setup: tuple[Path, SessionFactory, UUID, Path],
) -> None:
    database, factory, repository_id, stage_pid_file = crash_setup
    queue = WorkerQueue(factory)
    job = queue.enqueue_version(repository_id, "quality", 1, approved=True)

    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "buildscope.workers.worker",
            "--database",
            str(database),
            "--drain",
            "--lease-seconds",
            str(LEASE_SECONDS),
            "--heartbeat-interval",
            "0.3",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for(stage_pid_file.exists, timeout=30, what="long stage to start")

        running = queue.get_job(job.id)
        assert running.status == RunStatus.RUNNING
        assert running.pipeline_run_id is not None
        run_id = running.pipeline_run_id

        before = stage_states(factory, run_id)
        assert before["build"][0] == RunStatus.SUCCEEDED.value
        assert before["test"] == (RunStatus.RUNNING.value, [("RUNNING", None)])

    finally:
        worker.send_signal(signal.SIGKILL)
        worker.wait(timeout=10)

    assert worker.returncode == -signal.SIGKILL

    # Recovery must refuse while the lease is still valid.
    assert queue.recover_stale(stale_after_seconds=LEASE_SECONDS) == []

    recovered: list[UUID] = []

    def recover() -> bool:
        recovered.extend(queue.recover_stale(stale_after_seconds=LEASE_SECONDS))
        return bool(recovered)

    wait_for(recover, timeout=LEASE_SECONDS * 4, what="lease expiry")

    assert recovered == [job.id]

    finished = queue.get_job(job.id)
    assert finished.status == RunStatus.FAILED
    assert finished.lease_expires_at is None
    assert finished.error_message is not None

    with factory() as session:
        run = session.get(PipelineRunRecord, run_id)
        assert run is not None
        assert run.status == RunStatus.FAILED.value
        assert run.finished_at is not None

    after = stage_states(factory, run_id)
    assert after["build"] == (RunStatus.SUCCEEDED.value, [("SUCCEEDED", None)])

    test_status, test_attempts = after["test"]
    assert test_status == RunStatus.FAILED.value
    assert len(test_attempts) == 1
    assert test_attempts[0][0] == RunStatus.FAILED.value
    assert test_attempts[0][1] is not None
    assert "not replayed" in test_attempts[0][1]

    assert after["package"] == (RunStatus.BLOCKED.value, [])

    # The abandoned job must never be handed to another worker.
    assert queue.claim_next("replacement-worker") is None
