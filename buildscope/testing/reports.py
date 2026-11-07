"""Bounded parsing of pytest, CTest, and GoogleTest XML reports"""

from __future__ import annotations

import math
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

ReportKind = Literal["pytest", "ctest", "gtest"]
TestOutcome = Literal["PASSED", "FAILED", "ERROR", "SKIPPED"]

MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_TEST_CASES = 10_000


class TestReportError(ValueError):
    """A report is missing, malformed, unsafe, or misconfigured."""


@dataclass(frozen=True, slots=True)
class ParsedTestCase:
    suite_name: str
    class_name: str
    test_name: str
    outcome: TestOutcome
    duration_seconds: float | None
    message: str | None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _elapsed(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None

    text = value.strip().removesuffix("s")

    try:
        seconds = float(text)

    except ValueError as exc:
        raise TestReportError(f"Invalid test duration: {value!r}") from exc

    if not math.isfinite(seconds) or seconds < 0:
        raise TestReportError(f"Invalid test duration: {value!r}")

    return seconds


def parse_test_report(
    path: Path,
) -> tuple[ParsedTestCase, ...]:
    """Parse a bounded JUnit-style XML report.

    DTD and entity declarations are not accepted.
    """
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_REPORT_BYTES + 1)

    except FileNotFoundError as exc:
        raise TestReportError("Test XML report was not generated") from exc

    except OSError as exc:
        raise TestReportError(f"Cannot read test XML report: {exc}") from exc

    if len(raw) > MAX_REPORT_BYTES:
        raise TestReportError("Test XML report exceeds 8 MiB")

    try:
        root = SafeET.fromstring(
            raw,
            forbid_dtd=True,
        )

    except DefusedXmlException as exc:
        raise TestReportError("DTD and XML entity declarations are prohibited") from exc

    except ET.ParseError as exc:
        raise TestReportError(f"Malformed test XML: {exc}") from exc

    if _local_name(root.tag) not in {
        "testsuite",
        "testsuites",
    }:
        raise TestReportError("Expected a testsuite or testsuites root")

    cases: list[ParsedTestCase] = []

    for suite in root.iter():
        if _local_name(suite.tag) != "testsuite":
            continue

        suite_name = suite.get("name", "")[:512]

        for testcase in suite:
            if _local_name(testcase.tag) != "testcase":
                continue

            if len(cases) >= MAX_TEST_CASES:
                raise TestReportError("More than 10000 test cases in report")

            name = testcase.get("name", "").strip()

            if not name:
                raise TestReportError("Test case is missing its name")

            outcomes = {_local_name(child.tag): child for child in testcase}

            diagnostic = None
            outcome: TestOutcome = "PASSED"

            if "error" in outcomes:
                outcome = "ERROR"
                diagnostic = outcomes["error"]

            elif "failure" in outcomes:
                outcome = "FAILED"
                diagnostic = outcomes["failure"]

            elif (
                "skipped" in outcomes
                or testcase.get("status") == "notrun"
                or testcase.get("result") == "suppressed"
            ):
                outcome = "SKIPPED"
                diagnostic = outcomes.get("skipped")

            message: str | None = None

            if diagnostic is not None:
                message = (diagnostic.get("message") or "").strip()

                if not message:
                    message = "".join(diagnostic.itertext()).strip()

                message = message[:2048] or None

            cases.append(
                ParsedTestCase(
                    suite_name=suite_name,
                    class_name=(testcase.get("classname", "")[:512]),
                    test_name=name[:512],
                    outcome=outcome,
                    duration_seconds=_elapsed(testcase.get("time")),
                    message=message,
                )
            )

    return tuple(cases)


def prepare_test_command(
    command: tuple[str, ...],
    kind: ReportKind,
    log_directory: Path | None,
) -> tuple[tuple[str, ...], Path]:
    """Append an XML output flag and create a unique report path."""

    if kind not in {"pytest", "ctest", "gtest"}:
        raise TestReportError(f"Unsupported test report kind: {kind}")

    if not command:
        raise TestReportError("Test command is empty")

    executable = Path(command[0]).name

    if kind == "pytest" and not (
        executable in {"pytest", "py.test"}
        or (len(command) >= 3 and command[1:3] == ("-m", "pytest"))
    ):
        raise TestReportError("pytest report mode requires a pytest command")

    if kind == "ctest" and executable != "ctest":
        raise TestReportError("ctest report mode requires a ctest command")

    forbidden_prefixes = {
        "pytest": (
            "--junitxml",
            "--junit-xml",
        ),
        "ctest": ("--output-junit",),
        "gtest": ("--gtest_output",),
    }[kind]

    if any(arg.startswith(prefix) for arg in command for prefix in forbidden_prefixes):
        raise TestReportError("Command already contains a test-report output flag")

    root = log_directory or (Path.cwd() / "data" / "execution-logs")

    try:
        root.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_dir = Path(
            tempfile.mkdtemp(
                prefix="test-report-",
                dir=root,
            )
        ).resolve()

    except OSError as exc:
        raise TestReportError(f"Cannot create test-report directory: {exc}") from exc

    report_path = output_dir / "results.xml"

    # Explicit variable-length tuple annotation fixes
    # mypy's incompatible tuple assignment error.
    extra: tuple[str, ...]

    if kind == "pytest":
        extra = (f"--junitxml={report_path}",)

    elif kind == "ctest":
        extra = (
            "--output-junit",
            str(report_path),
        )

    else:
        extra = (f"--gtest_output=xml:{report_path}",)

    return command + extra, report_path
