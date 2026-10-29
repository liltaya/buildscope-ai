"""Bounded POSIX subprocess execution with cancellation and process groups"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from time import monotonic
from typing import BinaryIO, cast


class ExecutionError(Exception):
    """Base error for execution infrastructure failures."""


class ExecutionValidationError(ExecutionError):
    """Execution arguments or paths are invalid."""


class ExecutionLaunchError(ExecutionError):
    """The executable could not be started."""


class ExecutionTimeoutError(ExecutionError):
    """The command exceeded its timeout and its process group was stopped."""

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: int,
        stdout: str,
        stderr: str,
        stdout_path: Path,
        stderr_path: Path,
    ) -> None:
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.stdout = stdout
        self.stderr = stderr
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path


class ExecutionCancelledError(ExecutionError):
    """The caller requested cancellation."""

    def __init__(
        self,
        message: str,
        *,
        stdout: str,
        stderr: str,
        stdout_path: Path,
        stderr_path: Path,
    ) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    command: tuple[str, ...]
    repository_root: Path
    working_directory: Path = Path(".")
    timeout_seconds: int = 300
    log_directory: Path | None = None
    max_output_bytes: int = 1_048_576


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    command: tuple[str, ...]
    working_directory: Path
    returncode: int
    stdout: str
    stderr: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    stdout_path: Path
    stderr_path: Path
    stdout_truncated: bool
    stderr_truncated: bool

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class _OutputSink:
    """Write at most limit bytes, but always continue draining the pipe."""

    def __init__(self, target: BinaryIO, limit: int) -> None:
        self.target = target
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def add(self, chunk: bytes) -> None:
        capacity = self.limit - len(self.data)
        keep = chunk[:capacity]

        if keep:
            self.target.write(keep)
            self.target.flush()
            self.data.extend(keep)

        if len(keep) < len(chunk):
            self.truncated = True

    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


def _validate_request(request: ExecutionRequest) -> Path:
    if not isinstance(request.command, tuple) or not 1 <= len(request.command) <= 64:
        raise ExecutionValidationError("Command must be a tuple with 1-64 arguments.")

    if any(
        not isinstance(arg, str)
        or not arg.strip()
        or "\x00" in arg
        or len(arg.encode("utf-8")) > 8192
        for arg in request.command
    ):
        raise ExecutionValidationError("Command arguments must be valid nonblank strings.")

    if (
        isinstance(request.timeout_seconds, bool)
        or not isinstance(request.timeout_seconds, int)
        or not 1 <= request.timeout_seconds <= 3600
    ):
        raise ExecutionValidationError("timeout_seconds must be an integer from 1 to 3600.")

    if (
        isinstance(request.max_output_bytes, bool)
        or not isinstance(request.max_output_bytes, int)
        or not 1 <= request.max_output_bytes <= 10_485_760
    ):
        raise ExecutionValidationError("max_output_bytes must be from 1 to 10485760.")

    if not isinstance(request.repository_root, Path) or not isinstance(
        request.working_directory, Path
    ):
        raise ExecutionValidationError("Repository root and working directory must be Paths.")

    if request.working_directory.is_absolute():
        raise ExecutionValidationError("Working directory must be repository-relative.")

    if request.log_directory is not None and not isinstance(request.log_directory, Path):
        raise ExecutionValidationError("log_directory must be a Path or None.")

    try:
        root = request.repository_root.expanduser().resolve(strict=True)
        cwd = (root / request.working_directory).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ExecutionValidationError("Repository root and working directory must exist.") from exc

    if not root.is_dir() or not cwd.is_dir() or not cwd.is_relative_to(root):
        raise ExecutionValidationError("Working directory must be inside repository root.")

    return cwd


def _send_group_signal(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def execute_command(
    request: ExecutionRequest,
    *,
    cancel_event: Event | None = None,
) -> ExecutionResult:
    """Run an explicitly approved command, bounding output and cleanup time.

    macOS/POSIX only; not a sandbox. Cancels processes remaining in the
    process group created by start_new_session=True.
    """
    cwd = _validate_request(request)

    if os.name != "posix":
        raise ExecutionError("Process-group execution requires POSIX.")

    logs_root = (
        request.log_directory
        if request.log_directory is not None
        else Path.cwd() / "data" / "execution-logs"
    )

    try:
        logs_root.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=logs_root)).resolve()
    except OSError as exc:
        raise ExecutionLaunchError(f"Cannot create log directory: {exc}") from exc

    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"

    started_at = datetime.now(UTC)
    started_mono = monotonic()

    reason: str | None = None
    termination_started: float | None = None
    force_sent = False
    leader_exited_at: float | None = None

    try:
        with (
            stdout_path.open("xb") as stdout_file,
            stderr_path.open("xb") as stderr_file,
        ):
            output = _OutputSink(stdout_file, request.max_output_bytes)
            errors = _OutputSink(stderr_file, request.max_output_bytes)

            if cancel_event is not None and cancel_event.is_set():
                raise ExecutionCancelledError(
                    "Execution was cancelled before launch.",
                    stdout="",
                    stderr="",
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )

            try:
                process = subprocess.Popen(
                    request.command,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    start_new_session=True,
                    bufsize=0,
                )
            except (FileNotFoundError, PermissionError, OSError) as exc:
                raise ExecutionLaunchError(
                    f"Could not start {request.command[0]!r}: {exc}"
                ) from exc

            assert process.stdout is not None
            assert process.stderr is not None

            streams = {
                process.stdout: output,
                process.stderr: errors,
            }

            try:
                with selectors.DefaultSelector() as selector:
                    for pipe in streams:
                        selector.register(pipe, selectors.EVENT_READ)

                    while selector.get_map() or process.poll() is None:
                        now = monotonic()
                        running = process.poll() is None

                        if not running and leader_exited_at is None:
                            leader_exited_at = now

                        if reason is None:
                            if cancel_event is not None and cancel_event.is_set():
                                reason = "cancelled"
                            elif now - started_mono >= request.timeout_seconds:
                                reason = "timeout"
                            elif (
                                leader_exited_at is not None
                                and selector.get_map()
                                and now - leader_exited_at >= 0.5
                            ):
                                reason = "lingering_pipes"

                            if reason is not None:
                                termination_started = now
                                _send_group_signal(process.pid, signal.SIGTERM)

                        if (
                            reason is not None
                            and termination_started is not None
                            and not force_sent
                            and now - termination_started >= 0.4
                        ):
                            _send_group_signal(process.pid, signal.SIGKILL)
                            force_sent = True

                        # Avoid waiting forever for streams held by
                        # descendants that escaped the process group.
                        if (
                            reason is not None
                            and termination_started is not None
                            and now - termination_started >= 2.0
                        ):
                            for key in list(selector.get_map().values()):
                                selector.unregister(key.fileobj)
                                cast(BinaryIO, key.fileobj).close()

                            if process.poll() is not None:
                                break

                        for key, _ in selector.select(timeout=0.05):
                            pipe = cast(BinaryIO, key.fileobj)
                            chunk = os.read(pipe.fileno(), 64 * 1024)

                            if chunk:
                                streams[pipe].add(chunk)
                            else:
                                selector.unregister(pipe)
                                pipe.close()

                if process.poll() is None:
                    _send_group_signal(process.pid, signal.SIGKILL)

                exit_code = process.wait(timeout=2)

            except BaseException:
                _send_group_signal(process.pid, signal.SIGKILL)

                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

                raise

            stdout_file.flush()
            stderr_file.flush()

            stdout = output.text()
            stderr = errors.text()

    except OSError as exc:
        raise ExecutionError(f"Log I/O failure: {exc}") from exc

    if reason == "timeout":
        raise ExecutionTimeoutError(
            f"Command exceeded {request.timeout_seconds} seconds.",
            timeout_seconds=request.timeout_seconds,
            stdout=stdout,
            stderr=stderr,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    if reason == "cancelled":
        raise ExecutionCancelledError(
            "Execution cancelled.",
            stdout=stdout,
            stderr=stderr,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    return ExecutionResult(
        command=request.command,
        working_directory=cwd,
        returncode=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        duration_seconds=monotonic() - started_mono,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_truncated=output.truncated,
        stderr_truncated=errors.truncated,
    )
