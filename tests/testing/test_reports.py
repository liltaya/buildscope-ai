"""Unit and real-tool integration tests for XML test reports"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from buildscope.testing.reports import (
    TestReportError as ReportError,
)
from buildscope.testing.reports import (
    parse_test_report,
    prepare_test_command,
)


def test_pytest_junit_cases(tmp_path: Path) -> None:
    report = tmp_path / "pytest.xml"

    report.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase name="pass" classname="tests.api" time="0.012"/>'
        '<testcase name="fail" time="0.2">'
        '<failure message="expected 2">traceback</failure></testcase>'
        '<testcase name="error">'
        '<error message="RuntimeError"/></testcase>'
        '<testcase name="skip">'
        '<skipped message="unsupported"/></testcase>'
        "</testsuite></testsuites>"
    )

    cases = parse_test_report(report)

    assert [case.outcome for case in cases] == [
        "PASSED",
        "FAILED",
        "ERROR",
        "SKIPPED",
    ]

    assert cases[0].class_name == "tests.api"
    assert cases[1].duration_seconds == 0.2
    assert cases[1].message == "expected 2"


def test_ctest_junit(tmp_path: Path) -> None:
    report = tmp_path / "ctest.xml"

    report.write_text(
        '<testsuite name="CTest">'
        '<testcase name="demo" time="0.120">'
        '<failure message="failed"/>'
        "</testcase></testsuite>"
    )

    cases = parse_test_report(report)

    assert len(cases) == 1
    assert cases[0].suite_name == "CTest"
    assert cases[0].outcome == "FAILED"


def test_gtest_xml(tmp_path: Path) -> None:
    report = tmp_path / "gtest.xml"

    report.write_text(
        '<testsuites><testsuite name="Math">'
        '<testcase name="Add" status="run" '
        'result="completed" time="0.004s"/>'
        '<testcase name="Disabled" status="notrun" '
        'result="suppressed" time="0s"/>'
        "</testsuite></testsuites>"
    )

    cases = parse_test_report(report)

    assert cases[0].duration_seconds == 0.004
    assert cases[1].outcome == "SKIPPED"


@pytest.mark.parametrize(
    "xml",
    [
        "not xml",
        "<data/>",
        "<!DOCTYPE foo><testsuite/>",
        '<testsuite><testcase name="x" time="nan"/></testsuite>',
        '<testsuite><testcase time="0"/></testsuite>',
    ],
)
def test_invalid_xml(
    tmp_path: Path,
    xml: str,
) -> None:
    report = tmp_path / "invalid.xml"
    report.write_text(xml)

    with pytest.raises(ReportError):
        parse_test_report(report)


def test_missing_report(tmp_path: Path) -> None:
    with pytest.raises(ReportError):
        parse_test_report(tmp_path / "missing.xml")


def test_report_size_is_bounded(
    tmp_path: Path,
) -> None:
    report = tmp_path / "oversized.xml"

    report.write_bytes(b"<" + b"x" * (8 * 1024 * 1024 + 1))

    with pytest.raises(
        ReportError,
        match="8 MiB",
    ):
        parse_test_report(report)


def test_unique_report_paths_and_flags(
    tmp_path: Path,
) -> None:
    pytest_cmd, one = prepare_test_command(
        ("pytest", "-q"),
        "pytest",
        tmp_path,
    )

    ctest_cmd, two = prepare_test_command(
        ("ctest", "-V"),
        "ctest",
        tmp_path,
    )

    gtest_cmd, three = prepare_test_command(
        ("./build/tests",),
        "gtest",
        tmp_path,
    )

    assert pytest_cmd[-1] == (f"--junitxml={one}")

    assert ctest_cmd[-2:] == (
        "--output-junit",
        str(two),
    )

    assert gtest_cmd[-1] == (f"--gtest_output=xml:{three}")

    assert len({one, two, three}) == 3


def test_conflicting_flags_rejected(
    tmp_path: Path,
) -> None:
    with pytest.raises(ReportError):
        prepare_test_command(
            ("pytest", "--junitxml=other.xml"),
            "pytest",
            tmp_path,
        )

    with pytest.raises(ReportError):
        prepare_test_command(
            ("echo", "hello"),
            "ctest",
            tmp_path,
        )


def test_real_pytest_produces_test_cases(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    (root / "test_sample.py").write_text(
        "def test_pass():\n    assert 1 + 1 == 2\n\ndef test_fail():\n    assert 1 == 2\n"
    )

    command, report_path = prepare_test_command(
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
        ),
        "pytest",
        tmp_path / "logs",
    )

    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1

    cases = parse_test_report(report_path)

    assert {case.outcome for case in cases} == {
        "PASSED",
        "FAILED",
    }
