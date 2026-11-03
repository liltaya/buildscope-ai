"""Execute trusted stages through the optional C++20 POSIX worker"""

from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path
from threading import Event

from buildscope.execution.executor import (
    ExecutionCancelledError,
    ExecutionError,
    ExecutionLaunchError,
    ExecutionRequest,
    ExecutionResult,
    ExecutionTimeoutError,
    ExecutionValidationError,
    execute_command,
)


class CppStageExecutor:
    """Adapter implementing the normal stage executor interface."""

    def __init__(self, binary: Path) -> None:
        self.binary = binary.expanduser().resolve(strict=True)

        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise ExecutionLaunchError(f"C++ worker is not executable: {self.binary}")

    def __call__(
        self,
        request: ExecutionRequest,
        *,
        cancel_event: Event | None = None,
    ) -> ExecutionResult:
        if os.name != "posix":
            raise ExecutionError("C++ stage worker requires POSIX")

        if not isinstance(request.repository_root, Path) or not isinstance(
            request.working_directory, Path
        ):
            raise ExecutionValidationError("Working directories must be Paths")

        if request.working_directory.is_absolute():
            raise ExecutionValidationError("Working directory must be repository-relative")

        if not isinstance(request.command, tuple) or not 1 <= len(request.command) <= 64:
            raise ExecutionValidationError("Command must contain 1-64 arguments")

        if any(
            not isinstance(arg, str)
            or not arg.strip()
            or "\x00" in arg
            or len(arg.encode("utf-8")) > 8192
            for arg in request.command
        ):
            raise ExecutionValidationError("Invalid command argument")

        if (
            isinstance(request.max_output_bytes, bool)
            or not isinstance(request.max_output_bytes, int)
            or not 1 <= request.max_output_bytes <= 10_485_760
        ):
            raise ExecutionValidationError("Invalid output limit")

        if (
            isinstance(request.timeout_seconds, bool)
            or not isinstance(request.timeout_seconds, int)
            or not 1 <= request.timeout_seconds <= 3600
        ):
            raise ExecutionValidationError("Invalid timeout")

        try:
            root = request.repository_root.expanduser().resolve(strict=True)

            cwd = (root / request.working_directory).resolve(strict=True)

        except (OSError, RuntimeError) as exc:
            raise ExecutionValidationError("Repository directory does not exist") from exc

        if not root.is_dir() or not cwd.is_dir() or not cwd.is_relative_to(root):
            raise ExecutionValidationError("Working directory escapes repository root")

        log_root = request.log_directory or (Path.cwd() / "data" / "execution-logs")

        try:
            log_root.mkdir(
                parents=True,
                exist_ok=True,
            )

            run_dir = Path(
                tempfile.mkdtemp(
                    prefix="cpp-stage-",
                    dir=log_root,
                )
            ).resolve()

        except OSError as exc:
            raise ExecutionLaunchError(f"Could not create native worker logs: {exc}") from exc

        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        args_file = run_dir / "arguments.bin"

        # Length-prefixed arguments preserve spaces,
        # newlines and other special characters.
        with args_file.open("xb") as stream:
            stream.write(b"BSARGS1\n")

            stream.write(
                struct.pack(
                    "<I",
                    len(request.command),
                )
            )

            for argument in request.command:
                raw = argument.encode("utf-8")

                stream.write(struct.pack("<I", len(raw)))

                stream.write(raw)

        native_command = (
            str(self.binary),
            "--cwd",
            str(cwd),
            "--timeout",
            str(request.timeout_seconds),
            "--max-output",
            str(request.max_output_bytes),
            "--stdout",
            str(stdout_path),
            "--stderr",
            str(stderr_path),
            "--args-file",
            str(args_file),
        )

        def captured(path: Path) -> str:
            try:
                raw = path.read_bytes()[: request.max_output_bytes]

                return raw.decode(
                    "utf-8",
                    errors="replace",
                )

            except FileNotFoundError:
                return ""

        try:
            control = execute_command(
                ExecutionRequest(
                    command=native_command,
                    repository_root=root,
                    timeout_seconds=min(
                        3600,
                        request.timeout_seconds + 3,
                    ),
                    log_directory=run_dir / "control",
                    max_output_bytes=4096,
                ),
                cancel_event=cancel_event,
            )

        except ExecutionCancelledError as exc:
            raise ExecutionCancelledError(
                "C++ stage worker cancelled.",
                stdout=captured(stdout_path),
                stderr=captured(stderr_path),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            ) from exc

        except ExecutionTimeoutError as exc:
            raise ExecutionTimeoutError(
                "C++ stage worker supervisory timeout.",
                timeout_seconds=request.timeout_seconds,
                stdout=captured(stdout_path),
                stderr=captured(stderr_path),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            ) from exc

        finally:
            args_file.unlink(missing_ok=True)

        if control.returncode != 0:
            raise ExecutionError(
                f"C++ worker process exited {control.returncode}: {control.stderr}"
            )

        lines = control.stdout.strip().splitlines()

        fields = lines[-1].split("\t") if lines else []

        if len(fields) != 5 or fields[0] != "BSCOPE1":
            raise ExecutionError(f"Invalid C++ worker protocol: {control.stdout!r}")

        _, outcome, code_text, out_trunc, err_trunc = fields

        try:
            code = int(code_text)

        except ValueError as exc:
            raise ExecutionError("Invalid C++ worker exit code") from exc

        if out_trunc not in {"0", "1"} or err_trunc not in {"0", "1"}:
            raise ExecutionError("Invalid C++ worker truncation flags")

        stdout = captured(stdout_path)
        stderr = captured(stderr_path)

        if outcome == "LAUNCH":
            raise ExecutionLaunchError(f"Native stage executable launch failed (errno {code})")

        if outcome == "TIMEOUT":
            raise ExecutionTimeoutError(
                f"Native stage exceeded {request.timeout_seconds} seconds.",
                timeout_seconds=request.timeout_seconds,
                stdout=stdout,
                stderr=stderr,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )

        if outcome == "CANCELLED":
            raise ExecutionCancelledError(
                "Native stage cancelled.",
                stdout=stdout,
                stderr=stderr,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )

        if outcome != "OK":
            raise ExecutionError(f"Native stage worker error: {outcome}")

        return ExecutionResult(
            command=request.command,
            working_directory=cwd,
            returncode=code,
            stdout=stdout,
            stderr=stderr,
            started_at=control.started_at,
            finished_at=control.finished_at,
            duration_seconds=control.duration_seconds,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            stdout_truncated=out_trunc == "1",
            stderr_truncated=err_trunc == "1",
        )
