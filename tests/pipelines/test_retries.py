"""Integration tests for pipeline retries and execution attempt history"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
import yaml
from sqlalchemy.exc import IntegrityError

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.execution import ExecutionRequest, ExecutionResult
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.runner import PipelineRunner
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository

type TestContext = tuple[
    PipelineRunner,
    PipelineService,
    UUID,
    SessionFactory,
]


@pytest.fixture
def context(
    tmp_path: Path,
) -> Iterator[TestContext]:
    database_path = tmp_path / "retries.db"

    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)
    factory = create_session_factory(engine)

    with factory.begin() as session:
        repository = Repository(
            name="Retry Test Project",
            local_path=str(tmp_path),
        )

        session.add(repository)
        session.flush()
        repository_id = repository.id

    yield (
        PipelineRunner(factory),
        PipelineService(factory),
        repository_id,
        factory,
    )

    engine.dispose()


def python_stage(
    name: str,
    script: str,
    *,
    retries: int = 0,
    timeout: int = 5,
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "command": [
            sys.executable,
            "-c",
            script,
        ],
        "retry_count": retries,
        "timeout_seconds": timeout,
        "depends_on": depends_on or [],
    }


def save_pipeline(
    service: PipelineService,
    repository_id: UUID,
    stages: list[dict[str, object]],
) -> None:
    content = yaml.safe_dump(
        {
            "name": "quality",
            "stages": stages,
        }
    )

    service.create_version(
        repository_id,
        content,
    )


def test_failure_then_success(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    counter = tmp_path / "counter"

    script = (
        "from pathlib import Path; import sys; "
        f"p = Path({str(counter)!r}); "
        "n = int(p.read_text()) + 1 if p.exists() else 1; "
        "p.write_text(str(n)); "
        "sys.exit(0 if n == 2 else 3)"
    )

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "tests",
                script,
                retries=2,
            )
        ],
    )

    result = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    stage = result.stages[0]

    assert result.status == RunStatus.SUCCEEDED
    assert stage.status == RunStatus.SUCCEEDED
    assert stage.attempt_count == 2

    assert [attempt.status for attempt in stage.attempts] == [
        RunStatus.FAILED,
        RunStatus.SUCCEEDED,
    ]

    assert [attempt.returncode for attempt in stage.attempts] == [3, 0]

    assert counter.read_text() == "2"

    assert stage.attempts[0].stdout_path != (stage.attempts[1].stdout_path)

    assert runner.get_run(result.id) == result


def test_exhausted_retries_block_dependents(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    marker = tmp_path / "should-not-exist"

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "build",
                "import sys; sys.exit(7)",
                retries=2,
            ),
            python_stage(
                "tests",
                f"open({str(marker)!r}, 'w')",
                depends_on=["build"],
            ),
            python_stage(
                "lint",
                "print('independent stage succeeded')",
            ),
        ],
    )

    result = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    assert result.status == RunStatus.FAILED

    build, tests, lint = result.stages

    assert build.status == RunStatus.FAILED
    assert build.attempt_count == 3

    assert [attempt.returncode for attempt in build.attempts] == [7, 7, 7]

    assert tests.status == RunStatus.BLOCKED
    assert tests.attempt_count == 0
    assert not marker.exists()

    assert lint.status == RunStatus.SUCCEEDED
    assert lint.attempt_count == 1


def test_zero_retries_means_one_attempt(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "build",
                "import sys; sys.exit(2)",
                retries=0,
            )
        ],
    )

    result = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    assert result.status == RunStatus.FAILED
    assert result.stages[0].attempt_count == 1


def test_missing_executable_is_not_retried(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [
            {
                "name": "missing",
                "command": ["__buildscope_missing_program__"],
                "retry_count": 4,
            }
        ],
    )

    result = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    stage = result.stages[0]

    assert stage.status == RunStatus.FAILED
    assert stage.attempt_count == 1
    assert stage.returncode is None
    assert stage.error_message is not None


def test_timeout_then_success(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    counter = tmp_path / "timeout-counter"

    script = (
        "from pathlib import Path; import time; "
        f"p = Path({str(counter)!r}); "
        "n = int(p.read_text()) + 1 if p.exists() else 1; "
        "p.write_text(str(n)); "
        "time.sleep(5) if n == 1 else print('ok')"
    )

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "slow",
                script,
                retries=1,
                timeout=1,
            )
        ],
    )

    result = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    assert result.status == RunStatus.SUCCEEDED

    assert [attempt.status for attempt in result.stages[0].attempts] == [
        RunStatus.TIMED_OUT,
        RunStatus.SUCCEEDED,
    ]

    assert counter.read_text() == "2"


def test_timeout_retries_exhausted(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "slow",
                "import time; time.sleep(10)",
                retries=1,
                timeout=1,
            )
        ],
    )

    result = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    assert result.status == RunStatus.TIMED_OUT
    assert result.stages[0].attempt_count == 2

    assert all(attempt.status == RunStatus.TIMED_OUT for attempt in result.stages[0].attempts)


def test_pre_cancelled_pipeline_has_no_attempts(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "tests",
                "print('should not execute')",
                retries=5,
            )
        ],
    )

    cancellation = Event()
    cancellation.set()

    result = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        cancel_event=cancellation,
        log_directory=tmp_path / "logs",
    )

    assert result.status == RunStatus.CANCELLED
    assert result.stages[0].status == RunStatus.CANCELLED
    assert result.stages[0].attempt_count == 0


def test_cancellation_prevents_another_retry(
    context: TestContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "build",
                "print('failed')",
                retries=5,
            )
        ],
    )

    cancellation = Event()

    def fail_and_cancel(
        request: ExecutionRequest,
        *,
        cancel_event: Event | None = None,
    ) -> ExecutionResult:
        cancellation.set()

        stdout = tmp_path / "fake-stdout.log"
        stderr = tmp_path / "fake-stderr.log"

        stdout.write_text("failed")
        stderr.write_text("")

        now = datetime.now(UTC)

        return ExecutionResult(
            command=request.command,
            working_directory=tmp_path,
            returncode=3,
            stdout="failed",
            stderr="",
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
            stdout_path=stdout,
            stderr_path=stderr,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(
        "buildscope.pipelines.runner.execute_command",
        fail_and_cancel,
    )

    result = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        cancel_event=cancellation,
        log_directory=tmp_path / "logs",
    )

    stage = result.stages[0]

    assert result.status == RunStatus.CANCELLED
    assert stage.status == RunStatus.CANCELLED
    assert stage.attempt_count == 1
    assert stage.attempts[0].status == RunStatus.FAILED


def test_attempt_history_survives_new_connection(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "tests",
                "import sys; sys.exit(4)",
                retries=1,
            )
        ],
    )

    original = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    engine = create_sqlite_engine(tmp_path / "retries.db")

    try:
        another_runner = PipelineRunner(create_session_factory(engine))

        restored = another_runner.get_run(original.id)

        assert restored == original
        assert restored.stages[0].attempt_count == 2

    finally:
        engine.dispose()


def test_unique_attempt_number_enforced(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, factory = context

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "tests",
                "print('ok')",
            )
        ],
    )

    result = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    stage_id = result.stages[0].id

    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(
                StageAttemptRecord(
                    stage_run_id=stage_id,
                    attempt_number=1,
                    status=RunStatus.QUEUED.value,
                )
            )
            session.flush()


def test_retry_policy_is_version_specific(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    script = "import sys; sys.exit(1)"

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "tests",
                script,
                retries=0,
            )
        ],
    )

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "tests",
                script,
                retries=2,
            )
        ],
    )

    first = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    second = runner.run_version(
        repository_id,
        "quality",
        2,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    assert first.stages[0].attempt_count == 1
    assert second.stages[0].attempt_count == 3

    assert first.pipeline_definition_id != (second.pipeline_definition_id)
