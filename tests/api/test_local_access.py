"""Every API route rejects remote clients, foreign hosts and other origins"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from apps.api.main import ROUTERS, create_app
from apps.api.security import require_local_request
from tests.helpers import LOCAL_BASE_URL, local_client

PATH_VALUES = {
    "repository_id": str(uuid4()),
    "pipeline_name": "quality",
    "version": "1",
    "filename": "app.js",
}

QUERY = f"?repository_id={uuid4()}&pipeline_name=quality&version=1"


def api_routes() -> list[APIRoute]:
    return [
        route
        for router in ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute)
    ]


def every_route() -> list[tuple[str, str]]:
    """List every (method, concrete path) the routers serve."""
    routes: list[tuple[str, str]] = []

    for route in api_routes():
        path = re.sub(
            r"\{(\w+)(?::\w+)?\}",
            lambda match: PATH_VALUES[match.group(1)],
            route.path,
        )

        for method in sorted((route.methods or set()) - {"HEAD"}):
            routes.append((method, path))

    return routes


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("BUILDSCOPE_ENABLE_LOCAL_DASHBOARD", "1")
    monkeypatch.setenv("BUILDSCOPE_DATABASE_PATH", str(tmp_path / "access.db"))

    application = create_app(database_path=tmp_path / "access.db")

    with local_client(application):
        yield application


def send(
    client: TestClient,
    method: str,
    path: str,
    **headers: str,
) -> httpx.Response:
    target = path + (QUERY if path.startswith("/dashboard/api") else "")

    return client.request(method, target, headers=headers, json={})


def test_route_inventory_includes_reads_and_writes() -> None:
    routes = every_route()
    methods = {method for method, _ in routes}

    assert {"GET", "POST"} <= methods
    assert ("POST", "/repositories") in routes
    assert ("POST", "/predictions/stages") in routes
    assert ("GET", "/dashboard/api/overview") in routes


def test_inventory_covers_every_documented_path() -> None:
    documented = set(create_app(database_path=Path("unused.db")).openapi()["paths"])

    assert documented <= {route.path for route in api_routes()}


def test_every_route_declares_the_local_guard() -> None:
    for route in api_routes():
        calls = {dependency.call for dependency in route.dependant.dependencies}

        assert require_local_request in calls, route.path


@pytest.mark.parametrize(("method", "path"), every_route())
def test_remote_client_rejected(app: FastAPI, method: str, path: str) -> None:
    client = TestClient(
        app,
        base_url=LOCAL_BASE_URL,
        client=("192.0.2.10", 50000),
    )

    response = send(client, method, path)

    assert response.status_code == 403
    assert response.json() == {"detail": "Loopback clients only"}


@pytest.mark.parametrize(("method", "path"), every_route())
@pytest.mark.parametrize(
    "host",
    ["evil.example", "evil.example:8000", "127.0.0.1.evil.example", "0.0.0.0:8000"],
)
def test_foreign_host_header_rejected(
    app: FastAPI,
    method: str,
    path: str,
    host: str,
) -> None:
    response = send(local_client(app), method, path, host=host)

    assert response.status_code == 403
    assert response.json() == {"detail": "Untrusted Host header"}


@pytest.mark.parametrize(("method", "path"), every_route())
@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "http://localhost:3000",
        "http://127.0.0.1:9999",
        "null",
        "file://",
    ],
)
def test_cross_origin_rejected(
    app: FastAPI,
    method: str,
    path: str,
    origin: str,
) -> None:
    response = send(local_client(app), method, path, origin=origin)

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-origin requests are not allowed"}


@pytest.mark.parametrize(("method", "path"), every_route())
def test_same_origin_request_passes_the_guard(
    app: FastAPI,
    method: str,
    path: str,
) -> None:
    client = local_client(app, raise_server_exceptions=False)

    response = send(client, method, path, origin=LOCAL_BASE_URL)

    # Handlers may still reject the empty body or unknown IDs; the point
    # is that the access guard does not.
    assert response.status_code != 403


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
def test_loopback_host_names_accepted(app: FastAPI, host: str) -> None:
    response = local_client(app).get("/health", headers={"host": host})

    assert response.status_code == 200
