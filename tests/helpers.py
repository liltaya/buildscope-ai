"""Helpers shared by API tests."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

LOCAL_BASE_URL = "http://127.0.0.1:8000"


def local_client(app: FastAPI, **kwargs: Any) -> TestClient:
    """Return a client that looks like a browser on the same machine."""
    return TestClient(
        app,
        base_url=LOCAL_BASE_URL,
        client=("127.0.0.1", 50000),
        **kwargs,
    )
