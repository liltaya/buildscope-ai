"""Tests for the BuildScope health endpoint."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from apps.api.main import create_app
from buildscope.database.migrate import head_revision
from tests.helpers import local_client


def test_health_returns_success(tmp_path: Path) -> None:
    with local_client(create_app(database_path=tmp_path / "api.db")) as client:
        response = client.get("/health")

    assert response.status_code == 200


def test_health_returns_expected_payload(tmp_path: Path) -> None:
    with local_client(create_app(database_path=tmp_path / "api.db")) as client:
        response = client.get("/health")

    assert response.json() == {
        "status": "ok",
        "service": "buildscope-api",
        "version": "0.1.0",
    }


def test_health_returns_json_content_type(tmp_path: Path) -> None:
    with local_client(create_app(database_path=tmp_path / "api.db")) as client:
        response = client.get("/health")

    assert response.headers["content-type"].startswith("application/json")


def test_openapi_schema_is_available(tmp_path: Path) -> None:
    with local_client(create_app(database_path=tmp_path / "api.db")) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200

    payload = response.json()

    assert payload["info"]["title"] == "BuildScope AI"
    assert payload["info"]["version"] == "0.1.0"
    assert "/health" in payload["paths"]


def test_ready_after_startup_migration(tmp_path: Path) -> None:
    with local_client(create_app(database_path=tmp_path / "api.db")) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_not_ready_when_schema_is_behind(tmp_path: Path) -> None:
    database = tmp_path / "api.db"
    application = create_app(database_path=database)

    with local_client(application) as client:
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE alembic_version SET version_num = 'old'")

        response = client.get("/health/ready")

    assert response.status_code == 503
    assert str(database) not in response.text


def test_head_revision_is_latest_packaged_migration() -> None:
    assert head_revision() == "0010_benchmarks"
