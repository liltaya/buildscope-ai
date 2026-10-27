"""Integration tests for project detection and persistence"""

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.main import create_app
from buildscope.database import (
    create_schema,
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.repositories.service import RepositoryService


def test_service_detection_persists(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    (project / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools"]\n'
        'build-backend = "setuptools.build_meta"\n'
        "[tool.pytest.ini_options]\n"
    )

    engine = create_sqlite_engine(tmp_path / "database.sqlite3")

    try:
        create_schema(engine)

        service = RepositoryService(create_session_factory(engine))

        repository = service.register_repository(
            name="Sample",
            local_path=project,
        )

        outcome = service.detect_repository(repository.id)

        assert outcome.repository.language == "python"
        assert outcome.detection.build_system == "setuptools"

        persisted = service.get_repository(repository.id)

        assert persisted.test_framework == "pytest"

    finally:
        engine.dispose()


def test_detect_endpoint_persists_results(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    (project / "CMakeLists.txt").write_text("project(Test LANGUAGES CXX)\ninclude(CTest)\n")

    app = create_app(database_path=tmp_path / "database.sqlite3")

    with TestClient(app) as client:
        created = client.post(
            "/repositories",
            json={
                "name": "Example",
                "local_path": str(project),
            },
        )

        assert created.status_code == 201

        repository_id = created.json()["id"]

        response = client.post(f"/repositories/{repository_id}/detect")

        assert response.status_code == 200

        payload = response.json()

        assert payload["detection"]["language"] == "cpp"
        assert payload["detection"]["build_system"] == "cmake"
        assert payload["detection"]["test_framework"] == "ctest"

        assert payload["repository"]["language"] == "cpp"

        saved = client.get(f"/repositories/{repository_id}")

        assert saved.status_code == 200
        assert saved.json()["language"] == "cpp"


def test_detection_missing_repository(
    tmp_path: Path,
) -> None:
    app = create_app(database_path=tmp_path / "database.sqlite3")

    with TestClient(app) as client:
        response = client.post(f"/repositories/{uuid4()}/detect")

        assert response.status_code == 404


def test_detection_rejects_deleted_folder(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    app = create_app(database_path=tmp_path / "database.sqlite3")

    with TestClient(app) as client:
        created = client.post(
            "/repositories",
            json={
                "name": "Example",
                "local_path": str(project),
            },
        )

        assert created.status_code == 201

        repository_id = created.json()["id"]

        project.rmdir()

        response = client.post(f"/repositories/{repository_id}/detect")

        assert response.status_code == 422

        saved = client.get(f"/repositories/{repository_id}")

        assert saved.json()["language"] is None


def test_unsupported_project_returns_warning(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    (project / "README.md").write_text("Unsupported sample.")

    app = create_app(database_path=tmp_path / "database.sqlite3")

    with TestClient(app) as client:
        created = client.post(
            "/repositories",
            json={
                "name": "Unsupported",
                "local_path": str(project),
            },
        )

        repository_id = created.json()["id"]

        response = client.post(f"/repositories/{repository_id}/detect")

        assert response.status_code == 200

        detection = response.json()["detection"]

        assert detection["language"] is None

        assert any("No supported" in warning for warning in detection["warnings"])
