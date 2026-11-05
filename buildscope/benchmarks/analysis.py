"""Conservative slowdown detection across clean Git revisions"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from uuid import UUID

from sqlalchemy import select

from buildscope.benchmarks.models import (
    BenchmarkReportRecord,
    BenchmarkSampleRecord,
)
from buildscope.database.session import SessionFactory
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.pipelines.state import RunStatus


class SlowdownAssessment(StrEnum):
    SLOWDOWN = "SLOWDOWN"
    NO_CLEAR_SLOWDOWN = "NO_CLEAR_SLOWDOWN"
    INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES"


@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    stage_name: str
    branch: str
    baseline_commit: str
    current_commit: str
    environment_key: str
    command_sha256: str
    baseline_samples: int
    current_samples: int
    baseline_median_seconds: float
    current_median_seconds: float
    baseline_mad_seconds: float
    current_mad_seconds: float
    slowdown_percent: float
    assessment: SlowdownAssessment


@dataclass(slots=True)
class _CommitGroup:
    commit_sha: str
    last_observed_at: datetime
    durations: list[float]


def _median_and_mad(
    values: list[float],
) -> tuple[float, float]:
    """Calculate median and median absolute deviation."""

    center = statistics.median(values)

    deviation = statistics.median(abs(value - center) for value in values)

    return center, deviation


class BenchmarkAnalysisService:
    """Compare process runtimes on a matching host and pipeline."""

    def __init__(
        self,
        session_factory: SessionFactory,
    ) -> None:
        self._factory = session_factory

    def compare(
        self,
        repository_id: UUID,
        pipeline_name: str,
        version: int,
        *,
        branch: str | None = None,
        max_runs: int = 200,
        min_samples: int = 5,
        slowdown_threshold: float = 0.20,
        noise_multiplier: float = 3.0,
    ) -> tuple[BenchmarkComparison, ...]:
        """Compare adjacent observed, clean Git revision groups."""

        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be positive")

        if not pipeline_name.strip():
            raise ValueError("pipeline_name is required")

        if (
            isinstance(max_runs, bool)
            or not isinstance(max_runs, int)
            or not 1 <= max_runs <= 1000
        ):
            raise ValueError("max_runs must be between 1 and 1000")

        if (
            isinstance(min_samples, bool)
            or not isinstance(min_samples, int)
            or not 5 <= min_samples <= 100
        ):
            raise ValueError("min_samples must be between 5 and 100")

        if (
            isinstance(slowdown_threshold, bool)
            or not isinstance(
                slowdown_threshold,
                (int, float),
            )
            or not 0.01 <= slowdown_threshold <= 1.0
        ):
            raise ValueError("slowdown_threshold must be between 0.01 and 1.0")

        if (
            isinstance(noise_multiplier, bool)
            or not isinstance(
                noise_multiplier,
                (int, float),
            )
            or not 1.0 <= noise_multiplier <= 10.0
        ):
            raise ValueError("noise_multiplier must be between 1 and 10")

        with self._factory() as session:
            runs_query = (
                select(PipelineRunRecord.id)
                .join(
                    PipelineDefinitionRecord,
                    PipelineRunRecord.pipeline_definition_id
                    == PipelineDefinitionRecord.id,
                )
                .where(
                    PipelineRunRecord.repository_id == repository_id,
                    PipelineDefinitionRecord.name == pipeline_name,
                    PipelineDefinitionRecord.version == version,
                    PipelineRunRecord.status == RunStatus.SUCCEEDED.value,
                    PipelineRunRecord.git_is_dirty.is_(False),
                    PipelineRunRecord.git_commit_sha.is_not(None),
                    PipelineRunRecord.git_branch.is_not(None),
                )
            )

            if branch is not None:
                runs_query = runs_query.where(PipelineRunRecord.git_branch == branch)

            run_ids = list(
                session.scalars(
                    runs_query.order_by(
                        PipelineRunRecord.queued_at.desc(),
                        PipelineRunRecord.id.desc(),
                    ).limit(max_runs)
                ).all()
            )

            if not run_ids:
                return ()

            rows = session.execute(
                select(
                    BenchmarkSampleRecord.duration_seconds,
                    BenchmarkReportRecord,
                    StageRunRecord.stage_name,
                    PipelineRunRecord.git_branch,
                    PipelineRunRecord.git_commit_sha,
                    PipelineRunRecord.queued_at,
                )
                .join(
                    BenchmarkReportRecord,
                    BenchmarkSampleRecord.benchmark_report_id
                    == BenchmarkReportRecord.id,
                )
                .join(
                    StageAttemptRecord,
                    BenchmarkReportRecord.stage_attempt_id == StageAttemptRecord.id,
                )
                .join(
                    StageRunRecord,
                    StageAttemptRecord.stage_run_id == StageRunRecord.id,
                )
                .join(
                    PipelineRunRecord,
                    StageRunRecord.pipeline_run_id == PipelineRunRecord.id,
                )
                .where(
                    PipelineRunRecord.id.in_(run_ids),
                    StageAttemptRecord.status == RunStatus.SUCCEEDED.value,
                    BenchmarkReportRecord.parse_status == "PARSED",
                    BenchmarkReportRecord.metric == "process_wall_time",
                    BenchmarkReportRecord.unit == "seconds",
                )
                .order_by(
                    PipelineRunRecord.queued_at,
                    PipelineRunRecord.id,
                    BenchmarkSampleRecord.iteration,
                )
                .limit(30001)
            ).all()

            if len(rows) > 30000:
                raise ValueError("Too many samples; reduce max_runs")

        # Group by stage, branch, host and exact command.
        grouped: dict[
            tuple[str, str, str, str],
            dict[str, _CommitGroup],
        ] = defaultdict(dict)

        for (
            seconds,
            report,
            stage_name,
            git_branch,
            sha,
            queued_at,
        ) in rows:
            if not (
                git_branch and sha and report.environment_key and report.command_sha256
            ):
                continue

            key = (
                stage_name,
                git_branch,
                report.environment_key,
                report.command_sha256,
            )

            commits = grouped[key]
            group = commits.get(sha)

            if group is None:
                group = _CommitGroup(
                    commit_sha=sha,
                    last_observed_at=queued_at,
                    durations=[],
                )

                commits[sha] = group

            # PLR1730: use max() instead of an if statement.
            group.last_observed_at = max(
                group.last_observed_at,
                queued_at,
            )

            group.durations.append(seconds)

        comparisons: list[BenchmarkComparison] = []

        for (
            stage,
            git_branch,
            environment,
            fingerprint,
        ), commits in grouped.items():
            revisions = sorted(
                commits.values(),
                key=lambda item: (
                    item.last_observed_at,
                    item.commit_sha,
                ),
            )

            # RUF007: pairwise() is the correct operation
            # for comparing adjacent revisions.
            for baseline, current in pairwise(revisions):
                baseline_median, baseline_mad = _median_and_mad(baseline.durations)

                current_median, current_mad = _median_and_mad(current.durations)

                delta = current_median - baseline_median

                percent = 100 * delta / baseline_median

                if (
                    len(baseline.durations) < min_samples
                    or len(current.durations) < min_samples
                ):
                    assessment = SlowdownAssessment.INSUFFICIENT_SAMPLES

                elif (
                    delta >= slowdown_threshold * baseline_median
                    and delta > noise_multiplier * (baseline_mad + current_mad)
                ):
                    assessment = SlowdownAssessment.SLOWDOWN

                else:
                    assessment = SlowdownAssessment.NO_CLEAR_SLOWDOWN

                comparisons.append(
                    BenchmarkComparison(
                        stage_name=stage,
                        branch=git_branch,
                        baseline_commit=baseline.commit_sha,
                        current_commit=current.commit_sha,
                        environment_key=environment,
                        command_sha256=fingerprint,
                        baseline_samples=len(baseline.durations),
                        current_samples=len(current.durations),
                        baseline_median_seconds=baseline_median,
                        current_median_seconds=current_median,
                        baseline_mad_seconds=baseline_mad,
                        current_mad_seconds=current_mad,
                        slowdown_percent=percent,
                        assessment=assessment,
                    )
                )

        return tuple(
            sorted(
                comparisons,
                key=lambda item: (
                    item.branch,
                    item.stage_name,
                    item.current_commit,
                ),
            )
        )
