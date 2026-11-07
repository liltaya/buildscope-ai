"""Source-tree checks that must pass before a release is tagged."""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import buildscope

PRIVATE_PATTERNS = (
    "*.db",
    "*.db-*",
    "*.sqlite",
    "*.sqlite3",
    "*.sqlite-*",
    "*.joblib",
    "*.pkl",
    "*.pickle",
    "*.pem",
    "*.key",
    "*.p12",
    "id_rsa*",
    "id_ed25519*",
    ".env",
    ".env.*",
    ".claude/*",
    "CLAUDE.md",
)

ALLOWED_PRIVATE_LOOKALIKES = frozenset({".env.example"})

REQUIRED_FILES = ("LICENSE", "SECURITY.md", "CHANGELOG.md")

DASHBOARD_ASSETS = (
    "apps/api/static/dashboard/index.html",
    "apps/api/static/dashboard/app.js",
    "apps/api/static/dashboard/styles.css",
)


@dataclass(frozen=True, slots=True)
class Finding:
    name: str
    ok: bool
    detail: str


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def project_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text())
    version = data["project"]["version"]

    if not isinstance(version, str):
        raise TypeError("project.version must be a string")

    return version


def is_private_path(path: str) -> bool:
    """Return whether a tracked path looks like local state or a secret."""
    pure = PurePosixPath(path)

    if pure.name in ALLOWED_PRIVATE_LOOKALIKES:
        return False

    return any(
        fnmatch.fnmatch(pure.name, pattern) or fnmatch.fnmatch(path, pattern)
        for pattern in PRIVATE_PATTERNS
    )


def check_clean_tree(root: Path) -> Finding:
    status = _git(root, "status", "--porcelain", "--untracked-files=normal")
    dirty = [line[3:] for line in status.splitlines() if line.strip()]

    return Finding(
        "clean working tree",
        not dirty,
        "clean" if not dirty else f"uncommitted: {', '.join(dirty[:10])}",
    )


def check_private_files(root: Path) -> Finding:
    tracked = _git(root, "ls-files", "-z").split("\0")
    private = [path for path in tracked if path and is_private_path(path)]

    return Finding(
        "no private files tracked",
        not private,
        "none" if not private else ", ".join(private[:10]),
    )


def check_version(root: Path, tag: str | None) -> Finding:
    version = project_version(root)
    problems: list[str] = []

    if buildscope.__version__ != version:
        problems.append(
            f"buildscope.__version__ is {buildscope.__version__}, "
            f"pyproject says {version}"
        )

    if tag is not None and tag != f"v{version}":
        problems.append(f"tag {tag} does not match v{version}")

    return Finding(
        "version matches" + (" tag" if tag else ""),
        not problems,
        "; ".join(problems) or f"v{version}",
    )


def check_required_files(root: Path) -> Finding:
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]

    return Finding(
        "license, security policy and changelog present",
        not missing,
        "present" if not missing else f"missing: {', '.join(missing)}",
    )


def changelog_section(changelog: str, version: str) -> str | None:
    """Return the body of the changelog section for one version."""
    heading = re.compile(rf"^## \[{re.escape(version)}\][^\n]*$", re.MULTILINE)
    match = heading.search(changelog)

    if match is None:
        return None

    following = re.compile(r"^## \[", re.MULTILINE).search(changelog, match.end())
    end = following.start() if following else len(changelog)

    return changelog[match.end() : end].strip()


def check_changelog(root: Path) -> Finding:
    version = project_version(root)
    path = root / "CHANGELOG.md"
    section = changelog_section(path.read_text(), version) if path.is_file() else None

    return Finding(
        f"changelog entry for {version}",
        bool(section),
        "present" if section else f"no non-empty '## [{version}]' section",
    )


def build_wheel(root: Path, output: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(output), str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    wheels = sorted(output.glob("*.whl"))

    if len(wheels) != 1:
        raise RuntimeError(f"Expected one wheel, found {len(wheels)}")

    return wheels[0]


def check_wheel_assets(wheel: Path) -> Finding:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    missing = [asset for asset in DASHBOARD_ASSETS if asset not in names]
    private = sorted(name for name in names if is_private_path(name))
    problems = []

    if missing:
        problems.append(f"missing {', '.join(missing)}")

    if private:
        problems.append(f"private files {', '.join(private[:5])}")

    return Finding(
        "wheel contains dashboard assets and no private files",
        not problems,
        "; ".join(problems) or wheel.name,
    )


def run_preflight(
    root: Path,
    *,
    tag: str | None = None,
    wheel: Path | None = None,
) -> list[Finding]:
    """Run every source check; callers decide how to report failures."""
    findings = [
        check_clean_tree(root),
        check_private_files(root),
        check_version(root, tag),
        check_required_files(root),
        check_changelog(root),
    ]

    if wheel is not None:
        findings.append(check_wheel_assets(wheel))
        return findings

    with tempfile.TemporaryDirectory(prefix="buildscope-wheel-") as scratch:
        try:
            built = build_wheel(root, Path(scratch))
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            findings.append(Finding("wheel builds", False, str(exc)))
        else:
            findings.append(check_wheel_assets(built))

    return findings
