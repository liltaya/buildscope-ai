"""Integration tests for Git metadata HTTP endpoints"""

import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app


def git(directory: Path, *args: str) -> str:
    """Run a Git command in a temporary test directory."""
    result = subprocess.run(
        ["git", *args],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def make_git_repo(path: Path, *, commit: bool = True) -> None:
    """Initialize a temporary Git repository."""
    path.mkdir()

    git(path, "init", "-q")
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")

    if commit:
        (path / "README.md").write_text("Test\n")

        git(path, "add", "README.md")

        git(
            path,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.com",
            "commit",
            "-q",
            "-m",
            "initial",
        )


def register(client: TestClient, path: Path) -> str:
    """Register a directory and return its repository UUID."""
    response = client.post(
        "/repositories",
        json={
            "name": "Test Repository",
            "local_path": str(path),
        },
    )

    assert response.status_code == 201, response.text

    return str(response.json()["id"])


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(database_path=tmp_path / "test.db")

    with TestClient(app) as test_client:
        yield test_client


def test_git_endpoint_returns_branch_and_commit(
    client: TestClient,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    make_git_repo(project)

    repository_id = register(client, project)

    response = client.get(f"/repositories/{repository_id}/git")

    assert response.status_code == 200, response.text

    data = response.json()

    assert data["is_git_repository"] is True
    assert data["repository_root"] == str(project)
    assert data["branch"] == "main"
    assert data["commit_hash"] == git(
        project,
        "rev-parse",
        "HEAD",
    )
    assert data["detached_head"] is False
    assert data["is_repository_root"] is True


def test_non_git_folder_returns_useful_result(
    client: TestClient,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "ordinary"
    directory.mkdir()

    repository_id = register(client, directory)

    response = client.get(f"/repositories/{repository_id}/git")

    assert response.status_code == 200

    data = response.json()

    assert data["is_git_repository"] is False
    assert data["branch"] is None
    assert data["commit_hash"] is None
    assert data["warnings"]


def test_unknown_repository_returns_404(
    client: TestClient,
) -> None:
    response = client.get(f"/repositories/{uuid4()}/git")

    assert response.status_code == 404


def test_deleted_directory_returns_422(
    client: TestClient,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "temporary"
    directory.mkdir()

    repository_id = register(client, directory)

    directory.rmdir()

    response = client.get(f"/repositories/{repository_id}/git")

    assert response.status_code == 422


def test_git_metadata_is_read_live(
    client: TestClient,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    make_git_repo(project)

    repository_id = register(client, project)

    first = client.get(f"/repositories/{repository_id}/git")

    assert first.status_code == 200
    assert first.json()["branch"] == "main"

    git(
        project,
        "checkout",
        "-b",
        "feature-test",
    )

    second = client.get(f"/repositories/{repository_id}/git")

    assert second.status_code == 200
    assert second.json()["branch"] == "feature-test"


def test_unborn_repository_through_api(
    client: TestClient,
    tmp_path: Path,
) -> None:
    project = tmp_path / "empty-git"
    make_git_repo(project, commit=False)

    repository_id = register(client, project)

    response = client.get(f"/repositories/{repository_id}/git")

    assert response.status_code == 200

    data = response.json()

    assert data["is_git_repository"] is True
    assert data["branch"] == "main"
    assert data["commit_hash"] is None
    assert data["warnings"]


def test_git_unavailable_returns_503(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    make_git_repo(project)

    repository_id = register(client, project)

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(
        "buildscope.git.inspector.subprocess.run",
        missing,
    )

    response = client.get(f"/repositories/{repository_id}/git")

    assert response.status_code == 503
    assert "Git executable" in response.json()["detail"]
