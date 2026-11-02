"""Scheduling rules for the SQLite worker queue"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.selectable import ScalarSelect

from buildscope.pipelines.state import RunStatus
from buildscope.workers.models import WorkerJobRecord


MIN_PRIORITY = 0
MAX_PRIORITY = 9
DEFAULT_PRIORITY = 5


@dataclass(frozen=True, slots=True)
class SchedulingPolicy:
    """Maximum active pipeline jobs across one worker pool."""

    max_running_jobs: int = 2

    def __post_init__(self) -> None:
        value = self.max_running_jobs

        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 16
        ):
            raise ValueError(
                "max_running_jobs must be an integer from 1 to 16"
            )


def validate_priority(priority: int) -> None:
    """Require an explicit integer priority between 0 and 9."""
    if (
        isinstance(priority, bool)
        or not isinstance(priority, int)
        or not MIN_PRIORITY <= priority <= MAX_PRIORITY
    ):
        raise ValueError(
            "priority must be an integer from 0 to 9"
        )


def normalize_not_before(
    value: datetime | None,
) -> datetime | None:
    """Normalize the earliest execution time to aware UTC."""
    if value is None:
        return None

    if not isinstance(value, datetime):
        raise ValueError(
            "not_before must be a datetime or None"
        )

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "not_before must include a timezone"
        )

    return value.astimezone(UTC)


def candidate_job_id(
    now: datetime,
) -> ScalarSelect[UUID]:
    """Build a scalar SQL query selecting the best eligible job.

    Ordering:
      1. Higher priority
      2. Repository least recently claimed
      3. Oldest queued job
      4. UUID for deterministic ties

    The caller embeds this query inside an atomic UPDATE.
    """
    active = aliased(WorkerJobRecord)
    previous = aliased(WorkerJobRecord)

    # A repository cannot have two RUNNING jobs.
    repository_busy = (
        select(active.id)
        .where(
            active.repository_id
            == WorkerJobRecord.repository_id,
            active.status
            == RunStatus.RUNNING.value,
        )
        .exists()
    )

    # Last time this repository received a job.
    last_claimed = (
        select(func.max(previous.started_at))
        .where(
            previous.repository_id
            == WorkerJobRecord.repository_id
        )
        .correlate(WorkerJobRecord)
        .scalar_subquery()
    )

    return (
        select(WorkerJobRecord.id)
        .where(
            WorkerJobRecord.status
            == RunStatus.QUEUED.value,
            or_(
                WorkerJobRecord.not_before.is_(None),
                WorkerJobRecord.not_before <= now,
            ),
            ~repository_busy,
        )
        .order_by(
            WorkerJobRecord.priority.desc(),
            last_claimed.asc().nulls_first(),
            WorkerJobRecord.queued_at,
            WorkerJobRecord.id,
        )
        .limit(1)
        .scalar_subquery()
    )
