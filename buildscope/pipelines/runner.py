"""Sequential pipeline execution with durable per-attempt retry history"""

from __future__ import annotations

from collections.abc import Callable
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
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.graph import build_pipeline_plan
from buildscope.pipelines.run_models import PipelineRunRecord, StageRunRecord
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus, assert_transition
from buildscope.repositories.errors import RepositoryNotFoundError
from buildscope.repositories.models import Repository
from buildscope.repositories.paths import validate_repository_path


class ExecutionApprovalRequiredError(PermissionError):
    """A local user must explicitly authorize executing this pipeline."""


class PipelineRunNotFoundError(LookupError):
    """A requested run was not found."""


class PipelineRunnerError(RuntimeError):
    """Unexpected execution infrastructure error."""


@dataclass(frozen=True, slots=True)
class StageAttemptDetails:
    id: UUID
    attempt_number: int
    status: RunStatus
    returncode: int | None
    stdout_path: str | None
    stderr_path: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class StageRunDetails:
    id: UUID
    stage_name: str
    status: RunStatus
    returncode: int | None
    stdout_path: str | None
    stderr_path: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    attempts: tuple[StageAttemptDetails, ...]

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)


@dataclass(frozen=True, slots=True)
class PipelineRunDetails:
    id: UUID
    repository_id: UUID
    pipeline_definition_id: UUID
    status: RunStatus
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stages: tuple[StageRunDetails, ...]


class PipelineRunner:
    """Execute approved pipeline stages sequentially with retry persistence."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._factory = session_factory

    def get_run(self, run_id: UUID) -> PipelineRunDetails:
        """Load a run with its stages and individual attempt history."""
        with self._factory() as session:
            record = session.get(PipelineRunRecord, run_id)

            if record is None:
                raise PipelineRunNotFoundError(f"Pipeline run not found: {run_id}")

            stages = list(
                session.scalars(
                    select(StageRunRecord)
                    .where(StageRunRecord.pipeline_run_id == run_id)
                    .order_by(StageRunRecord.position)
                ).all()
            )

            stage_ids = [stage.id for stage in stages]

            attempt_rows = (
                list(
                    session.scalars(
                        select(StageAttemptRecord)
                        .where(StageAttemptRecord.stage_run_id.in_(stage_ids))
                        .order_by(StageAttemptRecord.attempt_number)
                    ).all()
                )
                if stage_ids
                else []
            )

            attempts_by_stage: dict[UUID, list[StageAttemptDetails]] = {
                stage.id: [] for stage in stages
            }

            for row in attempt_rows:
                attempts_by_stage[row.stage_run_id].append(
                    StageAttemptDetails(
                        id=row.id,
                        attempt_number=row.attempt_number,
                        status=RunStatus(row.status),
                        returncode=row.exit_code,
                        stdout_path=row.stdout_path,
                        stderr_path=row.stderr_path,
                        error_message=row.error_message,
                        started_at=row.started_at,
                        finished_at=row.finished_at,
                    )
                )

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
                        id=stage.id,
                        stage_name=stage.stage_name,
                        status=RunStatus(stage.status),
                        returncode=stage.exit_code,
                        stdout_path=stage.stdout_path,
                        stderr_path=stage.stderr_path,
                        error_message=stage.error_message,
                        started_at=stage.started_at,
                        finished_at=stage.finished_at,
                        attempts=tuple(attempts_by_stage[stage.id]),
                    )
                    for stage in stages
                ),
            )

    def _update_run(
        self,
        run_id: UUID,
        status: RunStatus,
    ) -> None:
        """Persist a legal pipeline-level state transition."""
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
        """Persist a stage transition without changing previous attempts."""
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

    def _start_attempt(
        self,
        stage_id: UUID,
        attempt_number: int,
    ) -> UUID:
        """Create and start one durable attempt record."""
        with self._factory.begin() as session:
            record = StageAttemptRecord(
                stage_run_id=stage_id,
                attempt_number=attempt_number,
                status=RunStatus.QUEUED.value,
            )

            session.add(record)
            session.flush()
            attempt_id = record.id

        with self._factory.begin() as session:
            attempt_record = session.get(
                StageAttemptRecord,
                attempt_id,
            )

            if attempt_record is None:
                raise PipelineRunnerError(f"Missing attempt record: {attempt_id}")

            assert_transition(
                RunStatus(attempt_record.status),
                RunStatus.RUNNING,
            )

            attempt_record.status = RunStatus.RUNNING.value
            attempt_record.started_at = datetime.now(UTC)

        return attempt_id

    def _complete_attempt(
        self,
        attempt_id: UUID,
        status: RunStatus,
        *,
        stage_status: RunStatus | None = None,
        returncode: int | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
        error_message: str | None = None,
    ) -> None:
        """Complete an attempt and optionally finalize the stage atomically."""
        with self._factory.begin() as session:
            attempt = session.get(
                StageAttemptRecord,
                attempt_id,
            )

            if attempt is None:
                raise PipelineRunnerError(f"Missing attempt record: {attempt_id}")

            assert_transition(
                RunStatus(attempt.status),
                status,
            )

            attempt.status = status.value
            attempt.finished_at = datetime.now(UTC)
            attempt.exit_code = returncode
            attempt.stdout_path = str(stdout_path) if stdout_path is not None else None
            attempt.stderr_path = str(stderr_path) if stderr_path is not None else None
            attempt.error_message = error_message

            # An intermediate failed attempt does not finish the stage.
            if stage_status is not None:
                stage = session.get(
                    StageRunRecord,
                    attempt.stage_run_id,
                )

                if stage is None:
                    raise PipelineRunnerError(f"Missing stage record: {attempt.stage_run_id}")

                assert_transition(
                    RunStatus(stage.status),
                    stage_status,
                )

                stage.status = stage_status.value
                stage.finished_at = datetime.now(UTC)
                stage.exit_code = returncode
                stage.stdout_path = attempt.stdout_path
                stage.stderr_path = attempt.stderr_path
                stage.error_message = error_message

    def _run_stage(
        self,
        stage_id: UUID,
        command: tuple[str, ...],
        root: Path,
        timeout_seconds: int,
        retry_count: int,
        cancel_event: Event | None,
        log_directory: Path | None,
    ) -> RunStatus:
        """Execute the allowed attempts for one stage."""
        self._update_stage(
            stage_id,
            RunStatus.RUNNING,
        )

        total_attempts = retry_count + 1

        for number in range(1, total_attempts + 1):
            # Never start another attempt after cancellation.
            if cancel_event is not None and cancel_event.is_set():
                self._update_stage(
                    stage_id,
                    RunStatus.CANCELLED,
                    error_message="Run cancelled before next attempt.",
                )
                return RunStatus.CANCELLED

            attempt_id = self._start_attempt(
                stage_id,
                number,
            )

            request = ExecutionRequest(
                command=command,
                repository_root=root,
                timeout_seconds=timeout_seconds,
                log_directory=log_directory,
            )

            try:
                result = execute_command(
                    request,
                    cancel_event=cancel_event,
                )

            except ExecutionCancelledError as exc:
                self._complete_attempt(
                    attempt_id,
                    RunStatus.CANCELLED,
                    stage_status=RunStatus.CANCELLED,
                    stdout_path=exc.stdout_path,
                    stderr_path=exc.stderr_path,
                    error_message=str(exc),
                )

                return RunStatus.CANCELLED

            except ExecutionTimeoutError as exc:
                interrupted = cancel_event is not None and cancel_event.is_set()

                should_retry = number < total_attempts and not interrupted

                self._complete_attempt(
                    attempt_id,
                    RunStatus.TIMED_OUT,
                    stage_status=(
                        None
                        if should_retry
                        else (RunStatus.CANCELLED if interrupted else RunStatus.TIMED_OUT)
                    ),
                    stdout_path=exc.stdout_path,
                    stderr_path=exc.stderr_path,
                    error_message=str(exc),
                )

                if should_retry:
                    continue

                return RunStatus.CANCELLED if interrupted else RunStatus.TIMED_OUT

            except ExecutionError as exc:
                # Infrastructure failures are not retried.
                self._complete_attempt(
                    attempt_id,
                    RunStatus.FAILED,
                    stage_status=RunStatus.FAILED,
                    error_message=str(exc),
                )

                return RunStatus.FAILED

            except Exception as exc:
                self._complete_attempt(
                    attempt_id,
                    RunStatus.FAILED,
                    stage_status=RunStatus.FAILED,
                    error_message=f"Unexpected runner error: {exc}",
                )

                raise PipelineRunnerError(f"Unexpected failure executing stage: {exc}") from exc

            else:
                succeeded = result.succeeded

                interrupted = cancel_event is not None and cancel_event.is_set()

                should_retry = not succeeded and number < total_attempts and not interrupted

                stage_status = (
                    RunStatus.SUCCEEDED
                    if succeeded
                    else (RunStatus.CANCELLED if interrupted else RunStatus.FAILED)
                )

                self._complete_attempt(
                    attempt_id,
                    (RunStatus.SUCCEEDED if succeeded else RunStatus.FAILED),
                    stage_status=(None if should_retry else stage_status),
                    returncode=result.returncode,
                    stdout_path=result.stdout_path,
                    stderr_path=result.stderr_path,
                )

                if should_retry:
                    continue

                return stage_status

        raise AssertionError("Attempt loop exhausted without a terminal state.")

    def run_version(
        self,
        repository_id: UUID,
        pipeline_name: str,
        version: int,
        *,
        approved: bool,
        cancel_event: Event | None = None,
        log_directory: Path | None = None,
        on_run_created: Callable[[UUID], None] | None = None,
    ) -> PipelineRunDetails:
        """Execute a saved pipeline using its configured retry policies."""
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

        # Save the pipeline and its stages before execution.
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
        if on_run_created is not None:
            on_run_created(run_id)

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

            # Dependents wait for final success, including all retries.
            if unexpected is not None or any(
                statuses[dependency] != RunStatus.SUCCEEDED for dependency in stage.depends_on
            ):
                self._update_stage(
                    stage_id,
                    RunStatus.BLOCKED,
                    error_message="Prerequisite stage did not succeed.",
                )

                statuses[stage.name] = RunStatus.BLOCKED
                continue

            try:
                status = self._run_stage(
                    stage_id=stage_id,
                    command=stage.command,
                    root=root,
                    timeout_seconds=stage.timeout_seconds,
                    retry_count=stage.retry_count,
                    cancel_event=cancel_event,
                    log_directory=log_directory,
                )

            except PipelineRunnerError as exc:
                unexpected = exc
                status = RunStatus.FAILED

            statuses[stage.name] = status

            if status == RunStatus.CANCELLED:
                cancelled = True

        if cancelled:
            final = RunStatus.CANCELLED

        elif any(status == RunStatus.TIMED_OUT for status in statuses.values()):
            final = RunStatus.TIMED_OUT

        elif all(status == RunStatus.SUCCEEDED for status in statuses.values()):
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
