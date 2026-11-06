"""Bounded, read-only database projections for the local dashboard"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from buildscope.benchmarks.analysis import BenchmarkAnalysisService
from buildscope.database.session import SessionFactory
from buildscope.git.regressions import GitRegressionHistoryService
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import PipelineRunRecord
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

            result["pipelines"] = [
                {
                    "name": definition.name,
                    "version": definition.version,
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
        """Summarize Git, test and benchmark analysis."""
        with self._factory() as session:
            if (
                session.get(
                    Repository,
                    repository_id,
                )
                is None
            ):
                raise DashboardNotFoundError("Repository was not found")

            definition = session.scalar(
                select(PipelineDefinitionRecord.id).where(
                    PipelineDefinitionRecord.repository_id == repository_id,
                    PipelineDefinitionRecord.name == pipeline_name,
                    PipelineDefinitionRecord.version == version,
                )
            )

            if definition is None:
                raise DashboardNotFoundError("Pipeline version was not found")

        reliability = TestReliabilityService(self._factory).analyze(
            repository_id,
            pipeline_name,
            version,
            max_runs=100,
        )

        regressions = GitRegressionHistoryService(self._factory).find_regressions(
            repository_id,
            pipeline_name,
            limit=200,
        )

        benchmarks = BenchmarkAnalysisService(self._factory).compare(
            repository_id,
            pipeline_name,
            version,
            max_runs=100,
        )

        suspects = [
            item
            for item in reliability
            if item.classification.value
            in (
                "RETRY_FLAKY",
                "SAME_COMMIT_VARIABILITY",
                "MIXED_UNATTRIBUTED",
            )
        ]

        matching_regressions = [
            item for item in regressions if item.failing.pipeline_version == version
        ]

        slowdowns = [item for item in benchmarks if item.assessment.value == "SLOWDOWN"]

        return {
            "pipeline_name": pipeline_name,
            "version": version,
            "counts": {
                "test_identities": len(reliability),
                "unstable_tests": len(suspects),
                "git_regressions": len(matching_regressions),
                "benchmark_slowdowns": len(slowdowns),
            },
            "flaky_tests": [
                {
                    "name": item.identity.display_name,
                    "classification": (item.classification.value),
                    "failure_rate": item.failure_rate,
                    "observations": item.observations,
                    "retry_recoveries": (item.retry_recoveries),
                }
                for item in suspects[:15]
            ],
            "git_regressions": [
                {
                    "branch": item.branch,
                    "previous_commit": (item.previous.commit_sha[:12]),
                    "failing_commit": (item.failing.commit_sha[:12]),
                }
                for item in matching_regressions[-15:]
            ],
            "benchmark_comparisons": [
                {
                    "stage": item.stage_name,
                    "branch": item.branch,
                    "previous_commit": (item.baseline_commit[:12]),
                    "current_commit": (item.current_commit[:12]),
                    "slowdown_percent": round(
                        item.slowdown_percent,
                        2,
                    ),
                    "assessment": item.assessment.value,
                }
                for item in benchmarks[-15:]
            ],
        }
