"""Sequential execution of a stored, validated pipeline versions"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import UUID

from sqlalchemy import select

from buildscope.database.session import SessionFactory
from buildscope.execution import (
    ExecutionCancelledError,
    ExecutionError,
    ExecutionRequest,
    ExecutionTimeoutError,
    execute_command,
)
from buildscope.pipelines.graph import build_pipeline_plan
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import (
    RunStatus,
    assert_transition,
)
from buildscope.repositories.errors import (
    RepositoryNotFoundError,
)
from buildscope.repositories.models import Repository
from buildscope.repositories.paths import (
    validate_repository_path,
)


class ExecutionApprovalRequiredError(PermissionError):
    """Running local project commands requires explicit approval."""


class PipelineRunNotFoundError(LookupError):
    """The requested pipeline execution was not found."""


class PipelineRunnerError(RuntimeError):
    """The runner suffered an unexpected infrastructure error."""


@dataclass(frozen=True, slots=True)
class StageRunDetails:
    """Persisted outcome of a single stage."""

    id: UUID
    stage_name: str
    status: RunStatus
    returncode: int | None
    stdout_path: str | None
    stderr_path: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class PipelineRunDetails:
    """Persisted pipeline execution and its stages."""

    id: UUID
    repository_id: UUID
    pipeline_definition_id: UUID
    status: RunStatus
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stages: tuple[StageRunDetails, ...]


class PipelineRunner:
    """Run approved stages in DAG order, persisting every state change."""

    def __init__(
        self,
        session_factory: SessionFactory,
    ) -> None:
        self._factory = session_factory

    def get_run(
        self,
        run_id: UUID,
    ) -> PipelineRunDetails:
        """Read the persisted run and its stage statuses."""
        with self._factory() as session:
            record = session.get(
                PipelineRunRecord,
                run_id,
            )

            if record is None:
                raise PipelineRunNotFoundError(f"Pipeline run not found: {run_id}")

            rows = session.scalars(
                select(StageRunRecord)
                .where(StageRunRecord.pipeline_run_id == run_id)
                .order_by(StageRunRecord.position)
            ).all()

            return PipelineRunDetails(
                id=record.id,
                repository_id=record.repository_id,
                pipeline_definition_id=record.pipeline_definition_id,
                status=RunStatus(record.status),
                queued_at=record.queued_at,
                started_at=record.started_at,
                finished_at=record.finished_at,
                stages=tuple(
                    StageRunDetails(
                        id=row.id,
                        stage_name=row.stage_name,
                        status=RunStatus(row.status),
                        returncode=row.exit_code,
                        stdout_path=row.stdout_path,
                        stderr_path=row.stderr_path,
                        error_message=row.error_message,
                        started_at=row.started_at,
                        finished_at=row.finished_at,
                    )
                    for row in rows
                ),
            )

    def _update_run(
        self,
        run_id: UUID,
        status: RunStatus,
    ) -> None:
        """Transition a pipeline run and commit the new state."""
        with self._factory.begin() as session:
            record = session.get(
                PipelineRunRecord,
                run_id,
            )

            if record is None:
                raise PipelineRunNotFoundError(str(run_id))

            assert_transition(
                RunStatus(record.status),
                status,
            )

            record.status = status.value

            if status == RunStatus.RUNNING:
                record.started_at = datetime.now(UTC)
            else:
                record.finished_at = datetime.now(UTC)

    def _update_stage(
        self,
        stage_id: UUID,
        status: RunStatus,
        *,
        returncode: int | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
        error_message: str | None = None,
    ) -> None:
        """Transition a stage and persist its execution details."""
        with self._factory.begin() as session:
            record = session.get(
                StageRunRecord,
                stage_id,
            )

            if record is None:
                raise PipelineRunnerError(f"Missing stage record: {stage_id}")

            assert_transition(
                RunStatus(record.status),
                status,
            )

            record.status = status.value

            if status == RunStatus.RUNNING:
                record.started_at = datetime.now(UTC)

            else:
                record.finished_at = datetime.now(UTC)
                record.exit_code = returncode

                record.stdout_path = str(stdout_path) if stdout_path is not None else None

                record.stderr_path = str(stderr_path) if stderr_path is not None else None

                record.error_message = error_message

    def run_version(
        self,
        repository_id: UUID,
        pipeline_name: str,
        version: int,
        *,
        approved: bool,
        cancel_event: Event | None = None,
        log_directory: Path | None = None,
    ) -> PipelineRunDetails:
        """Execute a stored pipeline sequentially after explicit approval."""
        if not approved:
            raise ExecutionApprovalRequiredError(
                "Review the pipeline commands and pass approved=True explicitly."
            )

        saved = PipelineService(self._factory).get_version(
            repository_id,
            pipeline_name,
            version,
        )

        plan = build_pipeline_plan(saved.definition)

        with self._factory() as session:
            repository = session.get(
                Repository,
                repository_id,
            )

            if repository is None:
                raise RepositoryNotFoundError(str(repository_id))

            root = Path(validate_repository_path(repository.local_path))

        stage_ids: dict[str, UUID] = {}

        with self._factory.begin() as session:
            run = PipelineRunRecord(
                repository_id=repository_id,
                pipeline_definition_id=saved.id,
                status=RunStatus.QUEUED.value,
                queued_at=datetime.now(UTC),
            )

            session.add(run)
            session.flush()

            run_id = run.id

            for position, stage in enumerate(plan.ordered_stages):
                row = StageRunRecord(
                    pipeline_run_id=run_id,
                    stage_name=stage.name,
                    position=position,
                    command=list(stage.command),
                    status=RunStatus.QUEUED.value,
                )

                session.add(row)
                session.flush()

                stage_ids[stage.name] = row.id

        self._update_run(
            run_id,
            RunStatus.RUNNING,
        )

        statuses: dict[str, RunStatus] = {}
        cancelled = False
        unexpected: Exception | None = None

        for stage in plan.ordered_stages:
            stage_id = stage_ids[stage.name]

            if cancelled or (cancel_event is not None and cancel_event.is_set()):
                cancelled = True

                self._update_stage(
                    stage_id,
                    RunStatus.CANCELLED,
                    error_message="Run cancelled.",
                )

                statuses[stage.name] = RunStatus.CANCELLED
                continue

            if unexpected is not None or any(
                statuses[dependency] != RunStatus.SUCCEEDED for dependency in stage.depends_on
            ):
                self._update_stage(
                    stage_id,
                    RunStatus.BLOCKED,
                    error_message=("Prerequisite stage did not succeed."),
                )

                statuses[stage.name] = RunStatus.BLOCKED
                continue

            self._update_stage(
                stage_id,
                RunStatus.RUNNING,
            )

            request = ExecutionRequest(
                command=stage.command,
                repository_root=root,
                timeout_seconds=stage.timeout_seconds,
                log_directory=log_directory,
            )

            try:
                result = execute_command(
                    request,
                    cancel_event=cancel_event,
                )

            except ExecutionTimeoutError as exc:
                state = RunStatus.TIMED_OUT

                self._update_stage(
                    stage_id,
                    state,
                    stdout_path=exc.stdout_path,
                    stderr_path=exc.stderr_path,
                    error_message=str(exc),
                )

            except ExecutionCancelledError as exc:
                state = RunStatus.CANCELLED
                cancelled = True

                self._update_stage(
                    stage_id,
                    state,
                    stdout_path=exc.stdout_path,
                    stderr_path=exc.stderr_path,
                    error_message=str(exc),
                )

            except ExecutionError as exc:
                state = RunStatus.FAILED

                self._update_stage(
                    stage_id,
                    state,
                    error_message=str(exc),
                )

            except Exception as exc:
                state = RunStatus.FAILED
                unexpected = exc

                self._update_stage(
                    stage_id,
                    state,
                    error_message=(f"Unexpected runner error: {exc}"),
                )

            else:
                state = RunStatus.SUCCEEDED if result.succeeded else RunStatus.FAILED

                self._update_stage(
                    stage_id,
                    state,
                    returncode=result.returncode,
                    stdout_path=result.stdout_path,
                    stderr_path=result.stderr_path,
                )

            statuses[stage.name] = state

        # Derive the overall outcome from recorded stage outcomes.
        if cancelled:
            final = RunStatus.CANCELLED

        elif any(value == RunStatus.TIMED_OUT for value in statuses.values()):
            final = RunStatus.TIMED_OUT

        elif all(value == RunStatus.SUCCEEDED for value in statuses.values()):
            final = RunStatus.SUCCEEDED

        else:
            final = RunStatus.FAILED

        self._update_run(
            run_id,
            final,
        )

        if unexpected is not None:
            raise PipelineRunnerError(
                f"Unexpected execution failure in run {run_id}"
            ) from unexpected

        return self.get_run(run_id)
