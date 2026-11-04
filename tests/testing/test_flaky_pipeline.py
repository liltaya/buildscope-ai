"""End-to-end integration of test reports and flaky-test detection"""

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
from buildscope.testing.reliability import (
    ReliabilityClassification,
    TestReliabilityService,
)


def test_real_pytest_retry_is_detected(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()

    (repository / "test_unstable.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def test_eventually_passes():\n"
        "    marker = Path('counter.txt')\n"
        "    n = int(marker.read_text()) + 1 "
        "if marker.exists() else 1\n"
        "    marker.write_text(str(n))\n"
        "    assert n >= 2\n"
    )

    database_path = tmp_path / "buildscope.db"
    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)
    factory = create_session_factory(engine)

    try:
        with factory.begin() as session:
            row = Repository(
                name="Flaky Integration",
                local_path=str(repository),
            )

            session.add(row)
            session.flush()

            repository_id = row.id

        definition = yaml.safe_dump(
            {
                "name": "flaky-integration",
                "stages": [
                    {
                        "name": "tests",
                        "command": [
                            sys.executable,
                            "-m",
                            "pytest",
                            "-q",
                            "test_unstable.py",
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
            definition,
        )

        run = PipelineRunner(factory).run_version(
            repository_id,
            saved.name,
            saved.version,
            approved=True,
            log_directory=tmp_path / "logs",
        )

        assert run.status == RunStatus.SUCCEEDED

        assert run.stages[0].attempt_count == 2

        summaries = TestReliabilityService(factory).analyze(
            repository_id,
            saved.name,
            saved.version,
        )

        assert len(summaries) == 1

        result = summaries[0]

        assert result.classification == ReliabilityClassification.RETRY_FLAKY

        assert result.passed == 1
        assert result.failed == 1
        assert result.retry_recoveries == 1
        assert result.failure_rate == 0.5

    finally:
        engine.dispose()
