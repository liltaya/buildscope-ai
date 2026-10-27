
"""Rule-based detection of Python and C++ projects."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ProjectDetection:
    """Signals found in a local source directory."""

    language: str | None
    build_system: str | None
    test_framework: str | None
    evidence: tuple[str, ...]
    warnings: tuple[str, ...]


_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "build",
    "node_modules",
    ".tox",
}

_MAX_CONFIG_BYTES = 256_000


def _candidate_directories(root: Path) -> list[Path]:
    """Examine root and up to two levels of likely source directories."""
    directories = [root]

    for child in sorted(root.iterdir()):
        if (
            child.is_dir()
            and not child.is_symlink()
            and child.name not in _SKIP_DIRS
        ):
            directories.append(child)

            for name in ("src", "tests"):
                grandchild = child / name

                if (
                    grandchild.is_dir()
                    and not grandchild.is_symlink()
                ):
                    directories.append(grandchild)

    return directories


def _read_config(
    path: Path,
    root: Path,
    warnings: list[str],
) -> str | None:
    """Read a small configuration or test file without executing it."""
    if not path.is_file():
        return None

    relative_name = path.relative_to(root).as_posix()

    try:
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            warnings.append(
                f"Skipped oversized file: {relative_name}"
            )
            return None

        return path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    except OSError as exc:
        warnings.append(
            f"Could not read {relative_name}: {exc}"
        )
        return None


def _python_backend(
    project: dict[str, Any],
) -> str | None:
    """Determine a Python build backend from pyproject.toml."""
    build = project.get("build-system")

    if not isinstance(build, dict):
        return None

    backend = build.get("build-backend")

    if not isinstance(backend, str):
        return None

    if backend.startswith("setuptools"):
        return "setuptools"

    if backend.startswith("hatchling"):
        return "hatchling"

    if backend.startswith("poetry"):
        return "poetry"

    if backend.startswith("flit_core"):
        return "flit"

    return "pyproject"


def detect_project(directory: Path) -> ProjectDetection:
    """Detect project tools using bounded filesystem inspection."""
    root = directory.expanduser().resolve(strict=True)

    if not root.is_dir():
        raise ValueError(
            "Project path must be an existing directory."
        )

    evidence: set[str] = set()
    warnings: list[str] = []

    directories = _candidate_directories(root)

    def mark(path: Path) -> None:
        evidence.add(
            path.relative_to(root).as_posix()
        )

    # Detect Python project configuration.
    pyproject_path = root / "pyproject.toml"
    pyproject: dict[str, Any] = {}

    pyproject_text = _read_config(
        pyproject_path,
        root,
        warnings,
    )

    if pyproject_text is not None:
        mark(pyproject_path)

        try:
            pyproject = tomllib.loads(pyproject_text)

        except tomllib.TOMLDecodeError:
            warnings.append(
                "Invalid TOML in pyproject.toml; "
                "project metadata not parsed."
            )

    python_markers = (
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "Pipfile",
    )

    python_present = pyproject_path.is_file()

    for filename in python_markers:
        path = root / filename

        if path.is_file():
            mark(path)
            python_present = True

    # Inspect source files without recursing through the whole tree.
    python_source = False
    cpp_source = False
    test_py_files = False
    cpp_test_files: list[Path] = []

    for folder in directories:
        for child in folder.iterdir():
            if not child.is_file():
                continue

            if child.suffix == ".py":
                python_source = True

                if (
                    child.name.startswith("test_")
                    or child.name.endswith("_test.py")
                ):
                    test_py_files = True
                    mark(child)

            if child.suffix in (
                ".cpp",
                ".cc",
                ".cxx",
                ".hpp",
                ".hh",
                ".hxx",
            ):
                cpp_source = True

                if (
                    "test" in child.name.lower()
                    or folder.name.lower() == "tests"
                ):
                    cpp_test_files.append(child)

    python_present |= python_source

    # Detect CMake and C++ configuration.
    cmake_sources: list[str] = []
    cmake_present = False

    for folder in directories:
        cmake = folder / "CMakeLists.txt"

        if cmake.is_file():
            cmake_present = True
            mark(cmake)

            content = _read_config(
                cmake,
                root,
                warnings,
            )

            if content is not None:
                lines = [
                    line.split("#", 1)[0]
                    for line in content.splitlines()
                ]

                cmake_sources.append("\n".join(lines))

    cmake_text = "\n".join(cmake_sources)

    cpp_present = cpp_source or (
        cmake_present
        and bool(
            re.search(
                r"\bCXX\b|\.(?:cpp|cc|cxx)\b",
                cmake_text,
                re.IGNORECASE,
            )
        )
    )

    # Detect pytest.
    pytest_markers = (
        "pytest.ini",
        ".pytest.ini",
        "pytest.toml",
        ".pytest.toml",
        "conftest.py",
        "tests/conftest.py",
    )

    has_pytest = test_py_files or any(
        (root / name).is_file()
        for name in pytest_markers
    )

    tool_settings = pyproject.get("tool")

    if (
        isinstance(tool_settings, dict)
        and "pytest" in tool_settings
    ):
        has_pytest = True

    if has_pytest:
        for name in pytest_markers:
            path = root / name

            if path.is_file():
                mark(path)

    # Detect GoogleTest.
    has_gtest = bool(
        re.search(
            r"\bgtest_(?:discover|add)_tests\s*\(",
            cmake_text,
            re.IGNORECASE,
        )
    )

    if not has_gtest:
        has_gtest = any(
            re.search(
                r'^\s*#\s*include\s*[<"]gtest/gtest\.h[>"]',
                source,
                re.MULTILINE,
            )
            for path in cpp_test_files
            if (
                source := _read_config(
                    path,
                    root,
                    warnings,
                )
            ) is not None
        )

    # Detect CTest.
    has_ctest = bool(
        re.search(
            r"\b(?:include\s*\(\s*CTest\s*\)"
            r"|enable_testing\s*\("
            r"|add_test\s*\()",
            cmake_text,
            re.IGNORECASE,
        )
    )

    if has_gtest:
        has_ctest = True

    # Classify the primary programming language.
    language: str | None

    if python_present and cpp_present:
        language = "mixed"

    elif python_present:
        language = "python"

    elif cpp_present:
        language = "cpp"

    else:
        language = None

    # Classify the build system.
    python_build = _python_backend(pyproject)

    if (
        python_build is None
        and (root / "setup.py").is_file()
    ):
        python_build = "setuptools"

    if (
        python_build is None
        and pyproject_path.is_file()
    ):
        python_build = "pyproject"

    # Explicitly allow None to satisfy mypy.
    build_system: str | None

    if cmake_present and python_build:
        build_system = "mixed"

    elif cmake_present:
        build_system = "cmake"

    else:
        build_system = python_build

    # Classify the testing framework.
    test_framework: str | None

    if has_pytest and (has_gtest or has_ctest):
        test_framework = "mixed"

    elif has_gtest:
        test_framework = "googletest"

    elif has_ctest:
        test_framework = "ctest"

    elif has_pytest:
        test_framework = "pytest"

    else:
        test_framework = None

    if language is None:
        warnings.append(
            "No supported Python/C++ project markers found."
        )

    return ProjectDetection(
        language=language,
        build_system=build_system,
        test_framework=test_framework,
        evidence=tuple(sorted(evidence)),
        warnings=tuple(warnings),
    )
