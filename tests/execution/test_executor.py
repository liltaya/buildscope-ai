"""Integration tests for the direct subprocess executor."""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from buildscope.execution import (
    ExecutionLaunchError,
    ExecutionRequest,
    ExecutionTimeoutError,
    ExecutionValidationError,
    execute_command,
)


def request(
    root: Path,
    *command: str,
    working_directory: Path = Path("."),
    timeout_seconds: int = 300,
) -> ExecutionRequest:
    return ExecutionRequest(
        command=command,
        repository_root=root,
        working_directory=working_directory,
        timeout_seconds=timeout_seconds,
    )


def test_success_and_output(tmp_path: Path) -> None:
    result = execute_command(
        request(
            tmp_path,
            sys.executable,
            "-c",
            "print('hello')",
        )
    )

    assert result.succeeded
    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.working_directory == tmp_path.resolve()
    assert result.started_at.utcoffset() == timedelta(0)
    assert result.finished_at >= result.started_at
    assert result.duration_seconds >= 0


def test_nonzero_exit_is_result(tmp_path: Path) -> None:
    result = execute_command(
        request(
            tmp_path,
            sys.executable,
            "-c",
            "import sys; sys.exit(7)",
        )
    )

    assert result.returncode == 7
    assert not result.succeeded


def test_stderr_is_captured(tmp_path: Path) -> None:
    result = execute_command(
        request(
            tmp_path,
            sys.executable,
            "-c",
            "import sys; print('oops', file=sys.stderr)",
        )
    )

    assert result.stderr == "oops\n"


def test_working_directory_is_used(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()

    result = execute_command(
        request(
            tmp_path,
            sys.executable,
            "-c",
            "from pathlib import Path; print(Path.cwd())",
            working_directory=Path("sub"),
        )
    )

    assert result.stdout.strip() == str(sub.resolve())


def test_shell_metacharacters_are_literal(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "should-not-exist"

    suspicious_argument = f"hello; touch {marker}"

    result = execute_command(
        request(
            tmp_path,
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1])",
            suspicious_argument,
        )
    )

    assert result.stdout.strip() == suspicious_argument
    assert not marker.exists()


@pytest.mark.parametrize(
    "command",
    [
        (),
        ("",),
        ("python", " "),
        ("python", "bad\x00arg"),
        (42,),
    ],
)
def test_invalid_command(
    tmp_path: Path,
    command: tuple[object, ...],
) -> None:
    with pytest.raises(ExecutionValidationError):
        execute_command(
            ExecutionRequest(
                command=command,  # type: ignore[arg-type]
                repository_root=tmp_path,
            )
        )


@pytest.mark.parametrize(
    "timeout",
    [0, -1, 3601, True, "3"],
)
def test_invalid_timeout(
    tmp_path: Path,
    timeout: object,
) -> None:
    with pytest.raises(ExecutionValidationError):
        execute_command(
            ExecutionRequest(
                command=(
                    sys.executable,
                    "-c",
                    "print(1)",
                ),
                repository_root=tmp_path,
                timeout_seconds=timeout,  # type: ignore[arg-type]
            )
        )


def test_reject_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ExecutionValidationError):
        execute_command(
            request(
                tmp_path / "missing",
                sys.executable,
            )
        )


def test_reject_file_as_root(tmp_path: Path) -> None:
    file = tmp_path / "file.txt"
    file.write_text("hello")

    with pytest.raises(ExecutionValidationError):
        execute_command(request(file, sys.executable))


def test_reject_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(ExecutionValidationError):
        execute_command(
            request(
                tmp_path,
                sys.executable,
                working_directory=Path(".."),
            )
        )


def test_reject_absolute_working_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(ExecutionValidationError):
        execute_command(
            request(
                tmp_path,
                sys.executable,
                working_directory=tmp_path,
            )
        )


def test_reject_outside_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    outside = tmp_path / "outside"
    outside.mkdir()

    (root / "link").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(ExecutionValidationError):
        execute_command(
            request(
                root,
                sys.executable,
                working_directory=Path("link"),
            )
        )


def test_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(
        ExecutionLaunchError,
        match="Could not start",
    ):
        execute_command(
            request(
                tmp_path,
                "__buildscope_no_such_executable__",
            )
        )


def test_timeout(tmp_path: Path) -> None:
    with pytest.raises(ExecutionTimeoutError) as info:
        execute_command(
            request(
                tmp_path,
                sys.executable,
                "-c",
                "import time; time.sleep(4)",
                timeout_seconds=1,
            )
        )

    assert info.value.timeout_seconds == 1


def test_timeout_partial_output_bytes(
    tmp_path: Path,
) -> None:
    with pytest.raises(ExecutionTimeoutError) as info:
        execute_command(
            request(
                tmp_path,
                sys.executable,
                "-c",
                "import os,time; os.write(1,b'partial'); os.write(2,b'error'); time.sleep(20)",
                timeout_seconds=1,
            )
        )

    assert info.value.stdout == "partial"
    assert info.value.stderr == "error"
    assert info.value.stdout_path.read_bytes() == b"partial"
    assert info.value.stderr_path.read_bytes() == b"error"


def test_no_shell_or_stdin(
    tmp_path: Path,
) -> None:
    original_popen = subprocess.Popen

    with patch(
        "buildscope.execution.executor.subprocess.Popen",
        wraps=original_popen,
    ) as spy:
        execute_command(
            request(
                tmp_path,
                sys.executable,
                "-c",
                "print(1)",
            )
        )

    assert spy.call_args is not None

    arguments = spy.call_args.kwargs

    assert arguments["shell"] is False
    assert arguments["stdin"] == subprocess.DEVNULL
    assert arguments["start_new_session"] is True
    assert arguments["stdout"] == subprocess.PIPE
    assert arguments["stderr"] == subprocess.PIPE


def test_command_runs_without_shell(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "shell-executed"

    result = execute_command(
        request(
            tmp_path,
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1])",
            f"; touch {marker}",
        )
    )

    assert result.succeeded
    assert result.stdout.strip() == f"; touch {marker}"
    assert not marker.exists()
