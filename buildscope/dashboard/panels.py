"""Per-panel projections for one saved pipeline version.

Every function takes an open session or session factory, reads a bounded
window of history and returns JSON-ready dictionaries. Nothing here
writes to the database or starts a process.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from buildscope.benchmarks.analysis import (
    BenchmarkComparison,
    SlowdownAssessment,
    median_and_mad,
)
from buildscope.benchmarks.models import BenchmarkReportRecord, BenchmarkSampleRecord
from buildscope.git.regressions import GitRegression
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.run_models import PipelineRunRecord, StageRunRecord
from buildscope.pipelines.schema import PipelineDefinition
from buildscope.testing.models import TestCaseRecord, TestReportRecord
from buildscope.testing.reliability import TestReliabilitySummary

MATRIX_RUNS = 30

HISTORY_RUNS = 100

OUTCOME_HISTORY = 20

TREND_WINDOW = 10

# Mixed results across different commits may be a real regression, so
# they are listed for review but not counted as flaky.
FLAKY_CLASSIFICATIONS = frozenset({"RETRY_FLAKY", "SAME_COMMIT_VARIABILITY"})

UNSTABLE_CLASSIFICATIONS = FLAKY_CLASSIFICATIONS | {"MIXED_UNATTRIBUTED"}

FAILING_STATUSES = frozenset({"FAILED", "TIMED_OUT"})


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _duration(started: datetime | None, finished: datetime | None) -> float | None:
    if started is None or finished is None:
        return None

    return round((finished - started).total_seconds(), 3)


def recent_runs(
    session: Session,
    definition_id: UUID,
    limit: int,
) -> list[PipelineRunRecord]:
    """Return up to limit runs of one pipeline version, oldest first."""
    rows = session.scalars(
        select(PipelineRunRecord)
        .where(PipelineRunRecord.pipeline_definition_id == definition_id)
        .order_by(PipelineRunRecord.queued_at.desc(), PipelineRunRecord.id.desc())
        .limit(limit)
    ).all()

    return list(reversed(rows))


def run_matrix(
    session: Session,
    runs: Sequence[PipelineRunRecord],
    stage_names: Sequence[str],
) -> dict[str, object]:
    """Stage-by-run grid where each cell lists every attempt."""
    run_ids = [run.id for run in runs]
    stages = session.scalars(
        select(StageRunRecord).where(StageRunRecord.pipeline_run_id.in_(run_ids))
    ).all()
    attempts = session.scalars(
        select(StageAttemptRecord)
        .where(StageAttemptRecord.stage_run_id.in_([stage.id for stage in stages]))
        .order_by(StageAttemptRecord.attempt_number)
    ).all()

    attempts_by_stage: dict[UUID, list[StageAttemptRecord]] = defaultdict(list)

    for attempt in attempts:
        attempts_by_stage[attempt.stage_run_id].append(attempt)

    cell: dict[tuple[str, UUID], dict[str, object]] = {}

    for stage in stages:
        cell[(stage.stage_name, stage.pipeline_run_id)] = {
            "status": stage.status,
            "exit_code": stage.exit_code,
            "duration_seconds": _duration(stage.started_at, stage.finished_at),
            "attempts": [
                {
                    "number": attempt.attempt_number,
                    "status": attempt.status,
                    "exit_code": attempt.exit_code,
                    "duration_seconds": _duration(
                        attempt.started_at, attempt.finished_at
                    ),
                }
                for attempt in attempts_by_stage[stage.id]
            ],
        }

    return {
        "columns": [
            {
                "run_id": str(run.id),
                "status": run.status,
                "commit": run.git_commit_sha,
                "subject": run.git_commit_subject,
                "branch": run.git_branch,
                "dirty": run.git_is_dirty,
                "queued_at": _iso(run.queued_at),
            }
            for run in runs
        ],
        "rows": [
            {
                "stage": name,
                "cells": [cell.get((name, run.id)) for run in runs],
            }
            for name in stage_names
        ],
    }


def stage_graph(
    session: Session,
    definition: PipelineDefinition,
    runs: Sequence[PipelineRunRecord],
) -> list[dict[str, object]]:
    """Stages with dependencies, last outcome and observed failure rate."""
    by_stage: dict[str, list[str]] = defaultdict(list)

    if runs:
        rows = session.execute(
            select(StageRunRecord.stage_name, StageRunRecord.status)
            .join(
                PipelineRunRecord,
                StageRunRecord.pipeline_run_id == PipelineRunRecord.id,
            )
            .where(StageRunRecord.pipeline_run_id.in_([run.id for run in runs]))
            .order_by(PipelineRunRecord.queued_at, PipelineRunRecord.id)
        ).all()

        for name, status in rows:
            by_stage[name].append(status)

    graph: list[dict[str, object]] = []

    for position, stage in enumerate(definition.stages):
        statuses = [
            status for status in by_stage[stage.name] if status not in {"BLOCKED"}
        ]
        finished = [
            status for status in statuses if status not in {"QUEUED", "RUNNING"}
        ]
        failures = sum(status in FAILING_STATUSES for status in finished)

        graph.append(
            {
                "name": stage.name,
                "position": position,
                "depends_on": list(stage.depends_on),
                "command": list(stage.command),
                "retry_count": stage.retry_count,
                "timeout_seconds": stage.timeout_seconds,
                "last_status": by_stage[stage.name][-1]
                if by_stage[stage.name]
                else None,
                "executions": len(finished),
                "failure_rate": (
                    round(failures / len(finished), 4) if finished else None
                ),
            }
        )

    return graph


def worst_trending_stage(
    session: Session,
    runs: Sequence[PipelineRunRecord],
) -> dict[str, object] | None:
    """Stage whose failure rate rose most between the last two windows."""
    if len(runs) < 2 * TREND_WINDOW:
        return None

    previous_ids = [run.id for run in runs[-2 * TREND_WINDOW : -TREND_WINDOW]]
    recent_ids = [run.id for run in runs[-TREND_WINDOW:]]

    def rates(run_ids: list[UUID]) -> dict[str, float]:
        totals: dict[str, int] = defaultdict(int)
        failures: dict[str, int] = defaultdict(int)

        for name, status in session.execute(
            select(StageRunRecord.stage_name, StageRunRecord.status).where(
                StageRunRecord.pipeline_run_id.in_(run_ids)
            )
        ):
            if status in {"BLOCKED", "QUEUED", "RUNNING", "CANCELLED"}:
                continue

            totals[name] += 1
            failures[name] += status in FAILING_STATUSES

        return {name: failures[name] / totals[name] for name in totals}

    before = rates(previous_ids)
    after = rates(recent_ids)

    candidates = [
        (after[name] - before.get(name, 0.0), name)
        for name in after
        if after[name] - before.get(name, 0.0) > 0
    ]

    if not candidates:
        return None

    delta, name = max(candidates)

    return {
        "stage": name,
        "window_runs": TREND_WINDOW,
        "previous_failure_rate": round(before.get(name, 0.0), 4),
        "recent_failure_rate": round(after[name], 4),
        "delta": round(delta, 4),
    }


def commit_timeline(runs: Sequence[PipelineRunRecord]) -> list[dict[str, object]]:
    """One entry per commit in first-run order, with its latest outcome."""
    first_run: dict[str, PipelineRunRecord] = {}
    latest_status: dict[str, str] = {}
    total: dict[str, int] = defaultdict(int)
    failed: dict[str, int] = defaultdict(int)

    for run in runs:
        commit = run.git_commit_sha

        if not commit:
            continue

        first_run.setdefault(commit, run)
        latest_status[commit] = run.status
        total[commit] += 1
        failed[commit] += run.status in FAILING_STATUSES

    return [
        {
            "commit": commit,
            "subject": run.git_commit_subject,
            "branch": run.git_branch,
            "committed_at": _iso(run.git_commit_at),
            "runs": total[commit],
            "failed_runs": failed[commit],
            "last_status": latest_status[commit],
        }
        for commit, run in first_run.items()
    ]


def regression_windows(
    regressions: Sequence[GitRegression],
    runs: Sequence[PipelineRunRecord],
    version: int,
) -> list[dict[str, object]]:
    """Last-good to first-bad windows, marked open until a later success."""
    windows: list[dict[str, object]] = []

    for item in regressions:
        if item.failing.pipeline_version != version:
            continue

        recovered = any(
            run.git_branch == item.branch
            and run.status == "SUCCEEDED"
            and run.queued_at > item.failing.queued_at
            for run in runs
        )

        windows.append(
            {
                "branch": item.branch,
                "open": not recovered,
                "last_good": {
                    "run_id": str(item.previous.run_id),
                    "commit": item.previous.commit_sha,
                    "subject": item.previous.commit_subject,
                    "queued_at": _iso(item.previous.queued_at),
                },
                "first_bad": {
                    "run_id": str(item.failing.run_id),
                    "commit": item.failing.commit_sha,
                    "subject": item.failing.commit_subject,
                    "queued_at": _iso(item.failing.queued_at),
                },
            }
        )

    return windows


def flaky_tests(
    session: Session,
    summaries: Sequence[TestReliabilitySummary],
    runs: Sequence[PipelineRunRecord],
) -> list[dict[str, object]]:
    """Unstable tests with their recent outcomes, oldest first."""
    unstable = [
        item
        for item in summaries
        if item.classification.value in UNSTABLE_CLASSIFICATIONS
    ]

    if not unstable or not runs:
        return []

    rows = session.execute(
        select(
            StageRunRecord.stage_name,
            TestCaseRecord.suite_name,
            TestCaseRecord.class_name,
            TestCaseRecord.test_name,
            TestCaseRecord.outcome,
            PipelineRunRecord.git_commit_sha,
        )
        .join(TestReportRecord, TestCaseRecord.test_report_id == TestReportRecord.id)
        .join(
            StageAttemptRecord,
            TestReportRecord.stage_attempt_id == StageAttemptRecord.id,
        )
        .join(StageRunRecord, StageAttemptRecord.stage_run_id == StageRunRecord.id)
        .join(PipelineRunRecord, StageRunRecord.pipeline_run_id == PipelineRunRecord.id)
        .where(PipelineRunRecord.id.in_([run.id for run in runs]))
        .order_by(
            PipelineRunRecord.queued_at,
            PipelineRunRecord.id,
            StageAttemptRecord.attempt_number,
        )
    ).all()

    history: dict[tuple[str, str, str, str], list[dict[str, object]]] = defaultdict(
        list
    )

    for stage, suite, class_name, test, outcome, commit in rows:
        history[(stage, suite, class_name, test)].append(
            {"outcome": outcome, "commit": commit}
        )

    ranked = sorted(
        unstable,
        key=lambda item: (-(item.failure_rate or 0.0), item.identity.display_name),
    )

    return [
        {
            "name": item.identity.display_name,
            "stage": item.identity.stage_name,
            "classification": item.classification.value,
            "failure_rate": item.failure_rate,
            "observations": item.observations,
            "distinct_runs": item.distinct_runs,
            "retry_recoveries": item.retry_recoveries,
            "same_commit_mixed_groups": item.same_commit_mixed_groups,
            "history": history[
                (
                    item.identity.stage_name,
                    item.identity.suite_name,
                    item.identity.class_name,
                    item.identity.test_name,
                )
            ][-OUTCOME_HISTORY:],
        }
        for item in ranked[:25]
    ]


def benchmark_series(
    session: Session,
    runs: Sequence[PipelineRunRecord],
    comparisons: Sequence[BenchmarkComparison],
) -> list[dict[str, object]]:
    """Median and MAD per revision for each benchmarked stage and branch."""
    eligible = [
        run.id
        for run in runs
        if run.status == "SUCCEEDED"
        and run.git_is_dirty is False
        and run.git_commit_sha
        and run.git_branch
    ]

    if not eligible:
        return []

    rows = session.execute(
        select(
            BenchmarkSampleRecord.duration_seconds,
            StageRunRecord.stage_name,
            PipelineRunRecord.git_branch,
            PipelineRunRecord.git_commit_sha,
            PipelineRunRecord.git_commit_subject,
            PipelineRunRecord.queued_at,
        )
        .join(
            BenchmarkReportRecord,
            BenchmarkSampleRecord.benchmark_report_id == BenchmarkReportRecord.id,
        )
        .join(
            StageAttemptRecord,
            BenchmarkReportRecord.stage_attempt_id == StageAttemptRecord.id,
        )
        .join(StageRunRecord, StageAttemptRecord.stage_run_id == StageRunRecord.id)
        .join(PipelineRunRecord, StageRunRecord.pipeline_run_id == PipelineRunRecord.id)
        .where(
            PipelineRunRecord.id.in_(eligible),
            StageAttemptRecord.status == "SUCCEEDED",
            BenchmarkReportRecord.parse_status == "PARSED",
            BenchmarkReportRecord.metric == "process_wall_time",
        )
        .order_by(PipelineRunRecord.queued_at, BenchmarkSampleRecord.iteration)
    ).all()

    samples: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(dict)
    details: dict[str, tuple[str | None, datetime]] = {}

    for seconds, stage, branch, commit, subject, queued_at in rows:
        if branch is None or commit is None:
            continue

        samples[(stage, branch)].setdefault(commit, []).append(seconds)
        details[commit] = (subject, queued_at)

    flagged: dict[tuple[str, str], dict[str, dict[str, object]]] = defaultdict(dict)

    for item in comparisons:
        flagged[(item.stage_name, item.branch)][item.current_commit] = {
            "baseline_commit": item.baseline_commit,
            "slowdown_percent": round(item.slowdown_percent, 2),
            "assessment": item.assessment.value,
            "slowdown": item.assessment == SlowdownAssessment.SLOWDOWN,
        }

    series: list[dict[str, object]] = []

    for (stage, branch), by_commit in sorted(samples.items()):
        points = []

        for commit, durations in by_commit.items():
            median, mad = median_and_mad(durations)
            subject, observed_at = details[commit]
            points.append(
                {
                    "commit": commit,
                    "subject": subject,
                    "observed_at": _iso(observed_at),
                    "samples": len(durations),
                    "median_seconds": round(median, 6),
                    "mad_seconds": round(mad, 6),
                    "comparison": flagged[(stage, branch)].get(commit),
                }
            )

        series.append({"stage": stage, "branch": branch, "points": points})

    return series
