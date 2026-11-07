"""Bounded, read-only database projections for the local dashboard"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from buildscope.benchmarks.analysis import BenchmarkAnalysisService
from buildscope.dashboard import panels
from buildscope.database.session import SessionFactory
from buildscope.git.regressions import GitRegressionHistoryService
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import PipelineRunRecord
from buildscope.pipelines.service import PipelineService
from buildscope.repositories.models import Repository
from buildscope.testing.reliability import TestReliabilityService
from buildscope.workers.models import WorkerJobRecord


class DashboardNotFoundError(LookupError):
    """A selected repository or pipeline version does not exist."""


class DashboardService:
    """Read-only projections; never execute commands or mutate sessions."""

    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory

    def overview(
        self,
        repository_id: UUID | None = None,
    ) -> dict[str, object]:
        """Return repositories, summary counters and recent runs."""
        with self._factory() as session:
            repositories = session.scalars(
                select(Repository).order_by(Repository.name, Repository.id).limit(200)
            ).all()

            options = [{"id": str(row.id), "name": row.name} for row in repositories]

            available = {row.id for row in repositories}

            if repository_id is None:
                selected = repositories[0].id if repositories else None

            else:
                if repository_id not in available:
                    requested = session.get(
                        Repository,
                        repository_id,
                    )

                    if requested is None:
                        raise DashboardNotFoundError("Repository was not found")

                    options.append(
                        {
                            "id": str(requested.id),
                            "name": requested.name,
                        }
                    )

                selected = repository_id

            result: dict[str, object] = {
                "repositories": options,
                "selected_repository_id": (str(selected) if selected else None),
                "totals": {
                    "runs": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "running": 0,
                    "queued": 0,
                    "active_jobs": 0,
                },
                "pipelines": [],
                "recent_runs": [],
            }

            if selected is None:
                return result

            counts = dict(
                session.execute(
                    select(
                        PipelineRunRecord.status,
                        func.count(),
                    )
                    .where(PipelineRunRecord.repository_id == selected)
                    .group_by(PipelineRunRecord.status)
                ).all()
            )

            active_jobs = (
                session.scalar(
                    select(func.count())
                    .select_from(WorkerJobRecord)
                    .where(
                        WorkerJobRecord.repository_id == selected,
                        WorkerJobRecord.status.in_(("RUNNING", "QUEUED")),
                    )
                )
                or 0
            )

            result["totals"] = {
                "runs": sum(counts.values()),
                "succeeded": counts.get("SUCCEEDED", 0),
                "failed": (counts.get("FAILED", 0) + counts.get("TIMED_OUT", 0)),
                "running": counts.get("RUNNING", 0),
                "queued": counts.get("QUEUED", 0),
                "active_jobs": active_jobs,
            }

            definitions = session.scalars(
                select(PipelineDefinitionRecord)
                .where(PipelineDefinitionRecord.repository_id == selected)
                .order_by(
                    PipelineDefinitionRecord.name,
                    PipelineDefinitionRecord.version.desc(),
                )
                .limit(200)
            ).all()

            run_counts = dict(
                session.execute(
                    select(PipelineRunRecord.pipeline_definition_id, func.count())
                    .where(
                        PipelineRunRecord.pipeline_definition_id.in_(
                            [definition.id for definition in definitions]
                        )
                    )
                    .group_by(PipelineRunRecord.pipeline_definition_id)
                ).all()
            )

            ranked = (
                select(
                    PipelineRunRecord.pipeline_definition_id,
                    PipelineRunRecord.status,
                    func.row_number()
                    .over(
                        partition_by=PipelineRunRecord.pipeline_definition_id,
                        order_by=(
                            PipelineRunRecord.queued_at.desc(),
                            PipelineRunRecord.id.desc(),
                        ),
                    )
                    .label("rank"),
                )
                .where(
                    PipelineRunRecord.pipeline_definition_id.in_(
                        [definition.id for definition in definitions]
                    )
                )
                .subquery()
            )

            latest_status: dict[UUID, str] = dict(
                session.execute(
                    select(ranked.c.pipeline_definition_id, ranked.c.status).where(
                        ranked.c.rank == 1
                    )
                ).all()
            )

            result["pipelines"] = [
                {
                    "name": definition.name,
                    "version": definition.version,
                    "runs": run_counts.get(definition.id, 0),
                    "last_status": latest_status.get(definition.id),
                    "created_at": definition.created_at.isoformat(),
                }
                for definition in definitions
            ]

            recent = session.execute(
                select(
                    PipelineRunRecord,
                    PipelineDefinitionRecord,
                )
                .join(
                    PipelineDefinitionRecord,
                    PipelineRunRecord.pipeline_definition_id
                    == PipelineDefinitionRecord.id,
                )
                .where(PipelineRunRecord.repository_id == selected)
                .order_by(
                    PipelineRunRecord.queued_at.desc(),
                    PipelineRunRecord.id.desc(),
                )
                .limit(30)
            ).all()

            result["recent_runs"] = [
                {
                    "id": str(run.id),
                    "pipeline": definition.name,
                    "version": definition.version,
                    "status": run.status,
                    "queued_at": run.queued_at.isoformat(),
                    "finished_at": (
                        run.finished_at.isoformat() if run.finished_at else None
                    ),
                    "branch": run.git_branch,
                    "commit": (run.git_commit_sha[:12] if run.git_commit_sha else None),
                }
                for run, definition in recent
            ]

            return result

    def insights(
        self,
        repository_id: UUID,
        pipeline_name: str,
        version: int,
    ) -> dict[str, object]:
        """Return every panel for one saved pipeline version."""
        with self._factory() as session:
            if session.get(Repository, repository_id) is None:
                raise DashboardNotFoundError("Repository was not found")

            definition_id = session.scalar(
                select(PipelineDefinitionRecord.id).where(
                    PipelineDefinitionRecord.repository_id == repository_id,
                    PipelineDefinitionRecord.name == pipeline_name,
                    PipelineDefinitionRecord.version == version,
                )
            )

        if definition_id is None:
            raise DashboardNotFoundError("Pipeline version was not found")

        definition = (
            PipelineService(self._factory)
            .get_version(repository_id, pipeline_name, version)
            .definition
        )

        reliability = TestReliabilityService(self._factory).analyze(
            repository_id,
            pipeline_name,
            version,
            max_runs=panels.HISTORY_RUNS,
        )

        regressions = GitRegressionHistoryService(self._factory).find_regressions(
            repository_id,
            pipeline_name,
            limit=panels.HISTORY_RUNS,
        )

        comparisons = BenchmarkAnalysisService(self._factory).compare(
            repository_id,
            pipeline_name,
            version,
            max_runs=panels.HISTORY_RUNS,
        )

        with self._factory() as session:
            history = panels.recent_runs(session, definition_id, panels.HISTORY_RUNS)
            matrix_runs = history[-panels.MATRIX_RUNS :]
            stage_names = [stage.name for stage in definition.stages]

            matrix = panels.run_matrix(session, matrix_runs, stage_names)
            graph = panels.stage_graph(session, definition, history)
            trend = panels.worst_trending_stage(session, history)
            windows = panels.regression_windows(regressions, history, version)
            timeline = panels.commit_timeline(history)
            flaky = panels.flaky_tests(session, reliability, history)
            benchmarks = panels.benchmark_series(session, history, comparisons)

        finished = [run for run in history if run.status not in {"QUEUED", "RUNNING"}]
        succeeded = sum(run.status == "SUCCEEDED" for run in finished)

        return {
            "pipeline_name": pipeline_name,
            "version": version,
            "summary": {
                "runs": len(history),
                "window_runs": panels.HISTORY_RUNS,
                "pass_rate": (
                    round(succeeded / len(finished), 4) if finished else None
                ),
                "open_regressions": sum(bool(item["open"]) for item in windows),
                "flaky_tests": sum(
                    item["classification"] in panels.FLAKY_CLASSIFICATIONS
                    for item in flaky
                ),
                "worst_stage": trend,
            },
            "stages": graph,
            "matrix": matrix,
            "commits": timeline,
            "regressions": windows,
            "flaky_tests": flaky,
            "benchmarks": benchmarks,
            "test_identities": len(reliability),
        }
