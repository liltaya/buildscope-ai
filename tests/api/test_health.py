"""Tests for the BuildScope health endpoint."""

from fastapi.testclient import TestClient

from apps.api.main import create_app


def test_health_returns_success() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200


def test_health_returns_expected_payload() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.json() == {
        "status": "ok",
        "service": "buildscope-api",
        "version": "0.1.0",
    }


def test_health_returns_json_content_type() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.headers["content-type"].startswith("application/json")


def test_openapi_schema_is_available() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200

    payload = response.json()

    assert payload["info"]["title"] == "BuildScope AI"
    assert payload["info"]["version"] == "0.1.0"
    assert "/health" in payload["paths"]
