"""Query pipeline runs by Git revision and identify regressions"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select

from buildscope.database.session import SessionFactory
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import PipelineRunRecord
from buildscope.pipelines.state import RunStatus


@dataclass(frozen=True, slots=True)
class GitRunSummary:
    """One completed pipeline run with Git metadata."""

    run_id: UUID
    pipeline_name: str
    pipeline_version: int
    status: RunStatus
    branch: str | None
    commit_sha: str
    is_dirty: bool
    commit_at: datetime | None
    commit_subject: str | None
    queued_at: datetime


@dataclass(frozen=True, slots=True)
class GitRegression:
    """A clean success-to-failure transition between commits."""

    branch: str
    previous: GitRunSummary
    failing: GitRunSummary


class GitRegressionHistoryService:
    """Read execution history and detect observed regressions."""

    def __init__(
        self,
        session_factory: SessionFactory,
    ) -> None:
        self._factory = session_factory

    def list_history(
        self,
        repository_id: UUID,
        pipeline_name: str,
        *,
        branch: str | None = None,
        limit: int = 100,
    ) -> tuple[GitRunSummary, ...]:
        """Return recent Git-attributed runs chronologically."""
        if isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be from 1 to 1000")

        completed = (
            RunStatus.SUCCEEDED.value,
            RunStatus.FAILED.value,
            RunStatus.TIMED_OUT.value,
            RunStatus.CANCELLED.value,
        )

        query = (
            select(
                PipelineRunRecord,
                PipelineDefinitionRecord,
            )
            .join(
                PipelineDefinitionRecord,
                PipelineRunRecord.pipeline_definition_id == PipelineDefinitionRecord.id,
            )
            .where(
                PipelineRunRecord.repository_id == repository_id,
                PipelineDefinitionRecord.name == pipeline_name,
                PipelineRunRecord.git_commit_sha.is_not(None),
                PipelineRunRecord.status.in_(completed),
            )
            .order_by(
                PipelineRunRecord.queued_at.desc(),
                PipelineRunRecord.id.desc(),
            )
            .limit(limit)
        )

        if branch is not None:
            query = query.where(PipelineRunRecord.git_branch == branch)

        with self._factory() as session:
            records = session.execute(query).all()

            return tuple(
                GitRunSummary(
                    run_id=run.id,
                    pipeline_name=definition.name,
                    pipeline_version=definition.version,
                    status=RunStatus(run.status),
                    branch=run.git_branch,
                    commit_sha=cast(
                        str,
                        run.git_commit_sha,
                    ),
                    is_dirty=bool(run.git_is_dirty),
                    commit_at=run.git_commit_at,
                    commit_subject=run.git_commit_subject,
                    queued_at=run.queued_at,
                )
                for run, definition in reversed(records)
            )

    def find_regressions(
        self,
        repository_id: UUID,
        pipeline_name: str,
        *,
        branch: str | None = None,
        limit: int = 100,
    ) -> tuple[GitRegression, ...]:
        """Find clean success-to-failure transitions across commits.

        Comparisons require the same branch and pipeline version.

        Same-commit failures are not classified as Git regressions.
        """
        history = self.list_history(
            repository_id,
            pipeline_name,
            branch=branch,
            limit=limit,
        )

        previous: dict[
            tuple[str, int],
            GitRunSummary,
        ] = {}

        regressions: list[GitRegression] = []

        for run in history:
            if (
                run.branch is None
                or run.is_dirty
                or run.status
                not in {
                    RunStatus.SUCCEEDED,
                    RunStatus.FAILED,
                }
            ):
                continue

            key = (
                run.branch,
                run.pipeline_version,
            )

            preceding = previous.get(key)

            if (
                preceding is not None
                and preceding.status == RunStatus.SUCCEEDED
                and run.status == RunStatus.FAILED
                and preceding.commit_sha != run.commit_sha
            ):
                regressions.append(
                    GitRegression(
                        branch=run.branch,
                        previous=preceding,
                        failing=run,
                    )
                )

            previous[key] = run

        return tuple(regressions)
