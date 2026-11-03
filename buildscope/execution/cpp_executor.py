"""C++20 stage execution adapter for BuildScope pipelines"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from threading import Event

from buildscope.execution.executor import (
    ExecutionLaunchError,
    ExecutionRequest,
    ExecutionResult,
    execute_command,
)


class CppStageExecutor:
    """Execute pipeline commands through a native C++ launcher."""

    def __init__(self, executable: Path) -> None:
        path = Path(executable).expanduser().resolve(strict=True)

        if not path.is_file():
            raise ExecutionLaunchError(f"C++ executable is not a file: {path}")

        if not os.access(path, os.X_OK):
            raise ExecutionLaunchError(f"C++ executable is not executable: {path}")

        self._executable = path

    @property
    def executable(self) -> Path:
        return self._executable

    def __call__(
        self,
        request: ExecutionRequest,
        *,
        cancel_event: Event | None = None,
    ) -> ExecutionResult:
        """Execute one stage using the native launcher."""

        native_request = replace(
            request,
            command=(
                str(self._executable),
                "--",
                *request.command,
            ),
        )

        result = execute_command(
            native_request,
            cancel_event=cancel_event,
        )

        return replace(
            result,
            command=request.command,
        )
