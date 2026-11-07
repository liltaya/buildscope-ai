"""Shared fixtures for the BuildScope test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

WORKER_VARIABLE = "BUILDSCOPE_CPP_WORKER"

REQUIRE_VARIABLE = "BUILDSCOPE_REQUIRE_CPP_WORKER"

WORKER_RELATIVE_PATH = Path("cpp-worker") / "buildscope_cpp_worker"


def find_cpp_worker() -> Path | None:
    """Locate the CMake-built C++ worker binary, if one exists."""
    configured = os.environ.get(WORKER_VARIABLE)

    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None

    candidates = sorted((ROOT / "build").glob(f"*/{WORKER_RELATIVE_PATH}"))

    return candidates[0] if candidates else None


@pytest.fixture(scope="session")
def cpp_worker() -> Path:
    """Return the C++ worker binary or skip with build instructions."""
    binary = find_cpp_worker()

    if binary is not None:
        return binary

    message = (
        "C++ worker binary not found. Build it with "
        "`cmake -S . -B build/dev && cmake --build build/dev` "
        f"or set {WORKER_VARIABLE}."
    )

    # CI builds the worker before pytest, so a missing binary there is a
    # pipeline bug rather than an optional backend being unavailable.
    if os.environ.get(REQUIRE_VARIABLE) == "1":
        pytest.fail(message)

    pytest.skip(message)
