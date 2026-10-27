"""Unit tests for deterministic project detection"""

from pathlib import Path

from buildscope.detection import detect_project


def test_python_pytest(tmp_path: Path) -> None:
    """Detect Python, setuptools, and pytest."""
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools"]\n'
        'build-backend = "setuptools.build_meta"\n'
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
    )

    result = detect_project(tmp_path)

    assert result.language == "python"
    assert result.build_system == "setuptools"
    assert result.test_framework == "pytest"
    assert result.evidence == ("pyproject.toml",)


def test_cpp_with_google_test(tmp_path: Path) -> None:
    """Detect a CMake C++ project using Google Test."""
    (tmp_path / "CMakeLists.txt").write_text("project(Demo LANGUAGES CXX)\ninclude(CTest)\n")

    cpp_worker = tmp_path / "cpp-worker"
    cpp_worker.mkdir()

    (cpp_worker / "CMakeLists.txt").write_text("gtest_discover_tests(tests)\n")

    result = detect_project(tmp_path)

    assert result.language == "cpp"
    assert result.build_system == "cmake"
    assert result.test_framework == "googletest"


def test_cpp_with_ctest(tmp_path: Path) -> None:
    """Detect a CMake project using CTest."""
    (tmp_path / "CMakeLists.txt").write_text(
        "project(Demo LANGUAGES CXX)\nadd_test(NAME example COMMAND example)\n"
    )

    result = detect_project(tmp_path)

    assert result.language == "cpp"
    assert result.build_system == "cmake"
    assert result.test_framework == "ctest"


def test_mixed_project(tmp_path: Path) -> None:
    """Detect a project containing Python and C++."""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

    (tmp_path / "CMakeLists.txt").write_text("project(Demo LANGUAGES CXX)\ninclude(CTest)\n")

    result = detect_project(tmp_path)

    assert result.language == "mixed"
    assert result.build_system == "mixed"
    assert result.test_framework == "mixed"


def test_unsupported_project(tmp_path: Path) -> None:
    """Return warnings for unsupported projects."""
    (tmp_path / "README.md").write_text("No supported project files.")

    result = detect_project(tmp_path)

    assert result.language is None
    assert result.build_system is None
    assert result.test_framework is None
    assert result.warnings


def test_invalid_pyproject_does_not_crash(
    tmp_path: Path,
) -> None:
    """Handle invalid TOML without crashing."""
    (tmp_path / "pyproject.toml").write_text("[tool\n")

    result = detect_project(tmp_path)

    assert result.language == "python"
    assert any("Invalid TOML" in warning for warning in result.warnings)


def test_requirements_file_identifies_python(
    tmp_path: Path,
) -> None:
    """Detect Python using requirements.txt."""
    (tmp_path / "requirements.txt").write_text("requests\n")

    result = detect_project(tmp_path)

    assert result.language == "python"
    assert result.build_system is None


def test_detector_does_not_execute_setup(
    tmp_path: Path,
) -> None:
    """Ensure detection never executes setup.py."""
    (tmp_path / "setup.py").write_text('raise RuntimeError("Should never execute")\n')

    result = detect_project(tmp_path)

    assert result.language == "python"


def test_google_test_header_detection(
    tmp_path: Path,
) -> None:
    """Detect Google Test from C++ source headers."""
    tests = tmp_path / "tests"
    tests.mkdir()

    (tests / "core_test.cpp").write_text("#include <gtest/gtest.h>\nTEST(Sample, Works) {}\n")

    result = detect_project(tmp_path)

    assert result.language == "cpp"
    assert result.test_framework == "googletest"
