"""The built wheel ships the dashboard and migrations, and nothing private"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from buildscope.release.preflight import (
    DASHBOARD_ASSETS,
    build_wheel,
    check_wheel_assets,
    is_private_path,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_wheel(ROOT, tmp_path_factory.mktemp("wheel"))


@pytest.fixture(scope="module")
def names(wheel: Path) -> set[str]:
    with zipfile.ZipFile(wheel) as archive:
        return set(archive.namelist())


def test_wheel_contains_dashboard_assets(wheel: Path, names: set[str]) -> None:
    assert set(DASHBOARD_ASSETS) <= names
    assert check_wheel_assets(wheel).ok


def test_wheel_contains_migrations(names: set[str]) -> None:
    migrations = {
        name for name in names if name.startswith("buildscope/database/migrations/")
    }

    assert "buildscope/database/migrations/env.py" in migrations
    assert (
        sum(name.endswith(".py") and "/versions/v" in name for name in migrations) >= 10
    )


def test_wheel_contains_license_and_typing_marker(names: set[str]) -> None:
    assert "buildscope/py.typed" in names
    assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)


def test_wheel_has_no_private_or_test_files(names: set[str]) -> None:
    assert not [name for name in names if is_private_path(name)]
    assert not [name for name in names if name.startswith(("tests/", "data/", "docs/"))]


def test_dashboard_assets_are_not_empty(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        sizes = {name: archive.getinfo(name).file_size for name in DASHBOARD_ASSETS}

    assert all(size > 1000 for size in sizes.values()), sizes
