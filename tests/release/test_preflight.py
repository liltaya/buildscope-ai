"""Release preflight checks against throwaway repositories"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

import buildscope
from buildscope.release.__main__ import main as release_main
from buildscope.release.preflight import (
    DASHBOARD_ASSETS,
    changelog_section,
    check_changelog,
    check_clean_tree,
    check_private_files,
    check_required_files,
    check_version,
    check_wheel_assets,
    is_private_path,
)

VERSION = buildscope.__version__

CHANGELOG = f"""# Changelog

## [{VERSION}] - 2026-11-07

### Added

- Pipeline runner.

## [0.0.1] - 2026-10-01

- Prototype.
"""


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=F", "-c", "user.email=f@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "example"\nversion = "{VERSION}"\n'
    )
    (root / "LICENSE").write_text("MIT\n")
    (root / "SECURITY.md").write_text("# Security\n")
    (root / "CHANGELOG.md").write_text(CHANGELOG)
    (root / ".env.example").write_text("BUILDSCOPE_DATABASE_PATH=\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")

    return root


def test_clean_repository_passes_source_checks(repo: Path) -> None:
    findings = [
        check_clean_tree(repo),
        check_private_files(repo),
        check_version(repo, f"v{VERSION}"),
        check_required_files(repo),
        check_changelog(repo),
    ]

    assert all(finding.ok for finding in findings), findings


@pytest.mark.parametrize(
    "change",
    ["untracked", "modified", "staged"],
)
def test_dirty_tree_fails(repo: Path, change: str) -> None:
    if change == "untracked":
        (repo / "notes.txt").write_text("draft\n")
    elif change == "modified":
        (repo / "LICENSE").write_text("changed\n")
    else:
        (repo / "new.txt").write_text("staged\n")
        git(repo, "add", "new.txt")

    assert not check_clean_tree(repo).ok


@pytest.mark.parametrize(
    "path",
    [
        "data/buildscope.db",
        "backup.sqlite3",
        "models/model.joblib",
        "cache.pkl",
        ".env",
        ".env.production",
        "certs/server.key",
    ],
)
def test_tracked_private_file_fails(repo: Path, path: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private\n")
    git(repo, "add", "-f", path)
    git(repo, "commit", "-q", "-m", "oops")

    finding = check_private_files(repo)

    assert not finding.ok
    assert path in finding.detail


@pytest.mark.parametrize(
    ("path", "private"),
    [
        (".env.example", False),
        ("docs/database.md", False),
        ("buildscope/database/models.py", False),
        ("tests/fixtures/report.xml", False),
        ("data/demo.db-wal", True),
        ("deep/nested/id_ed25519", True),
    ],
)
def test_private_path_patterns(path: str, private: bool) -> None:
    assert is_private_path(path) is private


def test_tag_must_match_project_version(repo: Path) -> None:
    assert check_version(repo, f"v{VERSION}").ok
    assert check_version(repo, None).ok

    mismatch = check_version(repo, "v9.9.9")

    assert not mismatch.ok
    assert "v9.9.9" in mismatch.detail


def test_package_version_must_match_pyproject(repo: Path) -> None:
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "9.9.9"\n'
    )

    finding = check_version(repo, None)

    assert not finding.ok
    assert "__version__" in finding.detail


@pytest.mark.parametrize("missing", ["LICENSE", "SECURITY.md", "CHANGELOG.md"])
def test_missing_required_file_fails(repo: Path, missing: str) -> None:
    (repo / missing).unlink()

    finding = check_required_files(repo)

    assert not finding.ok
    assert missing in finding.detail


@pytest.mark.parametrize(
    "changelog",
    [
        "# Changelog\n\n## [0.0.1]\n\n- Prototype.\n",
        f"# Changelog\n\n## [{VERSION}]\n\n## [0.0.1]\n\n- Prototype.\n",
    ],
    ids=["no-section", "empty-section"],
)
def test_changelog_needs_a_non_empty_section(repo: Path, changelog: str) -> None:
    (repo / "CHANGELOG.md").write_text(changelog)

    assert not check_changelog(repo).ok


def test_changelog_section_stops_at_next_release() -> None:
    section = changelog_section(CHANGELOG, VERSION)

    assert section is not None
    assert "Pipeline runner." in section
    assert "Prototype." not in section


def test_changelog_section_drops_link_definitions() -> None:
    changelog = f"## [{VERSION}] - 2026-11-07\n\n- Added things.\n\n[{VERSION}]: https://example.com\n"

    assert changelog_section(changelog, VERSION) == "- Added things."


def test_repository_changelog_has_current_release() -> None:
    root = Path(__file__).resolve().parents[2]
    section = changelog_section((root / "CHANGELOG.md").read_text(), VERSION)

    assert section is not None
    assert "### Added" in section
    assert "releases/tag" not in section


def make_wheel(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "x")

    return path


def test_wheel_with_dashboard_assets_passes(tmp_path: Path) -> None:
    wheel = make_wheel(
        tmp_path / "ok.whl",
        ["buildscope/__init__.py", *DASHBOARD_ASSETS],
    )

    assert check_wheel_assets(wheel).ok


def test_wheel_missing_assets_fails(tmp_path: Path) -> None:
    wheel = make_wheel(tmp_path / "bare.whl", ["buildscope/__init__.py"])

    finding = check_wheel_assets(wheel)

    assert not finding.ok
    assert "app.js" in finding.detail


def test_wheel_with_private_files_fails(tmp_path: Path) -> None:
    wheel = make_wheel(
        tmp_path / "leaky.whl",
        [*DASHBOARD_ASSETS, "data/buildscope.db"],
    )

    finding = check_wheel_assets(wheel)

    assert not finding.ok
    assert "buildscope.db" in finding.detail


def test_notes_command_prints_changelog_section(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert release_main(["notes", "--root", str(repo)]) == 0
    assert capsys.readouterr().out.strip() == "### Added\n\n- Pipeline runner."

    assert release_main(["notes", "--root", str(repo), "--version", "9.9.9"]) == 1
