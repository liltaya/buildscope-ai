"""SQLite integration tests for test results and Git linking"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository
from buildscope.testing.service import (
    TestReportService as ReportService,
)


@pytest.fixture
def context(
    tmp_path: Path,
) -> Iterator[tuple[ReportService, UUID, UUID, Path]]:
    database = tmp_path / "reports.db"

    upgrade_database(database)

    engine = create_sqlite_engine(database)
    factory = create_session_factory(engine)

    with factory.begin() as session:
        repository = Repository(
            name="Test Report Project",
            local_path=str(tmp_path),
        )

        session.add(repository)
        session.flush()

        repository_id = repository.id

    saved = PipelineService(factory).create_version(
        repository_id,
        "name: reports\nstages:\n  - name: tests\n    command: [pytest, -q]\n",
    )

    with factory.begin() as session:
        run = PipelineRunRecord(
            repository_id=repository_id,
            pipeline_definition_id=saved.id,
            status=RunStatus.SUCCEEDED.value,
            queued_at=datetime.now(UTC),
            git_commit_sha="a" * 40,
        )

        session.add(run)
        session.flush()
        run_id = run.id

        stage = StageRunRecord(
            pipeline_run_id=run_id,
            stage_name="tests",
            position=0,
            command=["pytest", "-q"],
            status=RunStatus.SUCCEEDED.value,
        )

        session.add(stage)
        session.flush()

        attempt = StageAttemptRecord(
            stage_run_id=stage.id,
            attempt_number=2,
            status=RunStatus.SUCCEEDED.value,
        )

        session.add(attempt)
        session.flush()

        attempt_id = attempt.id

    yield (
        ReportService(factory),
        attempt_id,
        run_id,
        tmp_path,
    )

    engine.dispose()


def test_ingest_and_query_cases(
    context: tuple[ReportService, UUID, UUID, Path],
) -> None:
    service, attempt_id, run_id, root = context

    report = root / "cases.xml"

    report.write_text(
        '<testsuite name="tests">'
        '<testcase name="ok" classname="tests.test_api"/>'
        '<testcase name="bad" classname="tests.test_api" time="0.2">'
        '<failure message="expected true"/>'
        "</testcase></testsuite>"
    )

    ingested = service.ingest(
        attempt_id,
        "pytest",
        report,
    )

    assert ingested.parse_status == "PARSED"
    assert ingested.case_count == 2
    assert service.get_report(attempt_id) == ingested

    assert [case.outcome for case in service.list_cases(attempt_id)] == [
        "PASSED",
        "FAILED",
    ]

    failures = service.list_failed_for_run(run_id)

    assert len(failures) == 1
    assert failures[0].test_name == "bad"
    assert failures[0].attempt_number == 2
    assert failures[0].stage_name == "tests"
    assert failures[0].commit_sha == "a" * 40


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        (None, "MISSING"),
        ("not xml", "INVALID"),
    ],
)
def test_missing_or_invalid_is_recorded(
    context: tuple[ReportService, UUID, UUID, Path],
    contents: str | None,
    expected: str,
) -> None:
    service, attempt_id, _, root = context

    report = root / "report.xml"

    if contents is not None:
        report.write_text(contents)

    result = service.ingest(
        attempt_id,
        "pytest",
        report,
    )

    assert result.parse_status == expected
    assert result.case_count == 0
    assert result.error_message
    assert service.list_cases(attempt_id) == ()


def test_duplicate_ingestion_rejected(
    context: tuple[ReportService, UUID, UUID, Path],
) -> None:
    service, attempt_id, _, root = context

    report = root / "report.xml"
    report.write_text('<testsuite name="empty"/>')

    service.ingest(
        attempt_id,
        "pytest",
        report,
    )

    with pytest.raises(
        ValueError,
        match="already ingested",
    ):
        service.ingest(
            attempt_id,
            "pytest",
            report,
        )


# Shapes copied from `ctest --output-junit` (CMake 4) and
# `--gtest_output=xml` (GoogleTest 1.18), trimmed to the fields parsed.
CTEST_REPORT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="Linux-c++" tests="3" failures="1" disabled="0" skipped="1"
    hostname="ci" time="0" timestamp="2026-11-07T13:19:55">
  <testcase name="WorkerTest.Success" classname="WorkerTest.Success"
      time="0.0592847" status="run">
    <properties/><system-out>[       OK ]</system-out>
  </testcase>
  <testcase name="WorkerTest.Timeout" classname="WorkerTest.Timeout"
      time="1.08" status="fail">
    <failure message="Failed"/>
  </testcase>
  <testcase name="WorkerTest.Disabled" classname="WorkerTest.Disabled"
      time="0" status="disabled">
    <skipped message="Disabled"/>
  </testcase>
</testsuite>
"""

GTEST_REPORT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites tests="3" failures="1" disabled="1" errors="0" time="0.058"
    name="AllTests">
  <testsuite name="WorkerTest" tests="3" failures="1" disabled="1"
      skipped="0" errors="0" time="0.058">
    <testcase name="Success" file="worker_test.cpp" line="164" status="run"
        result="completed" time="0.01" classname="WorkerTest" />
    <testcase name="Timeout" file="worker_test.cpp" line="201" status="run"
        result="completed" time="1.047" classname="WorkerTest">
      <failure message="Expected equality" type=""/>
    </testcase>
    <testcase name="DISABLED_Slow" file="worker_test.cpp" line="230"
        status="notrun" result="suppressed" time="0" classname="WorkerTest" />
  </testsuite>
</testsuites>
"""


@pytest.mark.parametrize(
    ("kind", "xml"),
    [
        ("ctest", CTEST_REPORT),
        ("gtest", GTEST_REPORT),
    ],
)
def test_native_reports_persist_cases(
    context: tuple[ReportService, UUID, UUID, Path],
    kind: str,
    xml: str,
) -> None:
    service, attempt_id, run_id, root = context
    report = root / f"{kind}.xml"
    report.write_text(xml)

    ingested = service.ingest(attempt_id, kind, report)

    assert ingested.parse_status == "PARSED"
    assert ingested.case_count == 3
    assert ingested.error_message is None
    assert [case.outcome for case in service.list_cases(attempt_id)] == [
        "PASSED",
        "FAILED",
        "SKIPPED",
    ]

    failures = service.list_failed_for_run(run_id)
    assert len(failures) == 1
    assert failures[0].test_name.endswith("Timeout")


@pytest.mark.parametrize("kind", ["ctest", "gtest"])
@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        (None, "MISSING"),
        ('<testsuite name="cut"><testcase name="a"', "INVALID"),
        ("", "INVALID"),
    ],
)
def test_native_missing_or_malformed_is_recorded(
    context: tuple[ReportService, UUID, UUID, Path],
    kind: str,
    contents: str | None,
    expected: str,
) -> None:
    service, attempt_id, _, root = context
    report = root / f"{kind}.xml"

    if contents is not None:
        report.write_text(contents)

    result = service.ingest(attempt_id, kind, report)

    assert result.parse_status == expected
    assert result.case_count == 0
    assert result.error_message
    assert service.list_cases(attempt_id) == ()
    assert service.get_report(attempt_id) == result
