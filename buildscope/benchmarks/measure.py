"""Measure repeated whole-process wall-clock executions"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _environment_key() -> str:
    """Return a fingerprint of the local execution environment."""
    attributes = (
        platform.system(),
        platform.machine(),
        platform.processor(),
        platform.python_version(),
        platform.node(),
    )

    return hashlib.sha256("\0".join(attributes).encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure whole-process execution time")

    parser.add_argument(
        "--report",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--iterations",
        required=True,
        type=int,
    )

    parser.add_argument(
        "--warmups",
        required=True,
        type=int,
    )

    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
    )

    args = parser.parse_args(argv)

    command = args.command

    if command and command[0] == "--":
        command = command[1:]

    if not command or not 5 <= args.iterations <= 30 or not 0 <= args.warmups <= 5:
        parser.error("Need a command, 5-30 iterations, and 0-5 warmups")

    if not args.report.is_absolute():
        parser.error("--report must be an absolute path")

    if not args.report.parent.is_dir():
        parser.error("Report directory does not exist")

    durations: list[float] = []

    for number in range(args.warmups + args.iterations):
        started = time.perf_counter_ns()

        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                check=False,
            )

        except OSError as exc:
            print(
                f"Benchmark launch failed: {exc}",
                file=sys.stderr,
            )
            return 127

        duration = (time.perf_counter_ns() - started) / 1_000_000_000

        if completed.returncode != 0:
            print(
                f"Benchmark invocation {number + 1} failed with {completed.returncode}",
                file=sys.stderr,
            )

            return completed.returncode if completed.returncode > 0 else 1

        if number >= args.warmups:
            durations.append(duration)

    payload = {
        "schema_version": 1,
        "metric": "process_wall_time",
        "unit": "seconds",
        "iterations": args.iterations,
        "warmups": args.warmups,
        "command_sha256": hashlib.sha256(
            json.dumps(
                command,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
        "environment_key": _environment_key(),
        "samples": durations,
    }

    descriptor, temporary = tempfile.mkstemp(
        prefix=".benchmark-",
        suffix=".tmp",
        dir=args.report.parent,
    )

    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
        ) as stream:
            json.dump(
                payload,
                stream,
                separators=(",", ":"),
            )

            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(
            temporary,
            args.report,
        )

    finally:
        Path(temporary).unlink(missing_ok=True)

    print(f"Benchmark: {len(durations)} samples, report={args.report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
