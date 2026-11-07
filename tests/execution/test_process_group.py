"""Stage cleanup leaves no descendant processes on either backend"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from buildscope.execution import (
    CppStageExecutor,
    ExecutionCancelledError,
    ExecutionRequest,
    ExecutionResult,
    ExecutionTimeoutError,
    execute_command,
)

type Executor = Callable[..., ExecutionResult]

# Each process records its PID, then the leader and child each start the
# next generation with stdio detached, so pipe EOF cannot reveal them.
PROCESS_TREE = """
import os, subprocess, sys, time

role, pid_file, leader_exits = sys.argv[1], sys.argv[2], sys.argv[3] == "1"

with open(pid_file, "a") as stream:
    stream.write(f"{os.getpid()}\\n")

if role != "grandchild":
    subprocess.Popen(
        [
            sys.executable,
            __file__,
            "child" if role == "leader" else "grandchild",
            pid_file,
            "0",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

if role == "leader":
    deadline = time.monotonic() + 10

    while time.monotonic() < deadline:
        if len(open(pid_file).read().split()) == 3:
            break
        time.sleep(0.02)

    print("tree ready", flush=True)

    if leader_exits:
        sys.exit(0)

time.sleep(60)
"""


@pytest.fixture(params=["python", "cpp"])
def executor(request: pytest.FixtureRequest) -> Executor:
    if request.param == "python":
        return execute_command

    binary: Path = request.getfixturevalue("cpp_worker")

    return CppStageExecutor(binary)


def tree_request(
    tmp_path: Path,
    *,
    leader_exits: bool,
    timeout: int = 30,
) -> tuple[ExecutionRequest, Path]:
    script = tmp_path / "tree.py"
    script.write_text(PROCESS_TREE)
    pid_file = tmp_path / "pids.txt"

    return (
        ExecutionRequest(
            command=(
                sys.executable,
                str(script),
                "leader",
                str(pid_file),
                "1" if leader_exits else "0",
            ),
            repository_root=tmp_path,
            log_directory=tmp_path / "logs",
            timeout_seconds=timeout,
            max_output_bytes=4096,
        ),
        pid_file,
    )


def recorded_pids(pid_file: Path) -> list[int]:
    return [int(value) for value in pid_file.read_text().split()]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    return True


def assert_all_exit(pids: list[int]) -> None:
    # Orphans are reparented to init, which reaps them asynchronously.
    deadline = time.monotonic() + 5

    while time.monotonic() < deadline:
        survivors = [pid for pid in pids if alive(pid)]

        if not survivors:
            return

        time.sleep(0.05)

    pytest.fail(f"processes outlived the stage: {survivors}")


def wait_for_tree(pid_file: Path) -> None:
    deadline = time.monotonic() + 10

    while time.monotonic() < deadline:
        if pid_file.exists() and len(pid_file.read_text().split()) == 3:
            return

        time.sleep(0.02)

    pytest.fail("process tree did not start")


def test_successful_stage_leaves_no_descendants(
    executor: Executor,
    tmp_path: Path,
) -> None:
    request, pid_file = tree_request(tmp_path, leader_exits=True)

    result = executor(request)

    assert result.returncode == 0
    assert "tree ready" in result.stdout
    pids = recorded_pids(pid_file)
    assert len(pids) == 3
    assert_all_exit(pids)


def test_timeout_kills_child_and_grandchild(
    executor: Executor,
    tmp_path: Path,
) -> None:
    request, pid_file = tree_request(tmp_path, leader_exits=False, timeout=2)

    with pytest.raises(ExecutionTimeoutError):
        executor(request)

    pids = recorded_pids(pid_file)
    assert len(pids) == 3
    assert_all_exit(pids)


def test_cancel_kills_child_and_grandchild(
    executor: Executor,
    tmp_path: Path,
) -> None:
    request, pid_file = tree_request(tmp_path, leader_exits=False)
    cancellation = Event()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor, request, cancel_event=cancellation)

        wait_for_tree(pid_file)
        cancellation.set()

        with pytest.raises(ExecutionCancelledError):
            future.result(timeout=10)

    assert_all_exit(recorded_pids(pid_file))
