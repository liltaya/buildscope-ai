"""Integration tests for repository HTTP endpoints"""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(database_path=tmp_path / "database" / "buildscope.db")

    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint_still_works(
    client: TestClient,
) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_initial_list_is_empty(
    client: TestClient,
) -> None:
    response = client.get("/repositories")

    assert response.status_code == 200
    assert response.json() == []


def test_register_and_get_repository(
    client: TestClient,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()

    response = client.post(
        "/repositories",
        json={
            "name": " Example ",
            "local_path": str(repo),
        },
    )

    assert response.status_code == 201, response.text

    details = response.json()

    assert details["name"] == "Example"
    assert details["local_path"] == str(repo.resolve())
    assert details["language"] is None
    assert details["created_at"].endswith("Z")

    get_response = client.get(f"/repositories/{details['id']}")

    assert get_response.status_code == 200
    assert get_response.json() == details


def test_duplicate_path_returns_conflict(
    client: TestClient,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()

    first = client.post(
        "/repositories",
        json={
            "name": "First",
            "local_path": str(repo),
        },
    )

    assert first.status_code == 201

    alias_dir = tmp_path / "alias"
    alias_dir.mkdir()

    second = client.post(
        "/repositories",
        json={
            "name": "Second",
            "local_path": str(alias_dir / ".." / "project"),
        },
    )

    assert second.status_code == 409

    assert len(client.get("/repositories").json()) == 1


def test_nonexistent_directory_returns_422(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = client.post(
        "/repositories",
        json={
            "name": "Missing",
            "local_path": str(tmp_path / "not-here"),
        },
    )

    assert response.status_code == 422


def test_file_instead_of_directory_returns_422(
    client: TestClient,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "some-file.txt"
    file_path.write_text("hello")

    response = client.post(
        "/repositories",
        json={
            "name": "File",
            "local_path": str(file_path),
        },
    )

    assert response.status_code == 422


def test_blank_name_returns_422(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = client.post(
        "/repositories",
        json={
            "name": "  ",
            "local_path": str(tmp_path),
        },
    )

    assert response.status_code == 422


def test_blank_path_returns_422(
    client: TestClient,
) -> None:
    response = client.post(
        "/repositories",
        json={
            "name": "Blank",
            "local_path": "  ",
        },
    )

    assert response.status_code == 422


def test_missing_repository_returns_404(
    client: TestClient,
) -> None:
    response = client.get(f"/repositories/{uuid4()}")

    assert response.status_code == 404


def test_invalid_uuid_returns_422(
    client: TestClient,
) -> None:
    response = client.get("/repositories/not-a-uuid")

    assert response.status_code == 422


def test_list_is_sorted(
    client: TestClient,
    tmp_path: Path,
) -> None:
    for name in ("Zeta", "Alpha"):
        repo = tmp_path / name
        repo.mkdir()

        response = client.post(
            "/repositories",
            json={
                "name": name,
                "local_path": str(repo),
            },
        )

        assert response.status_code == 201

    assert [repo["name"] for repo in client.get("/repositories").json()] == ["Alpha", "Zeta"]


def test_repository_persists_across_app_restarts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "persist.db"

    repository_path = tmp_path / "project"
    repository_path.mkdir()

    with TestClient(create_app(database_path=database_path)) as first:
        response = first.post(
            "/repositories",
            json={
                "name": "Persisted",
                "local_path": str(repository_path),
            },
        )

        assert response.status_code == 201
        repository_id = response.json()["id"]

    with TestClient(create_app(database_path=database_path)) as second:
        response = second.get(f"/repositories/{repository_id}")

        assert response.status_code == 200
        assert response.json()["name"] == "Persisted"
