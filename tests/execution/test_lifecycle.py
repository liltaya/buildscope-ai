"""Tests for subprocess cancellation, log storage, and process cleanup"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from buildscope.execution import (
    ExecutionCancelledError,
    ExecutionLaunchError,
    ExecutionRequest,
    ExecutionTimeoutError,
    ExecutionValidationError,
    execute_command,
)


def make_request(
    tmp_path: Path,
    script: str,
    *,
    timeout: int = 5,
    limit: int = 1024,
) -> ExecutionRequest:
    return ExecutionRequest(
        command=(sys.executable, "-c", script),
        repository_root=tmp_path,
        log_directory=tmp_path / "logs",
        timeout_seconds=timeout,
        max_output_bytes=limit,
    )


def test_success_and_log_files(tmp_path: Path) -> None:
    result = execute_command(make_request(tmp_path, "print('hello')"))

    assert result.succeeded
    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.stdout_path.read_text() == "hello\n"
    assert result.stderr_path.read_bytes() == b""
    assert result.started_at.tzinfo is not None
    assert result.duration_seconds >= 0


def test_nonzero_exit_is_not_infrastructure_error(
    tmp_path: Path,
) -> None:
    result = execute_command(
        make_request(
            tmp_path,
            "import sys; sys.exit(7)",
        )
    )

    assert result.returncode == 7
    assert not result.succeeded


def test_stdout_and_stderr_are_separate(
    tmp_path: Path,
) -> None:
    result = execute_command(
        make_request(
            tmp_path,
            "import sys; print('out'); print('err', file=sys.stderr)",
        )
    )

    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.stdout_path.read_text() == "out\n"
    assert result.stderr_path.read_text() == "err\n"


def test_large_output_is_bounded_without_deadlock(
    tmp_path: Path,
) -> None:
    result = execute_command(
        make_request(
            tmp_path,
            "import sys; sys.stdout.write('x' * 1000000); sys.stderr.write('y' * 500000)",
            limit=97,
        )
    )

    assert result.succeeded
    assert len(result.stdout_path.read_bytes()) == 97
    assert len(result.stderr_path.read_bytes()) == 97
    assert len(result.stdout) == 97
    assert len(result.stderr) == 97
    assert result.stdout_truncated
    assert result.stderr_truncated


def test_each_run_has_unique_log_files(
    tmp_path: Path,
) -> None:
    first = execute_command(make_request(tmp_path, "print('first')"))

    second = execute_command(make_request(tmp_path, "print('second')"))

    assert first.stdout_path != second.stdout_path
    assert first.stdout_path.read_text() == "first\n"
    assert second.stdout_path.read_text() == "second\n"


def test_timeout_preserves_partial_logs(
    tmp_path: Path,
) -> None:
    with pytest.raises(ExecutionTimeoutError) as info:
        execute_command(
            make_request(
                tmp_path,
                "import time; print('started', flush=True); time.sleep(20)",
                timeout=1,
            )
        )

    error = info.value

    assert error.timeout_seconds == 1
    assert error.stdout == "started\n"
    assert error.stdout_path.read_text() == "started\n"
    assert error.stderr_path.is_file()


def test_timeout_terminates_child_process_group(
    tmp_path: Path,
) -> None:
    heartbeat = tmp_path / "heartbeat"

    child_script = (
        "import pathlib,sys,time; "
        "p=pathlib.Path(sys.argv[1]); "
        "[(p.write_text(str(time.time())),time.sleep(.05)) "
        "for _ in range(400)]"
    )

    parent_script = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',"
        f"{child_script!r},{str(heartbeat)!r}]); "
        "time.sleep(20)"
    )

    with pytest.raises(ExecutionTimeoutError):
        execute_command(
            make_request(
                tmp_path,
                parent_script,
                timeout=3,
            )
        )

    assert heartbeat.exists()

    initial = heartbeat.read_text()
    time.sleep(0.4)

    # The child should no longer be updating the file.
    assert heartbeat.read_text() == initial


def test_running_command_can_be_cancelled(
    tmp_path: Path,
) -> None:
    cancellation = Event()

    execution = make_request(
        tmp_path,
        "import time; print('running', flush=True); time.sleep(30)",
        timeout=30,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            execute_command,
            execution,
            cancel_event=cancellation,
        )

        # Wait until the command has actually produced output.
        deadline = time.monotonic() + 3

        while time.monotonic() < deadline:
            logs = list((tmp_path / "logs").glob("run-*/stdout.log"))

            if logs and "running" in logs[0].read_text():
                break

            time.sleep(0.02)

        cancellation.set()

        with pytest.raises(ExecutionCancelledError) as info:
            future.result(timeout=4)

    assert info.value.stdout == "running\n"
    assert info.value.stdout_path.read_text() == "running\n"


def test_cancel_before_launch(
    tmp_path: Path,
) -> None:
    cancellation = Event()
    cancellation.set()

    with pytest.raises(ExecutionCancelledError):
        execute_command(
            make_request(
                tmp_path,
                "print('should not run')",
            ),
            cancel_event=cancellation,
        )


def test_missing_executable_raises_launch_error(
    tmp_path: Path,
) -> None:
    request = ExecutionRequest(
        command=("__nonexistent_buildscope_command__",),
        repository_root=tmp_path,
        log_directory=tmp_path / "logs",
    )

    with pytest.raises(ExecutionLaunchError):
        execute_command(request)


def test_working_directory_cannot_escape_root(
    tmp_path: Path,
) -> None:
    request = ExecutionRequest(
        command=(sys.executable,),
        repository_root=tmp_path,
        working_directory=Path(".."),
        log_directory=tmp_path / "logs",
    )

    with pytest.raises(ExecutionValidationError):
        execute_command(request)


def test_invalid_output_limit(
    tmp_path: Path,
) -> None:
    request = ExecutionRequest(
        command=(sys.executable,),
        repository_root=tmp_path,
        log_directory=tmp_path / "logs",
        max_output_bytes=0,
    )

    with pytest.raises(ExecutionValidationError):
        execute_command(request)


def test_shell_metacharacters_are_literal(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "should-not-exist"

    request = ExecutionRequest(
        command=(
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1])",
            f"hello; touch {marker}",
        ),
        repository_root=tmp_path,
        log_directory=tmp_path / "logs",
    )

    result = execute_command(request)

    assert not marker.exists()
    assert "touch" in result.stdout
