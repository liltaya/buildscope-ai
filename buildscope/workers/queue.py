"""Atomically enqueue and claim approved local pipeline jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import aliased

from buildscope.database.session import SessionFactory
from buildscope.pipelines.graph import build_pipeline_plan
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import PipelineRunRecord
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus, assert_transition
from buildscope.workers.models import WorkerJobRecord


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
    error_message: str | None


class WorkerQueue:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._factory = session_factory

    def enqueue_version(
        self, repository_id: UUID, name: str, version: int, *, approved: bool
    ) -> JobDetails:
        if not approved:
            raise ExecutionApprovalRequiredError(
                "Review commands and explicitly approve execution before enqueuing."
            )
        saved = PipelineService(self._factory).get_version(repository_id, name, version)
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
            row = session.get(WorkerJobRecord, job_id)
            if row is None:
                raise JobNotFoundError(f"Worker job not found: {job_id}")
            definition = session.get(PipelineDefinitionRecord, row.pipeline_definition_id)
            if definition is None:
                raise RuntimeError("Job references a missing pipeline definition.")
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
                error_message=row.error_message,
            )

    def claim_next(self, worker_id: str) -> ClaimedJob | None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("worker_id must contain 1-128 characters.")
        active = aliased(WorkerJobRecord)
        active_for_same_repo = (
            select(active.id)
            .where(
                active.repository_id == WorkerJobRecord.repository_id,
                active.status == RunStatus.RUNNING.value,
            )
            .exists()
        )
        next_id = (
            select(WorkerJobRecord.id)
            .where(WorkerJobRecord.status == RunStatus.QUEUED.value, ~active_for_same_repo)
            .order_by(WorkerJobRecord.queued_at, WorkerJobRecord.id)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(WorkerJobRecord)
            .where(WorkerJobRecord.id == next_id, WorkerJobRecord.status == RunStatus.QUEUED.value)
            .values(
                status=RunStatus.RUNNING.value, worker_id=worker_id, started_at=datetime.now(UTC)
            )
            .returning(WorkerJobRecord.id)
        )
        with self._factory.begin() as session:
            claimed_id = session.execute(statement).scalar_one_or_none()
            if claimed_id is None:
                return None
            row = session.get(WorkerJobRecord, claimed_id)
            assert row is not None
            definition = session.get(PipelineDefinitionRecord, row.pipeline_definition_id)
            assert definition is not None
            return ClaimedJob(
                id=row.id,
                repository_id=row.repository_id,
                pipeline_name=definition.name,
                version=definition.version,
            )

    def complete(
        self,
        job_id: UUID,
        worker_id: str,
        status: RunStatus,
        *,
        pipeline_run_id: UUID | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.TIMED_OUT,
            RunStatus.CANCELLED,
        }:
            raise ValueError("Job must finish in a terminal execution status.")
        with self._factory.begin() as session:
            row = session.get(WorkerJobRecord, job_id)
            if row is None:
                raise JobNotFoundError(str(job_id))
            if row.worker_id != worker_id or row.status != RunStatus.RUNNING.value:
                raise JobOwnershipError("Only the worker owning a RUNNING job may finish it.")
            if pipeline_run_id is not None:
                run = session.get(PipelineRunRecord, pipeline_run_id)
                if (
                    run is None
                    or run.repository_id != row.repository_id
                    or run.pipeline_definition_id != row.pipeline_definition_id
                ):
                    raise JobOwnershipError("Pipeline run does not match job's saved definition.")
            assert_transition(RunStatus(row.status), status)
            row.status = status.value
            row.finished_at = datetime.now(UTC)
            row.pipeline_run_id = pipeline_run_id
            row.error_message = error_message[:2000] if error_message is not None else None
