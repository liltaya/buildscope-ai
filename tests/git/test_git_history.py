"""Test Git snapshot capture and pipeline regression history"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.git.history import capture_git_snapshot
from buildscope.git.regressions import GitRegressionHistoryService
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import PipelineRunRecord
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository


def git(root: Path, *args: str) -> str:
    """Execute a Git command inside a temporary repository."""
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a temporary Git repository with one commit."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Example")
    git(
        tmp_path,
        "config",
        "user.email",
        "example@localhost",
    )

    (tmp_path / "example.py").write_text("x = 1\n")

    git(tmp_path, "add", "example.py")
    git(tmp_path, "commit", "-qm", "initial commit")
    git(tmp_path, "branch", "-M", "main")

    return tmp_path


def test_capture_clean_head(repo: Path) -> None:
    snapshot = capture_git_snapshot(repo)

    assert snapshot is not None

    assert snapshot.commit_sha == git(
        repo,
        "rev-parse",
        "HEAD",
    )

    assert snapshot.branch == "main"
    assert not snapshot.is_dirty
    assert snapshot.subject == "initial commit"
    assert snapshot.commit_at.tzinfo is not None


def test_capture_dirty_tracked(repo: Path) -> None:
    (repo / "example.py").write_text("x=2\n")

    snapshot = capture_git_snapshot(repo)

    assert snapshot is not None
    assert snapshot.is_dirty


def test_capture_dirty_untracked(repo: Path) -> None:
    (repo / "new.py").write_text("secret\n")

    snapshot = capture_git_snapshot(repo)

    assert snapshot is not None
    assert snapshot.is_dirty


def test_capture_non_git(tmp_path: Path) -> None:
    assert capture_git_snapshot(tmp_path) is None


def test_empty_git_repository_has_no_snapshot(
    tmp_path: Path,
) -> None:
    """An initialized repository without commits has no snapshot."""
    git(tmp_path, "init", "-q")

    assert capture_git_snapshot(tmp_path) is None


def test_capture_detached_head(repo: Path) -> None:
    git(repo, "checkout", "--detach", "-q")

    snapshot = capture_git_snapshot(repo)

    assert snapshot is not None
    assert snapshot.branch is None


def test_capture_new_commit(repo: Path) -> None:
    first = capture_git_snapshot(repo)

    assert first is not None

    (repo / "example.py").write_text("x=2\n")

    git(repo, "add", "example.py")
    git(repo, "commit", "-qm", "second commit")

    second = capture_git_snapshot(repo)

    assert second is not None
    assert first.commit_sha != second.commit_sha
    assert second.subject == "second commit"
    assert not second.is_dirty


@pytest.fixture
def history(
    tmp_path: Path,
) -> Iterator[
    tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ]
]:
    """Create repository and pipeline versions through real APIs."""
    database_path = tmp_path / "database.db"

    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)

    try:
        factory = create_session_factory(engine)

        with factory.begin() as session:
            repository = Repository(
                name="History Test",
                local_path=str(tmp_path),
            )

            session.add(repository)
            session.flush()

            repository_id = repository.id

        service = PipelineService(factory)

        service.create_version(
            repository_id,
            "name: quality\nstages:\n  - name: checks\n    command: [echo, one]\n",
        )

        service.create_version(
            repository_id,
            "name: quality\nstages:\n  - name: checks\n    command: [echo, two]\n",
        )

        yield (
            factory,
            repository_id,
            GitRegressionHistoryService(factory),
        )

    finally:
        engine.dispose()


def make_run(
    factory: SessionFactory,
    repository_id: UUID,
    *,
    status: str = "SUCCEEDED",
    branch: str | None = "main",
    sha: str | None = "a" * 40,
    dirty: bool = False,
    version: int = 1,
    hour: int = 1,
) -> UUID:
    """Persist a pipeline run with Git snapshot metadata."""
    with factory.begin() as session:
        definition = (
            session.query(PipelineDefinitionRecord)
            .filter_by(
                repository_id=repository_id,
                name="quality",
                version=version,
            )
            .one()
        )

        record = PipelineRunRecord(
            repository_id=repository_id,
            pipeline_definition_id=definition.id,
            status=status,
            queued_at=(datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=hour)),
            git_commit_sha=sha,
            git_branch=branch,
            git_is_dirty=dirty,
            git_commit_subject="revision",
            git_commit_at=datetime(
                2025,
                1,
                1,
                tzinfo=UTC,
            ),
        )

        session.add(record)
        session.flush()

        return record.id


def test_history_includes_version_and_commit(
    history: tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ],
) -> None:
    factory, repo_id, service = history

    first = make_run(
        factory,
        repo_id,
        sha="a" * 40,
    )

    second = make_run(
        factory,
        repo_id,
        status="FAILED",
        sha="b" * 40,
        hour=2,
    )

    items = service.list_history(
        repo_id,
        "quality",
    )

    assert [item.run_id for item in items] == [
        first,
        second,
    ]

    assert [item.status for item in items] == [
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
    ]

    assert items[0].pipeline_version == 1


def test_detect_clean_transition(
    history: tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ],
) -> None:
    factory, repo_id, service = history

    make_run(
        factory,
        repo_id,
        sha="a" * 40,
    )

    make_run(
        factory,
        repo_id,
        status="FAILED",
        sha="b" * 40,
        hour=2,
    )

    cases = service.find_regressions(
        repo_id,
        "quality",
    )

    assert len(cases) == 1
    assert cases[0].previous.commit_sha == "a" * 40
    assert cases[0].failing.commit_sha == "b" * 40


def test_same_commit_failure_not_git_regression(
    history: tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ],
) -> None:
    factory, repo_id, service = history

    make_run(
        factory,
        repo_id,
        sha="a" * 40,
    )

    make_run(
        factory,
        repo_id,
        status="FAILED",
        sha="a" * 40,
        hour=2,
    )

    assert (
        service.find_regressions(
            repo_id,
            "quality",
        )
        == ()
    )


def test_dirty_runs_excluded_from_regressions(
    history: tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ],
) -> None:
    factory, repo_id, service = history

    make_run(
        factory,
        repo_id,
        sha="a" * 40,
    )

    make_run(
        factory,
        repo_id,
        status="FAILED",
        sha="b" * 40,
        dirty=True,
        hour=2,
    )

    assert (
        len(
            service.list_history(
                repo_id,
                "quality",
            )
        )
        == 2
    )

    assert (
        service.find_regressions(
            repo_id,
            "quality",
        )
        == ()
    )


def test_pipeline_version_change_not_automatically_regression(
    history: tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ],
) -> None:
    factory, repo_id, service = history

    make_run(
        factory,
        repo_id,
        sha="a" * 40,
    )

    make_run(
        factory,
        repo_id,
        status="FAILED",
        sha="b" * 40,
        version=2,
        hour=2,
    )

    assert (
        service.find_regressions(
            repo_id,
            "quality",
        )
        == ()
    )


def test_branch_isolation(
    history: tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ],
) -> None:
    factory, repo_id, service = history

    make_run(
        factory,
        repo_id,
        sha="a" * 40,
        branch="main",
    )

    make_run(
        factory,
        repo_id,
        status="FAILED",
        sha="b" * 40,
        branch="feature",
        hour=2,
    )

    assert (
        service.find_regressions(
            repo_id,
            "quality",
        )
        == ()
    )

    assert (
        len(
            service.list_history(
                repo_id,
                "quality",
                branch="feature",
            )
        )
        == 1
    )


def test_timeout_not_git_regression(
    history: tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ],
) -> None:
    factory, repo_id, service = history

    make_run(
        factory,
        repo_id,
        sha="a" * 40,
    )

    make_run(
        factory,
        repo_id,
        status="TIMED_OUT",
        sha="b" * 40,
        hour=2,
    )

    assert (
        service.find_regressions(
            repo_id,
            "quality",
        )
        == ()
    )


def test_limited_history_recent_order(
    history: tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ],
) -> None:
    factory, repo_id, service = history

    make_run(
        factory,
        repo_id,
        sha="a" * 40,
        hour=1,
    )

    make_run(
        factory,
        repo_id,
        sha="b" * 40,
        hour=2,
    )

    make_run(
        factory,
        repo_id,
        sha="c" * 40,
        hour=3,
    )

    items = service.list_history(
        repo_id,
        "quality",
        limit=2,
    )

    assert [item.commit_sha for item in items] == [
        "b" * 40,
        "c" * 40,
    ]


def test_no_git_sha_skipped(
    history: tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ],
) -> None:
    factory, repo_id, service = history

    make_run(
        factory,
        repo_id,
        sha=None,
    )

    assert (
        service.list_history(
            repo_id,
            "quality",
        )
        == ()
    )


def test_invalid_limit(
    history: tuple[
        SessionFactory,
        UUID,
        GitRegressionHistoryService,
    ],
) -> None:
    _, repo_id, service = history

    for value in (0, 1001, True):
        with pytest.raises(ValueError):
            service.list_history(
                repo_id,
                "quality",
                limit=value,
            )
