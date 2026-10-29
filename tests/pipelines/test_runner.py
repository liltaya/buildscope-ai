"""Integration tests for persistent sequential pipeline execution"""

import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
import yaml
from sqlalchemy import inspect, select

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.pipelines.run_models import PipelineRunRecord
from buildscope.pipelines.runner import (
    ExecutionApprovalRequiredError,
    PipelineRunner,
    PipelineRunNotFoundError,
)
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import (
    InvalidRunTransitionError,
    RunStatus,
    assert_transition,
)
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
    database_path = tmp_path / "runs.db"

    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)
    factory = create_session_factory(engine)

    with factory.begin() as session:
        repository = Repository(
            name="Test Project",
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


def save_pipeline(
    service: PipelineService,
    repository_id: UUID,
    stages: list[dict[str, object]],
) -> None:
    yaml_text = yaml.safe_dump(
        {
            "name": "quality",
            "stages": stages,
        }
    )

    service.create_version(
        repository_id,
        yaml_text,
    )


def python_stage(
    name: str,
    script: str,
    *,
    depends_on: list[str] | None = None,
    timeout: int = 30,
) -> dict[str, object]:
    return {
        "name": name,
        "command": [
            sys.executable,
            "-c",
            script,
        ],
        "depends_on": depends_on or [],
        "timeout_seconds": timeout,
    }


def test_successful_pipeline(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "first",
                "print('first')",
            ),
            python_stage(
                "second",
                "print('second')",
                depends_on=["first"],
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

    assert result.status == RunStatus.SUCCEEDED

    assert [stage.stage_name for stage in result.stages] == ["first", "second"]

    assert all(stage.status == RunStatus.SUCCEEDED for stage in result.stages)

    assert all(stage.returncode == 0 for stage in result.stages)

    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.finished_at >= result.started_at

    assert result.stages[0].stdout_path is not None

    assert Path(result.stages[0].stdout_path).read_text() == "first\n"

    assert runner.get_run(result.id) == result


def test_forward_dependencies_run_in_correct_order(
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
                "print('tests')",
                depends_on=["build"],
            ),
            python_stage(
                "build",
                "print('build')",
                depends_on=["configure"],
            ),
            python_stage(
                "configure",
                "print('configure')",
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

    assert result.status == RunStatus.SUCCEEDED

    assert [stage.stage_name for stage in result.stages] == ["configure", "build", "tests"]


def test_failure_blocks_dependent_stage(
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
                "import sys; sys.exit(7)",
            ),
            python_stage(
                "tests",
                "print('should not run')",
                depends_on=["build"],
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
    assert result.stages[0].status == RunStatus.FAILED
    assert result.stages[0].returncode == 7

    assert result.stages[1].status == RunStatus.BLOCKED
    assert result.stages[1].started_at is None
    assert result.stages[1].stdout_path is None


def test_independent_stage_still_runs(
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
            ),
            python_stage(
                "tests",
                "print('blocked')",
                depends_on=["build"],
            ),
            python_stage(
                "lint",
                "print('lint complete')",
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

    statuses = {stage.stage_name: stage.status for stage in result.stages}

    assert statuses == {
        "build": RunStatus.FAILED,
        "tests": RunStatus.BLOCKED,
        "lint": RunStatus.SUCCEEDED,
    }


def test_timeout_is_persisted(
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
                "import time; print('started', flush=True); time.sleep(10)",
                timeout=1,
            ),
            python_stage(
                "after",
                "print('should not run')",
                depends_on=["slow"],
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

    assert result.status == RunStatus.TIMED_OUT
    assert result.stages[0].status == RunStatus.TIMED_OUT
    assert result.stages[1].status == RunStatus.BLOCKED

    assert result.stages[0].stdout_path is not None

    assert "started" in Path(result.stages[0].stdout_path).read_text()


def test_cancellation_before_launch(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "first",
                "print('do not execute')",
            ),
            python_stage(
                "second",
                "print('do not execute')",
            ),
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

    assert all(stage.status == RunStatus.CANCELLED for stage in result.stages)

    assert all(stage.started_at is None for stage in result.stages)


def test_execution_requires_approval(
    context: TestContext,
) -> None:
    runner, service, repository_id, factory = context

    save_pipeline(
        service,
        repository_id,
        [
            python_stage(
                "tests",
                "print('hello')",
            ),
        ],
    )

    with pytest.raises(ExecutionApprovalRequiredError):
        runner.run_version(
            repository_id,
            "quality",
            1,
            approved=False,
        )

    with factory() as session:
        assert session.scalars(select(PipelineRunRecord)).all() == []


def test_missing_executable_is_failed_stage(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [
            {
                "name": "missing-command",
                "command": ["__buildscope_no_such_executable__"],
            },
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
    assert result.stages[0].status == RunStatus.FAILED
    assert result.stages[0].returncode is None
    assert result.stages[0].error_message is not None


def test_historical_pipeline_versions(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [python_stage("old", "print('old')")],
    )

    save_pipeline(
        service,
        repository_id,
        [python_stage("new", "print('new')")],
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

    assert first.pipeline_definition_id != (second.pipeline_definition_id)

    assert first.stages[0].stage_name == "old"
    assert second.stages[0].stage_name == "new"


def test_run_history_survives_new_connection(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    save_pipeline(
        service,
        repository_id,
        [python_stage("tests", "print(17)")],
    )

    original = runner.run_version(
        repository_id,
        "quality",
        1,
        approved=True,
        log_directory=tmp_path / "logs",
    )

    another_engine = create_sqlite_engine(tmp_path / "runs.db")

    try:
        other_runner = PipelineRunner(create_session_factory(another_engine))

        assert other_runner.get_run(original.id) == original

    finally:
        another_engine.dispose()


def test_missing_run(
    context: TestContext,
) -> None:
    runner, _, _, _ = context

    with pytest.raises(PipelineRunNotFoundError):
        runner.get_run(uuid4())


def test_state_machine_rejects_invalid_transitions() -> None:
    assert_transition(
        RunStatus.QUEUED,
        RunStatus.RUNNING,
    )

    assert_transition(
        RunStatus.RUNNING,
        RunStatus.SUCCEEDED,
    )

    with pytest.raises(InvalidRunTransitionError):
        assert_transition(
            RunStatus.SUCCEEDED,
            RunStatus.RUNNING,
        )

    with pytest.raises(InvalidRunTransitionError):
        assert_transition(
            RunStatus.QUEUED,
            RunStatus.SUCCEEDED,
        )


def test_migration_creates_run_tables(
    context: TestContext,
    tmp_path: Path,
) -> None:
    engine = create_sqlite_engine(tmp_path / "runs.db")

    try:
        tables = set(inspect(engine).get_table_names())

        assert "pipeline_runs" in tables
        assert "stage_runs" in tables

    finally:
        engine.dispose()


@pytest.mark.skipif(
    shutil.which("cmake") is None or shutil.which("c++") is None,
    reason="CMake and a C++ compiler are required",
)
def test_real_cpp_pipeline(
    context: TestContext,
    tmp_path: Path,
) -> None:
    runner, service, repository_id, _ = context

    (tmp_path / "main.cpp").write_text(
        '#include <iostream>\nint main() {\n    std::cout << "ok\\n";\n    return 0;\n}\n'
    )

    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(BuildScopeSample LANGUAGES CXX)\n"
        "enable_testing()\n"
        "add_executable(demo main.cpp)\n"
        "add_test(NAME demo COMMAND demo)\n"
    )

    save_pipeline(
        service,
        repository_id,
        [
            {
                "name": "configure",
                "command": [
                    "cmake",
                    "-S",
                    ".",
                    "-B",
                    "build",
                ],
            },
            {
                "name": "build",
                "command": [
                    "cmake",
                    "--build",
                    "build",
                ],
                "depends_on": ["configure"],
            },
            {
                "name": "tests",
                "command": [
                    "ctest",
                    "--test-dir",
                    "build",
                    "--output-on-failure",
                ],
                "depends_on": ["build"],
            },
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

    assert [stage.status for stage in result.stages] == [
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED,
    ]
