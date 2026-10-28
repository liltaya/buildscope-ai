"""HTTP integration tests for versioned pipeline definitions"""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.repositories.models import Repository

ONE = "name: quality\nstages:\n  - name: tests\n    command: [pytest, -q]\n"

TWO = ONE.replace("pytest, -q", "pytest, -vv")

BAD_DAG = ONE.replace(
    "    command: [pytest, -q]\n",
    "    command: [pytest, -q]\n    depends_on: [missing]\n",
)


@pytest.fixture
def api(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, str]]:
    database_path = tmp_path / "api.db"

    app = create_app(database_path=database_path)

    with TestClient(app) as client:
        engine = create_sqlite_engine(database_path)

        with create_session_factory(engine).begin() as session:
            repository = Repository(
                name="Example",
                local_path=str(tmp_path),
            )

            session.add(repository)
            session.flush()
            repository_id = str(repository.id)

        engine.dispose()

        yield client, repository_id


def test_post_creates_version(
    api: tuple[TestClient, str],
) -> None:
    client, repository_id = api

    response = client.post(
        f"/repositories/{repository_id}/pipelines",
        json={"yaml": ONE},
    )

    assert response.status_code == 201, response.text
    assert response.json()["version"] == 1
    assert response.json()["created_at"].endswith("Z")


def test_list_and_specific_version(
    api: tuple[TestClient, str],
) -> None:
    client, repository_id = api

    base = f"/repositories/{repository_id}/pipelines"

    assert (
        client.post(
            base,
            json={"yaml": ONE},
        ).status_code
        == 201
    )

    assert (
        client.post(
            base,
            json={"yaml": TWO},
        ).status_code
        == 201
    )

    response = client.get(base + "/quality/versions")

    assert [item["version"] for item in response.json()] == [1, 2]

    historical = client.get(base + "/quality/versions/1")

    assert historical.json()["definition"]["stages"][0]["command"] == ["pytest", "-q"]

    latest = client.get(base + "/quality/latest")

    assert latest.json()["version"] == 2


def test_bad_yaml_returns_422(
    api: tuple[TestClient, str],
) -> None:
    client, repository_id = api

    response = client.post(
        f"/repositories/{repository_id}/pipelines",
        json={"yaml": "name: ["},
    )

    assert response.status_code == 422


def test_bad_dag_returns_422(
    api: tuple[TestClient, str],
) -> None:
    client, repository_id = api

    response = client.post(
        f"/repositories/{repository_id}/pipelines",
        json={"yaml": BAD_DAG},
    )

    assert response.status_code == 422


def test_unknown_repository_returns_404(
    api: tuple[TestClient, str],
) -> None:
    client, _ = api

    response = client.post(
        f"/repositories/{uuid4()}/pipelines",
        json={"yaml": ONE},
    )

    assert response.status_code == 404


def test_unknown_version_returns_404(
    api: tuple[TestClient, str],
) -> None:
    client, repository_id = api

    response = client.get(f"/repositories/{repository_id}/pipelines/quality/versions/999")

    assert response.status_code == 404


def test_pipeline_persists_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "persistent.db"

    app = create_app(database_path=database_path)

    with TestClient(app) as first:
        engine = create_sqlite_engine(database_path)

        with create_session_factory(engine).begin() as session:
            repository = Repository(
                name="Example",
                local_path=str(tmp_path),
            )

            session.add(repository)
            session.flush()
            repository_id = str(repository.id)

        engine.dispose()

        response = first.post(
            f"/repositories/{repository_id}/pipelines",
            json={"yaml": ONE},
        )

        assert response.status_code == 201

    with TestClient(create_app(database_path=database_path)) as second:
        response = second.get(f"/repositories/{repository_id}/pipelines/quality/latest")

        assert response.status_code == 200
        assert response.json()["version"] == 1
