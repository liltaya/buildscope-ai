"""Bounded validation and command preparation for benchmark JSON"""

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_REPORT_BYTES = 64 * 1024


class BenchmarkReportError(ValueError):
    """Report missing, invalid, or unsafe to use as measurements"""


@dataclass(frozen=True, slots=True)
class ParsedBenchmarkReport:
    metric: str
    unit: str
    iterations: int
    warmups: int
    command_sha256: str
    environment_key: str
    samples: tuple[float, ...]


def prepare_benchmark_command(
    command: tuple[str, ...],
    *,
    iterations: int,
    warmups: int,
    log_directory: Path | None,
) -> tuple[tuple[str, ...], Path]:
    """Wrap a trusted command and assign it a unique report path"""
    if (
        isinstance(iterations, bool)
        or not isinstance(iterations, int)
        or not 5 <= iterations <= 30
        or isinstance(warmups, bool)
        or not isinstance(warmups, int)
        or not 0 <= warmups <= 5
    ):
        raise BenchmarkReportError("Require 5-30 iterations and 0-5 warmups")

    if not command:
        raise BenchmarkReportError("Benchmark command is empty")

    if len(command) > 55:
        raise BenchmarkReportError("Benchmark command may contain at most 55 arguments")

    parent = log_directory or (Path.cwd() / "data" / "execution-logs")

    try:
        parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        report_dir = Path(
            tempfile.mkdtemp(
                prefix="benchmark-",
                dir=parent,
            )
        ).resolve()

    except OSError as exc:
        raise BenchmarkReportError(
            f"Cannot create benchmark report directory: {exc}"
        ) from exc

    path = report_dir / "measurements.json"

    worker = Path(__file__).with_name("measure.py").resolve(strict=True)

    args = (
        sys.executable,
        str(worker),
        "--report",
        str(path),
        "--iterations",
        str(iterations),
        "--warmups",
        str(warmups),
        "--",
        *command,
    )

    return args, path


def parse_benchmark_report(
    path: Path,
) -> ParsedBenchmarkReport:
    """Validate a bounded process-wall-time JSON report"""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_REPORT_BYTES + 1)

    except FileNotFoundError as exc:
        raise BenchmarkReportError("Benchmark report was not generated") from exc

    except OSError as exc:
        raise BenchmarkReportError(f"Cannot read benchmark report: {exc}") from exc

    if len(raw) > MAX_REPORT_BYTES:
        raise BenchmarkReportError("Benchmark report exceeds 64 KiB")

    try:
        payload = json.loads(raw)

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise BenchmarkReportError("Malformed benchmark JSON") from exc

    keys = {
        "schema_version",
        "metric",
        "unit",
        "iterations",
        "warmups",
        "command_sha256",
        "environment_key",
        "samples",
    }

    if not isinstance(payload, dict) or set(payload) != keys:
        raise BenchmarkReportError("Unexpected benchmark JSON structure")

    iterations = payload["iterations"]
    warmups = payload["warmups"]

    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or payload["metric"] != "process_wall_time"
        or payload["unit"] != "seconds"
        or type(iterations) is not int
        or not 5 <= iterations <= 30
        or type(warmups) is not int
        or not 0 <= warmups <= 5
    ):
        raise BenchmarkReportError("Unsupported benchmark metric or iteration settings")

    fingerprint = payload["command_sha256"]
    environment = payload["environment_key"]

    if any(
        not isinstance(value, str)
        or re.fullmatch(
            r"[0-9a-f]{64}",
            value,
        )
        is None
        for value in (
            fingerprint,
            environment,
        )
    ):
        raise BenchmarkReportError("Invalid workload or environment fingerprint")

    samples = payload["samples"]

    if not isinstance(samples, list) or len(samples) != iterations:
        raise BenchmarkReportError("Sample count does not match iterations")

    if any(
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or not 0 < value <= 3600
        for value in samples
    ):
        raise BenchmarkReportError("Invalid benchmark sample duration")

    return ParsedBenchmarkReport(
        metric="process_wall_time",
        unit="seconds",
        iterations=iterations,
        warmups=warmups,
        command_sha256=fingerprint,
        environment_key=environment,
        samples=tuple(float(value) for value in samples),
    )
