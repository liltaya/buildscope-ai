"""End-to-end test: pytest retries create separate XML reports"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.pipelines.runner import PipelineRunner
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository
from buildscope.testing.service import (
    TestReportService as ReportService,
)


def test_retry_preserves_both_test_reports(
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "project"
    repository_path.mkdir()

    (repository_path / "test_flaky.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def test_eventually_passes():\n"
        "    marker = Path('attempt_counter.txt')\n"
        "    count = int(marker.read_text()) + 1 "
        "if marker.exists() else 1\n"
        "    marker.write_text(str(count))\n"
        "    assert count >= 2\n"
    )

    database_path = tmp_path / "runs.db"
    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)
    factory = create_session_factory(engine)

    try:
        with factory.begin() as session:
            repository = Repository(
                name="Pytest Report Integration",
                local_path=str(repository_path),
            )

            session.add(repository)
            session.flush()

            repository_id = repository.id

        configuration = yaml.safe_dump(
            {
                "name": "report-integration",
                "stages": [
                    {
                        "name": "tests",
                        "command": [
                            sys.executable,
                            "-m",
                            "pytest",
                            "-q",
                            "test_flaky.py",
                        ],
                        "retry_count": 1,
                        "timeout_seconds": 30,
                        "test_report": "pytest",
                    }
                ],
            }
        )

        saved = PipelineService(factory).create_version(
            repository_id,
            configuration,
        )

        run = PipelineRunner(factory).run_version(
            repository_id,
            saved.name,
            saved.version,
            approved=True,
            log_directory=tmp_path / "logs",
        )

        assert run.status == RunStatus.SUCCEEDED

        stage = run.stages[0]

        assert stage.attempt_count == 2

        assert [attempt.status for attempt in stage.attempts] == [
            RunStatus.FAILED,
            RunStatus.SUCCEEDED,
        ]

        reports = ReportService(factory)

        first = stage.attempts[0]
        second = stage.attempts[1]

        first_report = reports.get_report(first.id)
        second_report = reports.get_report(second.id)

        assert first_report is not None
        assert second_report is not None

        assert first_report.parse_status == "PARSED"
        assert second_report.parse_status == "PARSED"

        assert first_report.report_path != (second_report.report_path)

        assert [case.outcome for case in reports.list_cases(first.id)] == ["FAILED"]

        assert [case.outcome for case in reports.list_cases(second.id)] == ["PASSED"]

        failures = reports.list_failed_for_run(run.id)

        assert len(failures) == 1
        assert failures[0].attempt_number == 1

    finally:
        engine.dispose()
