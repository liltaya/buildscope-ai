"""Construct pre-execution features for the stages of a saved pipeline"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from buildscope.database.session import SessionFactory
from buildscope.datasets.failures import (
    MAX_ROWS,
    _command_info,
    _load_observations,
    _Prior,
    stage_features,
)
from buildscope.git.history import capture_git_snapshot
from buildscope.pipelines.graph import build_pipeline_plan
from buildscope.pipelines.service import PipelineService
from buildscope.repositories.models import Repository


class PredictionContextError(ValueError):
    """Repository, pipeline version, or prediction time is invalid."""


@dataclass(frozen=True, slots=True)
class StageFeatures:
    stage_name: str
    stage_position: int
    values: dict[str, object]


def build_live_stage_features(
    factory: SessionFactory,
    repository_id: UUID,
    pipeline_name: str,
    version: int,
    *,
    as_of: datetime | None = None,
) -> tuple[StageFeatures, ...]:
    """Build stage features without enqueuing or executing work.

    as_of exists for deterministic tests.
    Public entrypoints always use the current UTC time.
    """
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PredictionContextError("version must be a positive integer")

    if not pipeline_name.strip():
        raise PredictionContextError("pipeline name is required")

    now = datetime.now(UTC) if as_of is None else as_of

    if now.tzinfo is None or now.utcoffset() is None:
        raise PredictionContextError("as_of must include a timezone")

    now = now.astimezone(UTC)

    with factory() as session:
        repository = session.get(
            Repository,
            repository_id,
        )

        if repository is None:
            raise PredictionContextError("Repository is not registered")

        root = Path(repository.local_path).expanduser().resolve(strict=True)

        if not root.is_dir():
            raise PredictionContextError("Repository path is not a directory")

    saved = PipelineService(factory).get_version(
        repository_id,
        pipeline_name,
        version,
    )

    plan = build_pipeline_plan(saved.definition)

    snapshot = capture_git_snapshot(root)

    # Reuse the dataset export's history so live and training features match.
    # This includes first-attempt test and benchmark data.
    observations = _load_observations(
        factory,
        max_rows=MAX_ROWS,
    )

    history: dict[
        tuple[UUID, str, int, str, str],
        _Prior,
    ] = {}

    for previous in sorted(
        observations,
        key=lambda value: (
            value.finished_at or datetime.max.replace(tzinfo=UTC),
            str(value.run_id),
        ),
    ):
        if (
            previous.finished_at is None
            or previous.finished_at >= now
            or previous.finished_at < previous.queued_at
        ):
            continue

        key = previous.history_key

        if key not in history:
            history[key] = _Prior()

        history[key].add(previous)

    outputs: list[StageFeatures] = []

    for position, stage in enumerate(plan.ordered_stages):
        command_sha, _, _ = _command_info(stage.command)

        key = (
            repository_id,
            saved.name,
            saved.version,
            stage.name,
            command_sha,
        )

        prior = history.get(
            key,
            _Prior(),
        )

        outputs.append(
            StageFeatures(
                stage_name=stage.name,
                stage_position=position,
                values=stage_features(
                    prior,
                    is_dirty=(None if snapshot is None else snapshot.is_dirty),
                    commit_sha=(None if snapshot is None else snapshot.commit_sha),
                    branch=(None if snapshot is None else snapshot.branch),
                ),
            )
        )

    return tuple(outputs)
