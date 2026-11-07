"""POSIX integration tests for the C++ stage worker"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from buildscope.execution.cpp_backend import CppStageExecutor
from buildscope.execution.executor import (
    ExecutionCancelledError,
    ExecutionLaunchError,
    ExecutionRequest,
    ExecutionResult,
    ExecutionTimeoutError,
    ExecutionValidationError,
)


@pytest.fixture
def native_binary(cpp_worker: Path) -> Path:
    return cpp_worker


def execute(
    binary: Path,
    tmp_path: Path,
    *command: str,
    timeout: int = 5,
    cap: int = 10000,
) -> ExecutionResult:
    return CppStageExecutor(binary)(
        ExecutionRequest(
            command=command,
            repository_root=tmp_path,
            timeout_seconds=timeout,
            max_output_bytes=cap,
            log_directory=tmp_path / "logs",
        )
    )


def test_success_and_separate_logs(
    native_binary: Path,
    tmp_path: Path,
) -> None:
    result = execute(
        native_binary,
        tmp_path,
        sys.executable,
        "-c",
        "import sys; print('out'); print('err', file=sys.stderr)",
    )

    assert result.returncode == 0
    assert result.succeeded

    assert result.stdout == "out\n"
    assert result.stderr == "err\n"

    assert result.stdout_path.read_text() == "out\n"
    assert result.stderr_path.read_text() == "err\n"


def test_nonzero_is_normal_result(
    native_binary: Path,
    tmp_path: Path,
) -> None:
    result = execute(
        native_binary,
        tmp_path,
        sys.executable,
        "-c",
        "import sys; sys.exit(7)",
    )

    assert result.returncode == 7
    assert not result.succeeded


def test_output_is_bounded(
    native_binary: Path,
    tmp_path: Path,
) -> None:
    result = execute(
        native_binary,
        tmp_path,
        sys.executable,
        "-c",
        "print('x' * 200000)",
        cap=101,
    )

    assert result.stdout_path.stat().st_size == 101
    assert result.stdout_truncated


def test_shell_metacharacters_are_literal(
    native_binary: Path,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "should-not-exist"

    payload = f"hello; touch {marker}"

    result = execute(
        native_binary,
        tmp_path,
        sys.executable,
        "-c",
        "import sys; print(sys.argv[1])",
        payload,
    )

    assert result.stdout.strip() == payload
    assert not marker.exists()


def test_newlines_in_args_preserved(
    native_binary: Path,
    tmp_path: Path,
) -> None:
    result = execute(
        native_binary,
        tmp_path,
        sys.executable,
        "-c",
        "import sys; print(repr(sys.argv[1]))",
        "hello\nworld",
    )

    assert result.stdout.strip() == repr("hello\nworld")


def test_missing_executable_is_not_a_test_failure(
    native_binary: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(ExecutionLaunchError):
        execute(
            native_binary,
            tmp_path,
            "___buildscope_missing_exe___",
        )


def test_timeout_preserves_logs(
    native_binary: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(ExecutionTimeoutError) as failure:
        execute(
            native_binary,
            tmp_path,
            sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(10)",
            timeout=1,
        )

    assert "started" in failure.value.stdout

    assert "started" in (failure.value.stdout_path.read_text())


def test_cancellation(
    native_binary: Path,
    tmp_path: Path,
) -> None:
    cancellation = Event()

    backend = CppStageExecutor(native_binary)

    request = ExecutionRequest(
        command=(
            sys.executable,
            "-c",
            "import time; print('start', flush=True); time.sleep(30)",
        ),
        repository_root=tmp_path,
        timeout_seconds=30,
        log_directory=tmp_path / "logs",
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            backend,
            request,
            cancel_event=cancellation,
        )

        time.sleep(0.3)
        cancellation.set()

        with pytest.raises(ExecutionCancelledError):
            future.result(timeout=8)


def test_directory_escape_rejected(
    native_binary: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(ExecutionValidationError):
        CppStageExecutor(native_binary)(
            ExecutionRequest(
                command=(
                    sys.executable,
                    "--version",
                ),
                repository_root=tmp_path,
                working_directory=Path(".."),
            )
        )
