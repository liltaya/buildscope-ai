"""SQLite worker queue with renewable, fenced leases and recovery"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, aliased

from buildscope.database.session import SessionFactory
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.graph import build_pipeline_plan
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus, assert_transition
from buildscope.workers.models import (
    WorkerHeartbeatRecord,
    WorkerJobRecord,
)


def retry_sqlite_locks[**P, R](
    func: Callable[P, R],
) -> Callable[P, R]:

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        for attempt in range(12):
            try:
                return func(*args, **kwargs)
            except OperationalError as exc:
                message = str(exc.orig).lower()
                if "locked" not in message or attempt == 11:
                    raise

                time.sleep(min(0.025 * (2**attempt), 0.4))

        raise AssertionError("unreachable")

    return wrapper


class JobNotFoundError(LookupError):
    pass


class JobOwnershipError(RuntimeError):
    pass


class ExecutionApprovalRequiredError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: UUID
    repository_id: UUID
    pipeline_name: str
    version: int
    claim_token: UUID
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class JobDetails:
    id: UUID
    repository_id: UUID
    pipeline_definition_id: UUID
    pipeline_name: str
    version: int
    status: RunStatus
    worker_id: str | None
    pipeline_run_id: UUID | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    lease_expires_at: datetime | None
    error_message: str | None


def _pid_alive(pid: int) -> bool:
    """Best-effort local PID check."""
    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
        return True

    except ProcessLookupError:
        return False

    except PermissionError:
        return True


class WorkerQueue:
    def __init__(
        self,
        session_factory: SessionFactory,
    ) -> None:
        self._factory = session_factory

    @staticmethod
    def _lease_seconds(seconds: int) -> None:
        if isinstance(seconds, bool) or not 3 <= seconds <= 3600:
            raise ValueError("lease_seconds must be between 3 and 3600")

    @staticmethod
    def _validate_worker(worker_id: str) -> None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("worker_id must have 1-128 characters")

    @staticmethod
    def _owns(
        row: WorkerJobRecord | None,
        worker_id: str,
        token: UUID,
        now: datetime,
    ) -> bool:
        return bool(
            row is not None
            and row.status == RunStatus.RUNNING.value
            and row.worker_id == worker_id
            and row.claim_token == token
            and row.lease_expires_at is not None
            and row.lease_expires_at > now
        )

    @retry_sqlite_locks
    def register_worker(
        self,
        worker_id: str,
        *,
        pid: int | None = None,
    ) -> None:
        self._validate_worker(worker_id)

        actual_pid = os.getpid() if pid is None else pid

        if actual_pid <= 0:
            raise ValueError("pid must be positive")

        now = datetime.now(UTC)

        with self._factory.begin() as session:
            row = session.get(
                WorkerHeartbeatRecord,
                worker_id,
            )

            if row is None:
                session.add(
                    WorkerHeartbeatRecord(
                        worker_id=worker_id,
                        pid=actual_pid,
                        status="ACTIVE",
                        started_at=now,
                        last_seen_at=now,
                    )
                )

            elif row.status == "ACTIVE" and row.pid != actual_pid:
                raise JobOwnershipError("Worker identifier is already active for another PID")

            else:
                row.pid = actual_pid
                row.status = "ACTIVE"
                row.stopped_at = None
                row.last_seen_at = now

    def stop_worker(self, worker_id: str) -> None:
        with self._factory.begin() as session:
            row = session.get(
                WorkerHeartbeatRecord,
                worker_id,
            )

            if row is not None:
                row.status = "STOPPED"
                row.stopped_at = datetime.now(UTC)

    def enqueue_version(
        self,
        repository_id: UUID,
        name: str,
        version: int,
        *,
        approved: bool,
    ) -> JobDetails:
        if not approved:
            raise ExecutionApprovalRequiredError("Review commands and approve before enqueuing.")

        saved = PipelineService(self._factory).get_version(
            repository_id,
            name,
            version,
        )

        build_pipeline_plan(saved.definition)

        with self._factory.begin() as session:
            row = WorkerJobRecord(
                repository_id=repository_id,
                pipeline_definition_id=saved.id,
                status=RunStatus.QUEUED.value,
            )

            session.add(row)
            session.flush()
            job_id = row.id

        return self.get_job(job_id)

    def get_job(self, job_id: UUID) -> JobDetails:
        with self._factory() as session:
            row = session.get(
                WorkerJobRecord,
                job_id,
            )

            if row is None:
                raise JobNotFoundError(str(job_id))

            definition = session.get(
                PipelineDefinitionRecord,
                row.pipeline_definition_id,
            )

            if definition is None:
                raise RuntimeError("Job references a missing pipeline definition")

            return JobDetails(
                id=row.id,
                repository_id=row.repository_id,
                pipeline_definition_id=row.pipeline_definition_id,
                pipeline_name=definition.name,
                version=definition.version,
                status=RunStatus(row.status),
                worker_id=row.worker_id,
                pipeline_run_id=row.pipeline_run_id,
                queued_at=row.queued_at,
                started_at=row.started_at,
                finished_at=row.finished_at,
                lease_expires_at=row.lease_expires_at,
                error_message=row.error_message,
            )

    @retry_sqlite_locks
    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 15,
    ) -> ClaimedJob | None:
        """Atomically claim one eligible job."""
        self._validate_worker(worker_id)
        self._lease_seconds(lease_seconds)

        # Also supports the Part 18 claim_next() callers.
        self.register_worker(worker_id)

        now = datetime.now(UTC)
        token = uuid4()
        expiry = now + timedelta(seconds=lease_seconds)

        active = aliased(WorkerJobRecord)

        repo_busy = (
            select(active.id)
            .where(
                active.repository_id == WorkerJobRecord.repository_id,
                active.status == RunStatus.RUNNING.value,
            )
            .exists()
        )

        next_id = (
            select(WorkerJobRecord.id)
            .where(
                WorkerJobRecord.status == RunStatus.QUEUED.value,
                ~repo_busy,
            )
            .order_by(
                WorkerJobRecord.queued_at,
                WorkerJobRecord.id,
            )
            .limit(1)
            .scalar_subquery()
        )

        statement = (
            update(WorkerJobRecord)
            .where(
                WorkerJobRecord.id == next_id,
                WorkerJobRecord.status == RunStatus.QUEUED.value,
            )
            .values(
                status=RunStatus.RUNNING.value,
                worker_id=worker_id,
                claim_token=token,
                lease_expires_at=expiry,
                started_at=now,
            )
            .returning(WorkerJobRecord.id)
        )

        with self._factory.begin() as session:
            claimed_id = session.execute(statement).scalar_one_or_none()

            if claimed_id is None:
                return None

            row = session.get(
                WorkerJobRecord,
                claimed_id,
            )

            assert row is not None

            definition = session.get(
                PipelineDefinitionRecord,
                row.pipeline_definition_id,
            )

            assert definition is not None

            return ClaimedJob(
                row.id,
                row.repository_id,
                definition.name,
                definition.version,
                token,
                expiry,
            )

    def heartbeat(
        self,
        worker_id: str,
        *,
        job_id: UUID | None = None,
        claim_token: UUID | None = None,
        lease_seconds: int = 15,
    ) -> None:
        """Record worker liveness and extend an owned lease."""
        self._lease_seconds(lease_seconds)

        now = datetime.now(UTC)

        with self._factory.begin() as session:
            worker = session.get(
                WorkerHeartbeatRecord,
                worker_id,
            )

            if worker is None or worker.status != "ACTIVE":
                raise JobOwnershipError("Worker is not registered as active")

            if job_id is not None:
                if claim_token is None:
                    raise JobOwnershipError("Claim token is required")

                job = session.get(
                    WorkerJobRecord,
                    job_id,
                )

                if not self._owns(
                    job,
                    worker_id,
                    claim_token,
                    now,
                ):
                    raise JobOwnershipError("Lease is lost, expired, or owned by another worker")

                assert job is not None

                job.lease_expires_at = now + timedelta(seconds=lease_seconds)

            worker.last_seen_at = now

    def attach_run(
        self,
        job_id: UUID,
        worker_id: str,
        claim_token: UUID,
        run_id: UUID,
    ) -> None:
        """Link the pipeline run to its claimed job."""
        now = datetime.now(UTC)

        with self._factory.begin() as session:
            job = session.get(
                WorkerJobRecord,
                job_id,
            )

            if not self._owns(
                job,
                worker_id,
                claim_token,
                now,
            ):
                raise JobOwnershipError("Cannot attach run after ownership was lost")

            assert job is not None

            run = session.get(
                PipelineRunRecord,
                run_id,
            )

            if (
                run is None
                or run.repository_id != job.repository_id
                or run.pipeline_definition_id != job.pipeline_definition_id
            ):
                raise JobOwnershipError("Run does not match this job")

            if job.pipeline_run_id is not None and job.pipeline_run_id != run_id:
                raise JobOwnershipError("Job already has a different pipeline run")

            job.pipeline_run_id = run_id

    def complete(
        self,
        job_id: UUID,
        worker_id: str,
        status: RunStatus,
        *,
        claim_token: UUID,
        pipeline_run_id: UUID | None = None,
        error_message: str | None = None,
    ) -> None:
        """Finish a job only while its claim is still valid."""
        if status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.TIMED_OUT,
            RunStatus.CANCELLED,
        }:
            raise ValueError("Job requires a terminal status")

        now = datetime.now(UTC)

        with self._factory.begin() as session:
            job = session.get(
                WorkerJobRecord,
                job_id,
            )

            if not self._owns(
                job,
                worker_id,
                claim_token,
                now,
            ):
                raise JobOwnershipError(
                    "Only the current, unexpired lease owner may finish this job"
                )

            assert job is not None

            if pipeline_run_id is not None:
                run = session.get(
                    PipelineRunRecord,
                    pipeline_run_id,
                )

                if (
                    run is None
                    or run.repository_id != job.repository_id
                    or run.pipeline_definition_id != job.pipeline_definition_id
                ):
                    raise JobOwnershipError("Run does not match this job")

                if job.pipeline_run_id is not None and job.pipeline_run_id != pipeline_run_id:
                    raise JobOwnershipError("Job is linked to another run")

                job.pipeline_run_id = pipeline_run_id

            assert_transition(
                RunStatus(job.status),
                status,
            )

            job.status = status.value
            job.finished_at = now
            job.error_message = error_message[:2000] if error_message else None
            job.lease_expires_at = None

    def recover_stale(
        self,
        *,
        stale_after_seconds: int = 30,
    ) -> list[UUID]:
        """Mark abandoned local jobs terminal; never replay."""
        if isinstance(stale_after_seconds, bool) or not 3 <= stale_after_seconds <= 86400:
            raise ValueError("stale_after_seconds must be between 3 and 86400")

        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=stale_after_seconds)

        recovered: list[UUID] = []

        with self._factory.begin() as session:
            jobs = session.scalars(
                select(WorkerJobRecord)
                .where(
                    WorkerJobRecord.status == RunStatus.RUNNING.value,
                    or_(
                        WorkerJobRecord.lease_expires_at.is_(None),
                        WorkerJobRecord.lease_expires_at <= now,
                    ),
                )
                .order_by(WorkerJobRecord.queued_at)
            ).all()

            for job in jobs:
                worker = (
                    session.get(
                        WorkerHeartbeatRecord,
                        job.worker_id,
                    )
                    if job.worker_id
                    else None
                )

                if worker is not None:
                    # Require stale heartbeat AND dead PID.
                    if worker.status == "ACTIVE" and worker.last_seen_at > cutoff:
                        continue

                    if _pid_alive(worker.pid):
                        continue

                run = (
                    session.get(
                        PipelineRunRecord,
                        job.pipeline_run_id,
                    )
                    if job.pipeline_run_id
                    else None
                )

                if run is not None and RunStatus(run.status) in {
                    RunStatus.SUCCEEDED,
                    RunStatus.FAILED,
                    RunStatus.TIMED_OUT,
                    RunStatus.CANCELLED,
                }:
                    terminal = RunStatus(run.status)
                    reason = "Recovered completed run after worker exited before finalizing job"

                else:
                    terminal = RunStatus.FAILED
                    reason = (
                        "Worker lease expired and worker "
                        "process is not alive; execution "
                        "was not replayed"
                    )

                    if run is not None:
                        self._reconcile_run(
                            session,
                            run,
                            now,
                            reason,
                        )

                assert_transition(
                    RunStatus(job.status),
                    terminal,
                )

                job.status = terminal.value
                job.finished_at = now
                job.lease_expires_at = None
                job.error_message = reason

                if worker is not None:
                    worker.status = "STOPPED"
                    worker.stopped_at = now

                recovered.append(job.id)

        return recovered

    @staticmethod
    def _reconcile_run(
        session: Session,
        run: PipelineRunRecord,
        now: datetime,
        reason: str,
    ) -> None:
        """Reconcile dangling records from an abandoned run."""
        attempts = session.scalars(
            select(StageAttemptRecord)
            .join(
                StageRunRecord,
                StageAttemptRecord.stage_run_id == StageRunRecord.id,
            )
            .where(StageRunRecord.pipeline_run_id == run.id)
        ).all()

        for attempt in attempts:
            current = RunStatus(attempt.status)

            target = (
                RunStatus.FAILED
                if current == RunStatus.RUNNING
                else (RunStatus.CANCELLED if current == RunStatus.QUEUED else None)
            )

            if target is not None:
                assert_transition(current, target)
                attempt.status = target.value
                attempt.finished_at = now
                attempt.error_message = reason

        stages = session.scalars(
            select(StageRunRecord).where(StageRunRecord.pipeline_run_id == run.id)
        ).all()

        for stage in stages:
            current = RunStatus(stage.status)

            target = (
                RunStatus.FAILED
                if current == RunStatus.RUNNING
                else (RunStatus.BLOCKED if current == RunStatus.QUEUED else None)
            )

            if target is not None:
                assert_transition(current, target)
                stage.status = target.value
                stage.finished_at = now
                stage.error_message = reason

        current_run = RunStatus(run.status)

        if current_run == RunStatus.RUNNING:
            assert_transition(
                current_run,
                RunStatus.FAILED,
            )
            run.status = RunStatus.FAILED.value
            run.finished_at = now

        elif current_run == RunStatus.QUEUED:
            assert_transition(
                current_run,
                RunStatus.CANCELLED,
            )
            run.status = RunStatus.CANCELLED.value
            run.finished_at = now
