"""Regression windows detected from pipeline runs over a real Git history"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from buildscope.database import create_session_factory, create_sqlite_engine
from buildscope.database.migrate import upgrade_database
from buildscope.git.regressions import GitRegressionHistoryService
from buildscope.pipelines.runner import PipelineRunner
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository

CHECK = (
    "import pathlib, sys; "
    "status = pathlib.Path('status.txt').read_text().splitlines()[0]; "
    "sys.exit(0 if status == 'ok' else 1)"
)

HISTORY = (
    ("ok", "add parser"),
    ("ok", "document parser options"),
    ("broken", "switch parser to streaming mode"),
    ("broken", "rename streaming flag"),
    ("ok", "restore buffered fallback"),
    ("broken", "drop buffered fallback"),
)


def git(directory: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.com",
            *args,
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_regression_windows_from_scripted_history(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "-q", "-b", "main")

    database = tmp_path / "history.db"
    upgrade_database(database)
    engine = create_sqlite_engine(database)

    try:
        factory = create_session_factory(engine)

        with factory.begin() as session:
            repository = Repository(name="project", local_path=str(project))
            session.add(repository)
            session.flush()
            repository_id = repository.id

        PipelineService(factory).create_version(
            repository_id,
            yaml.safe_dump(
                {
                    "name": "ci",
                    "stages": [
                        {"name": "check", "command": [sys.executable, "-c", CHECK]},
                    ],
                }
            ),
        )

        runner = PipelineRunner(factory)
        commits: list[str] = []

        for content, subject in HISTORY:
            (project / "status.txt").write_text(f"{content}\n{subject}\n")
            git(project, "add", "status.txt")
            git(project, "commit", "-q", "-m", subject)
            commits.append(git(project, "rev-parse", "HEAD"))

            run = runner.run_version(
                repository_id,
                "ci",
                1,
                approved=True,
                log_directory=tmp_path / "logs",
            )

            expected = RunStatus.SUCCEEDED if content == "ok" else RunStatus.FAILED
            assert run.status == expected
            assert run.git_commit_sha == commits[-1]
            assert run.git_is_dirty is False

        regressions = GitRegressionHistoryService(factory).find_regressions(
            repository_id,
            "ci",
        )

    finally:
        engine.dispose()

    windows = [
        (
            item.branch,
            item.previous.commit_sha,
            item.previous.commit_subject,
            item.failing.commit_sha,
            item.failing.commit_subject,
        )
        for item in regressions
    ]

    # The fourth commit fails again but was never green, so it is not a
    # new regression; the window stays anchored at the last good commit.
    assert windows == [
        (
            "main",
            commits[1],
            "document parser options",
            commits[2],
            "switch parser to streaming mode",
        ),
        (
            "main",
            commits[4],
            "restore buffered fallback",
            commits[5],
            "drop buffered fallback",
        ),
    ]
