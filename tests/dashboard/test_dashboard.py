"""Dashboard panels with seeded history, empty states and asset safety"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from buildscope.database import create_session_factory, create_sqlite_engine
from buildscope.database.migrate import upgrade_database
from buildscope.demo.__main__ import main as demo_main
from buildscope.demo.seed import (
    COMMITS,
    FIXED_REGRESSION_COMMITS,
    FLAKY_TEST,
    REGRESSION_COMMIT,
    DemoSeedError,
    seed_demo,
)
from buildscope.pipelines.service import PipelineService
from buildscope.repositories.models import Repository
from tests.helpers import local_client

ASSETS = Path(__file__).resolve().parents[2] / "apps" / "api" / "static" / "dashboard"


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    database = tmp_path_factory.mktemp("demo") / "demo.db"
    seed_demo(database)
    return database


def dashboard_client(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    model: Path | None = None,
) -> TestClient:
    monkeypatch.setenv("BUILDSCOPE_ENABLE_LOCAL_DASHBOARD", "1")
    monkeypatch.setenv("BUILDSCOPE_DATABASE_PATH", str(database))

    if model is None:
        monkeypatch.delenv("BUILDSCOPE_ENABLE_LOCAL_PREDICTIONS", raising=False)
        monkeypatch.delenv("BUILDSCOPE_TRUSTED_MODEL_DIR", raising=False)
    else:
        monkeypatch.setenv("BUILDSCOPE_ENABLE_LOCAL_PREDICTIONS", "1")
        monkeypatch.setenv("BUILDSCOPE_TRUSTED_MODEL_DIR", str(model))

    return local_client(create_app(database_path=database))


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, demo: Path) -> Iterator[TestClient]:
    with dashboard_client(monkeypatch, demo) as test_client:
        yield test_client


def selection(client: TestClient) -> dict[str, object]:
    overview = client.get("/dashboard/api/overview").json()

    return {
        "repository_id": overview["selected_repository_id"],
        "pipeline_name": "ci",
        "version": 2,
    }


def test_overview_lists_repository_pipelines_and_versions(client: TestClient) -> None:
    body = client.get("/dashboard/api/overview").json()

    assert [item["name"] for item in body["repositories"]] == ["payments-ledger"]
    versions = {item["version"]: item for item in body["pipelines"]}
    assert set(versions) == {1, 2}
    assert versions[2]["runs"] == 2 * len(COMMITS)
    assert versions[2]["last_status"] == "FAILED"
    assert versions[1]["runs"] == 5


def test_summary_strip(client: TestClient) -> None:
    summary = client.get("/dashboard/api/insights", params=selection(client)).json()[
        "summary"
    ]

    assert summary["runs"] == 2 * len(COMMITS)
    assert 0 < summary["pass_rate"] < 1
    assert summary["open_regressions"] == 1
    assert summary["flaky_tests"] == 1
    assert summary["worst_stage"]["stage"] == "integration-tests"
    assert summary["worst_stage"]["recent_failure_rate"] == 1.0


def test_run_matrix_has_attempts_and_retries(client: TestClient) -> None:
    matrix = client.get("/dashboard/api/insights", params=selection(client)).json()[
        "matrix"
    ]

    assert len(matrix["columns"]) == 30
    assert [row["stage"] for row in matrix["rows"]] == [
        "lint",
        "build",
        "unit-tests",
        "integration-tests",
        "bench-statement-parser",
        "package",
    ]

    cells = [cell for row in matrix["rows"] for cell in row["cells"] if cell]
    statuses = {cell["status"] for cell in cells}
    retried = [cell for cell in cells if len(cell["attempts"]) > 1]

    assert {"SUCCEEDED", "FAILED", "BLOCKED"} <= statuses
    assert retried
    assert [attempt["status"] for attempt in retried[0]["attempts"]] == [
        "FAILED",
        "SUCCEEDED",
    ]
    assert all(attempt["duration_seconds"] > 0 for attempt in retried[0]["attempts"])
    assert all(len(column["commit"]) == 40 for column in matrix["columns"])


def test_stage_graph_has_dependencies_and_last_status(client: TestClient) -> None:
    stages = {
        stage["name"]: stage
        for stage in client.get(
            "/dashboard/api/insights", params=selection(client)
        ).json()["stages"]
    }

    assert stages["package"]["depends_on"] == [
        "lint",
        "unit-tests",
        "integration-tests",
    ]
    assert stages["integration-tests"]["last_status"] == "FAILED"
    assert stages["package"]["last_status"] == "BLOCKED"
    assert stages["build"]["failure_rate"] == 0.0


def test_regression_windows_name_last_good_and_first_bad(client: TestClient) -> None:
    body = client.get("/dashboard/api/insights", params=selection(client)).json()

    assert len(body["commits"]) == len(COMMITS)
    closed, window = body["regressions"]

    assert closed["open"] is False
    assert closed["last_good"]["subject"] == COMMITS[FIXED_REGRESSION_COMMITS[0] - 1]
    assert closed["first_bad"]["subject"] == COMMITS[FIXED_REGRESSION_COMMITS[0]]

    assert window["open"] is True
    assert window["branch"] == "main"
    assert window["last_good"]["subject"] == COMMITS[REGRESSION_COMMIT - 1]
    assert window["first_bad"]["subject"] == COMMITS[REGRESSION_COMMIT]
    assert len(window["first_bad"]["commit"]) == 40


def test_flaky_table_has_history_and_classification(client: TestClient) -> None:
    tests = client.get("/dashboard/api/insights", params=selection(client)).json()[
        "flaky_tests"
    ]
    flaky = next(item for item in tests if item["name"].endswith(FLAKY_TEST))

    assert flaky["classification"] == "RETRY_FLAKY"
    assert flaky["retry_recoveries"] > 0
    assert len(flaky["history"]) == 20
    assert {entry["outcome"] for entry in flaky["history"]} == {"PASSED", "FAILED"}


def test_benchmark_series_has_median_mad_and_slowdown(client: TestClient) -> None:
    [series] = client.get("/dashboard/api/insights", params=selection(client)).json()[
        "benchmarks"
    ]

    points = series["points"]
    slowdowns = [
        point
        for point in points
        if point["comparison"] and point["comparison"]["slowdown"]
    ]

    assert series["stage"] == "bench-statement-parser"
    assert all(point["mad_seconds"] > 0 for point in points)
    assert all(point["samples"] >= 9 for point in points)
    assert len(slowdowns) == 1
    assert slowdowns[0]["comparison"]["slowdown_percent"] > 20


def test_predictions_panel_with_trained_demo_model(
    monkeypatch: pytest.MonkeyPatch,
    demo: Path,
) -> None:
    model = demo.parent / "demo-model"

    with dashboard_client(monkeypatch, demo, model) as client:
        body = client.get("/dashboard/api/predictions", params=selection(client)).json()

    assert body["enabled"] is True
    assert body["calibrated"] is False
    assert body["threshold"] == 0.5

    scores = {item["stage"]: item["score"] for item in body["predictions"]}
    flagged = {item["stage"] for item in body["predictions"] if item["flagged"]}

    assert set(scores) == {
        "lint",
        "build",
        "unit-tests",
        "integration-tests",
        "bench-statement-parser",
        "package",
    }
    assert all(0 <= score <= 1 for score in scores.values())

    # The open regression ranks first, the flaky stage next, and stages that
    # never failed sit below both and under the threshold.
    assert max(scores, key=scores.__getitem__) == "integration-tests"
    assert "integration-tests" in flagged
    assert scores["unit-tests"] > scores["lint"]

    for stage in ("build", "bench-statement-parser", "package"):
        assert scores[stage] < scores["unit-tests"]
        assert stage not in flagged

    # Ranking on the chronological holdout must be well above chance; the
    # dashboard shows a warning below 0.6.
    assert body["holdout"]["rows"] > 0
    assert body["holdout"]["roc_auc"] > 0.75


def test_predictions_panel_explains_how_to_enable(client: TestClient) -> None:
    body = client.get("/dashboard/api/predictions", params=selection(client)).json()

    assert body == {"enabled": False, "reason": "Prediction service is disabled"}


def test_empty_database_returns_empty_panels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "empty.db"
    upgrade_database(database)

    with dashboard_client(monkeypatch, database) as client:
        overview = client.get("/dashboard/api/overview").json()

    assert overview["repositories"] == []
    assert overview["pipelines"] == []
    assert overview["selected_repository_id"] is None


def test_pipeline_without_runs_returns_empty_panels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "fresh.db"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    factory = create_session_factory(engine)

    with factory.begin() as session:
        repository = Repository(name="fresh", local_path=str(tmp_path))
        session.add(repository)
        session.flush()
        repository_id = repository.id

    PipelineService(factory).create_version(
        repository_id,
        "name: ci\nstages:\n  - name: test\n    command: [pytest]\n",
    )
    engine.dispose()

    with dashboard_client(monkeypatch, database) as client:
        body = client.get(
            "/dashboard/api/insights",
            params={
                "repository_id": str(repository_id),
                "pipeline_name": "ci",
                "version": 1,
            },
        ).json()

    assert body["summary"] == {
        "runs": 0,
        "window_runs": 100,
        "pass_rate": None,
        "open_regressions": 0,
        "flaky_tests": 0,
        "worst_stage": None,
    }
    assert body["matrix"] == {"columns": [], "rows": [{"stage": "test", "cells": []}]}
    assert body["stages"][0]["last_status"] is None
    assert body["commits"] == []
    assert body["regressions"] == []
    assert body["flaky_tests"] == []
    assert body["benchmarks"] == []


@pytest.mark.parametrize(
    "hint",
    [
        "python -m buildscope.demo seed",
        "python -m buildscope.workers.cli enqueue",
        "test_report: pytest",
        "benchmark_iterations",
        "buildscope serve --model",
        "POST /repositories",
    ],
)
def test_empty_states_say_what_to_configure(hint: str) -> None:
    assert hint in (ASSETS / "app.js").read_text()


def test_dashboard_reads_never_write(client: TestClient, demo: Path) -> None:
    def snapshot() -> list[tuple[str, int]]:
        with sqlite3.connect(demo) as connection:
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ]
            return [
                (
                    table,
                    connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
                )
                for table in sorted(tables)
            ]

    before = snapshot()
    params = selection(client)

    for path in ("/dashboard/api/insights", "/dashboard/api/predictions"):
        assert client.get(path, params=params).status_code == 200

    assert snapshot() == before


def test_assets_are_served_with_strict_headers(client: TestClient) -> None:
    for path, media in (
        ("/dashboard", "text/html"),
        ("/dashboard/assets/app.js", "application/javascript"),
        ("/dashboard/assets/styles.css", "text/css"),
    ):
        response = client.get(path)

        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith(media)
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"


def test_unknown_asset_is_not_served(client: TestClient) -> None:
    assert client.get("/dashboard/assets/..%2Fmain.py").status_code == 404
    assert client.get("/dashboard/assets/index.html").status_code == 404


def test_assets_need_nothing_beyond_self() -> None:
    html = (ASSETS / "index.html").read_text()
    script = (ASSETS / "app.js").read_text()
    styles = (ASSETS / "styles.css").read_text()

    # The CSP allows only same-origin files: no inline code, no CDNs.
    assert re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html) is None
    assert " style=" not in html
    assert re.search(r"\son[a-z]+\s*=", html) is None
    assert "http://" not in html + styles and "https://" not in html + styles
    assert "@import" not in styles

    # Untrusted strings (repository names, commit subjects) must not be
    # parsed as markup.
    for risky in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "eval(",
        "new Function",
    ):
        assert risky not in script


def test_dashboard_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
    demo: Path,
) -> None:
    monkeypatch.delenv("BUILDSCOPE_ENABLE_LOCAL_DASHBOARD", raising=False)
    monkeypatch.setenv("BUILDSCOPE_DATABASE_PATH", str(demo))

    with local_client(create_app(database_path=demo)) as client:
        assert client.get("/dashboard").status_code == 503


def test_seed_refuses_to_overwrite(demo: Path) -> None:
    with pytest.raises(DemoSeedError, match="Refusing to overwrite"):
        seed_demo(demo, train_model=False)


def test_demo_cli_prints_how_to_open_the_dashboard(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "cli-demo.db"

    assert demo_main(["seed", "--database", str(database), "--no-model"]) == 0

    output = capsys.readouterr().out
    assert f"buildscope serve --database {database}" in output
    assert "--model" not in output
    assert database.is_file()
