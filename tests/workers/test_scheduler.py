"""Integration tests for priority and capacity-based scheduling"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.database.session import SessionFactory
from buildscope.pipelines.service import PipelineService
from buildscope.pipelines.state import RunStatus
from buildscope.repositories.models import Repository
from buildscope.workers.queue import WorkerQueue
from buildscope.workers.scheduler import SchedulingPolicy

type TestContext = tuple[
    WorkerQueue,
    SessionFactory,
    tuple[UUID, UUID, UUID],
]


@pytest.fixture
def context(
    tmp_path: Path,
) -> Iterator[TestContext]:
    database_path = tmp_path / "scheduler.db"

    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)
    factory = create_session_factory(engine)

    repository_ids: list[UUID] = []

    with factory.begin() as session:
        for name in ("alpha", "beta", "gamma"):
            path = tmp_path / name
            path.mkdir()

            repository = Repository(
                name=name,
                local_path=str(path),
            )

            session.add(repository)
            session.flush()
            repository_ids.append(repository.id)

    yaml_text = (
        "name: quality\n"
        "stages:\n"
        "  - name: checks\n"
        f"    command: [{sys.executable!r}, -c, 'print(17)']\n"
    )

    service = PipelineService(factory)

    for repository_id in repository_ids:
        service.create_version(
            repository_id,
            yaml_text,
        )

    yield (
        WorkerQueue(factory),
        factory,
        (
            repository_ids[0],
            repository_ids[1],
            repository_ids[2],
        ),
    )

    engine.dispose()


def enqueue(
    queue: WorkerQueue,
    repository_id: UUID,
    *,
    priority: int = 5,
    not_before: datetime | None = None,
) -> UUID:
    return queue.enqueue_version(
        repository_id,
        "quality",
        1,
        approved=True,
        priority=priority,
        not_before=not_before,
    ).id


def complete(
    queue: WorkerQueue,
    job_id: UUID,
    worker_id: str,
    token: UUID,
) -> None:
    queue.complete(
        job_id,
        worker_id,
        RunStatus.SUCCEEDED,
        claim_token=token,
    )


def test_high_priority_is_selected_first(
    context: TestContext,
) -> None:
    queue, _, (alpha, beta, _) = context

    enqueue(queue, alpha, priority=2)
    high_id = enqueue(queue, beta, priority=9)

    claim = queue.claim_next("worker-1")

    assert claim is not None
    assert claim.id == high_id


def test_default_priority_is_five(
    context: TestContext,
) -> None:
    queue, _, (alpha, _, _) = context

    job = queue.enqueue_version(
        alpha,
        "quality",
        1,
        approved=True,
    )

    assert job.priority == 5
    assert job.not_before is None


def test_fifo_within_one_repository(
    context: TestContext,
) -> None:
    queue, _, (alpha, _, _) = context

    first_id = enqueue(queue, alpha)
    second_id = enqueue(queue, alpha)

    first = queue.claim_next("worker-1")
    assert first is not None
    assert first.id == first_id

    complete(
        queue,
        first.id,
        "worker-1",
        first.claim_token,
    )

    second = queue.claim_next("worker-2")
    assert second is not None
    assert second.id == second_id


def test_equal_priority_rotates_repositories(
    context: TestContext,
) -> None:
    queue, _, (alpha, beta, _) = context

    a1 = enqueue(queue, alpha)
    a2 = enqueue(queue, alpha)
    b1 = enqueue(queue, beta)
    b2 = enqueue(queue, beta)

    expected = [a1, b1, a2, b2]

    actual: list[UUID] = []

    for number in range(4):
        worker = f"worker-{number}"

        claim = queue.claim_next(
            worker,
            max_running_jobs=1,
        )

        assert claim is not None
        actual.append(claim.id)

        complete(
            queue,
            claim.id,
            worker,
            claim.claim_token,
        )

    assert actual == expected


def test_delayed_job_is_not_eligible(
    context: TestContext,
) -> None:
    queue, _, (alpha, beta, _) = context

    future = datetime.now(UTC) + timedelta(hours=2)

    delayed_id = enqueue(
        queue,
        alpha,
        priority=9,
        not_before=future,
    )

    ready_id = enqueue(
        queue,
        beta,
        priority=2,
    )

    claim = queue.claim_next("worker-1")

    assert claim is not None
    assert claim.id == ready_id

    assert queue.get_job(delayed_id).status == RunStatus.QUEUED


def test_global_concurrency_limit(
    context: TestContext,
) -> None:
    queue, _, (alpha, beta, gamma) = context

    enqueue(queue, alpha)
    enqueue(queue, beta)
    enqueue(queue, gamma)

    first = queue.claim_next(
        "worker-1",
        max_running_jobs=2,
    )

    second = queue.claim_next(
        "worker-2",
        max_running_jobs=2,
    )

    third = queue.claim_next(
        "worker-3",
        max_running_jobs=2,
    )

    assert first is not None
    assert second is not None
    assert third is None

    complete(
        queue,
        first.id,
        "worker-1",
        first.claim_token,
    )

    next_job = queue.claim_next(
        "worker-3",
        max_running_jobs=2,
    )

    assert next_job is not None
    assert next_job.repository_id == gamma


def test_same_repository_cannot_overlap(
    context: TestContext,
) -> None:
    queue, _, (alpha, _, _) = context

    enqueue(queue, alpha)
    enqueue(queue, alpha)

    first = queue.claim_next(
        "worker-1",
        max_running_jobs=4,
    )

    assert first is not None

    assert (
        queue.claim_next(
            "worker-2",
            max_running_jobs=4,
        )
        is None
    )

    complete(
        queue,
        first.id,
        "worker-1",
        first.claim_token,
    )

    assert (
        queue.claim_next(
            "worker-2",
            max_running_jobs=4,
        )
        is not None
    )


@pytest.mark.parametrize(
    "priority",
    [-1, 10, True, "9"],
)
def test_invalid_priority_rejected(
    context: TestContext,
    priority: object,
) -> None:
    queue, _, (alpha, _, _) = context

    with pytest.raises(ValueError):
        queue.enqueue_version(
            alpha,
            "quality",
            1,
            approved=True,
            priority=priority,  # type: ignore[arg-type]
        )


def test_naive_not_before_rejected(
    context: TestContext,
) -> None:
    queue, _, (alpha, _, _) = context

    with pytest.raises(ValueError):
        queue.enqueue_version(
            alpha,
            "quality",
            1,
            approved=True,
            not_before=datetime(2030, 1, 1),
        )


@pytest.mark.parametrize(
    "capacity",
    [0, 17, True, "2"],
)
def test_invalid_global_limit_rejected(
    capacity: object,
) -> None:
    with pytest.raises(ValueError):
        SchedulingPolicy(
            max_running_jobs=capacity  # type: ignore[arg-type]
        )


def test_concurrent_claims_respect_capacity(
    context: TestContext,
) -> None:
    queue, _, (alpha, beta, gamma) = context

    enqueue(queue, alpha)
    enqueue(queue, beta)
    enqueue(queue, gamma)

    def claim(worker_id: str) -> object:
        return queue.claim_next(
            worker_id,
            max_running_jobs=1,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                claim,
                ["worker-a", "worker-b", "worker-c"],
            )
        )

    assert sum(result is not None for result in results) == 1
