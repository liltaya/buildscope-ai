"""The same pipeline produces the same persisted outcome on both backends"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from buildscope.database import create_session_factory, create_sqlite_engine
from buildscope.database.migrate import upgrade_database
from buildscope.execution import CppStageExecutor
from buildscope.pipelines.runner import PipelineRunDetails, PipelineRunner
from buildscope.pipelines.service import PipelineService
from buildscope.repositories.models import Repository


def stage(
    name: str,
    script: str,
    *,
    depends_on: tuple[str, ...] = (),
    retry_count: int = 0,
    timeout: int = 30,
) -> dict[str, object]:
    return {
        "name": name,
        "command": [sys.executable, "-c", script],
        "depends_on": list(depends_on),
        "retry_count": retry_count,
        "timeout_seconds": timeout,
    }


PIPELINE = {
    "name": "parity",
    "stages": [
        stage("compile", "print('compiled')"),
        stage(
            "unit",
            "import sys; print('3 passed'); print('warning', file=sys.stderr)",
            depends_on=("compile",),
        ),
        stage(
            "lint",
            "import sys; print('E501 line too long'); sys.exit(3)",
            depends_on=("compile",),
            retry_count=1,
        ),
        stage("package", "print('never runs')", depends_on=("unit", "lint")),
    ],
}


def run_pipeline(
    tmp_path: Path,
    backend: CppStageExecutor | None,
) -> PipelineRunDetails:
    workspace = tmp_path / "repo"
    workspace.mkdir(parents=True)
    database = tmp_path / "parity.db"

    upgrade_database(database)
    engine = create_sqlite_engine(database)

    try:
        factory = create_session_factory(engine)

        with factory.begin() as session:
            repository = Repository(name="parity", local_path=str(workspace))
            session.add(repository)
            session.flush()
            repository_id = repository.id

        PipelineService(factory).create_version(
            repository_id,
            yaml.safe_dump(PIPELINE),
        )

        return PipelineRunner(factory, executor=backend).run_version(
            repository_id,
            "parity",
            1,
            approved=True,
            log_directory=tmp_path / "logs",
        )

    finally:
        engine.dispose()


def outcome(run: PipelineRunDetails) -> dict[str, object]:
    """Reduce a run to the fields that must not depend on the backend."""
    return {
        "status": run.status,
        "stages": [
            (
                item.stage_name,
                item.status,
                item.returncode,
                [attempt.status for attempt in item.attempts],
                Path(item.stdout_path).read_text() if item.stdout_path else None,
                Path(item.stderr_path).read_text() if item.stderr_path else None,
            )
            for item in run.stages
        ],
    }


def test_python_and_cpp_backends_agree(
    tmp_path: Path,
    cpp_worker: Path,
) -> None:
    python_run = run_pipeline(tmp_path / "python", None)
    cpp_run = run_pipeline(tmp_path / "cpp", CppStageExecutor(cpp_worker))

    assert outcome(python_run) == outcome(cpp_run)


def test_parity_pipeline_exercises_success_retry_and_skip(
    tmp_path: Path,
) -> None:
    run = run_pipeline(tmp_path, None)
    summary = {item.stage_name: item for item in run.stages}

    assert run.status.value == "FAILED"
    assert summary["compile"].returncode == 0
    assert summary["unit"].returncode == 0
    assert summary["lint"].returncode == 3
    assert summary["lint"].attempt_count == 2
    assert summary["package"].attempt_count == 0


@pytest.mark.parametrize("name", ["compile", "unit"])
def test_cpp_backend_keeps_streams_separate(
    tmp_path: Path,
    cpp_worker: Path,
    name: str,
) -> None:
    run = run_pipeline(tmp_path, CppStageExecutor(cpp_worker))
    item = next(entry for entry in run.stages if entry.stage_name == name)

    assert item.stdout_path is not None
    assert item.stderr_path is not None
    assert item.stdout_path != item.stderr_path
