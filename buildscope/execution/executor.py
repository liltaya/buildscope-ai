"""Synchronous, non-shell local command execution for approved workflows"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic


class ExecutionError(Exception):
    """Base class for command-execution failures."""


class ExecutionValidationError(ExecutionError):
    """An execution request or working directory is invalid."""


class ExecutionLaunchError(ExecutionError):
    """The operating system could not start the requested executable."""


class ExecutionTimeoutError(ExecutionError):
    """The direct subprocess exceeded its allotted time."""

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: int,
        stdout: str,
        stderr: str,
    ) -> None:
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """One explicitly invoked local subprocess command."""

    command: tuple[str, ...]
    repository_root: Path
    working_directory: Path = Path(".")
    timeout_seconds: int = 300


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Captured result from a completed direct subprocess."""

    command: tuple[str, ...]
    working_directory: Path
    returncode: int
    stdout: str
    stderr: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float

    @property
    def succeeded(self) -> bool:
        """Whether the command reported exit status zero."""
        return self.returncode == 0


def _decode_output(value: bytes | str | None) -> str:
    """Normalize partial timeout output, which may be bytes."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _validate_request(request: ExecutionRequest) -> Path:
    """Validate arguments and restrict the working directory to the root."""
    command = request.command

    if not isinstance(command, tuple) or not 1 <= len(command) <= 64:
        raise ExecutionValidationError("Command must be a tuple containing 1-64 string arguments.")

    if any(
        not isinstance(arg, str)
        or not arg.strip()
        or "\x00" in arg
        or len(arg.encode("utf-8")) > 8192
        for arg in command
    ):
        raise ExecutionValidationError(
            "Command arguments must be nonblank strings without NUL bytes "
            "and at most 8192 bytes each."
        )

    if (
        isinstance(request.timeout_seconds, bool)
        or not isinstance(request.timeout_seconds, int)
        or not 1 <= request.timeout_seconds <= 3600
    ):
        raise ExecutionValidationError("timeout_seconds must be an integer between 1 and 3600.")

    if not isinstance(request.repository_root, Path):
        raise ExecutionValidationError("repository_root must be a Path.")

    if not isinstance(request.working_directory, Path):
        raise ExecutionValidationError("working_directory must be a Path.")

    if request.working_directory.is_absolute():
        raise ExecutionValidationError("working_directory must be relative to repository_root.")

    try:
        root = request.repository_root.expanduser().resolve(strict=True)

        cwd = (root / request.working_directory).resolve(strict=True)

    except (OSError, RuntimeError) as exc:
        raise ExecutionValidationError(
            "Repository root and working directory must exist and be accessible."
        ) from exc

    if not root.is_dir() or not cwd.is_dir():
        raise ExecutionValidationError("Repository root and working directory must be directories.")

    if not cwd.is_relative_to(root):
        raise ExecutionValidationError("Working directory must stay within the repository root.")

    return cwd


def execute_command(request: ExecutionRequest) -> ExecutionResult:
    """Run a command directly, capturing its output and exit status.

    Call only for a locally approved command. This is not a security sandbox.
    """
    cwd = _validate_request(request)

    started_at = datetime.now(UTC)
    started_mono = monotonic()

    try:
        completed = subprocess.run(
            request.command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=request.timeout_seconds,
            check=False,
            shell=False,
        )

    except subprocess.TimeoutExpired as exc:
        raise ExecutionTimeoutError(
            f"Command exceeded {request.timeout_seconds} seconds.",
            timeout_seconds=request.timeout_seconds,
            stdout=_decode_output(exc.stdout),
            stderr=_decode_output(exc.stderr),
        ) from exc

    except FileNotFoundError as exc:
        raise ExecutionLaunchError(f"Executable not found: {request.command[0]}") from exc

    except PermissionError as exc:
        raise ExecutionLaunchError(f"Permission denied starting: {request.command[0]}") from exc

    except OSError as exc:
        raise ExecutionLaunchError(f"Could not start executable: {exc}") from exc

    return ExecutionResult(
        command=request.command,
        working_directory=cwd,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        duration_seconds=monotonic() - started_mono,
    )
