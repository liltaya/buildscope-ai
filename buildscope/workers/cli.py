"""Local CLI for approved pipelines, job status and recovery"""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import UUID

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.pipelines.graph import build_pipeline_plan
from buildscope.pipelines.service import PipelineService
from buildscope.workers.queue import WorkerQueue


def main() -> None:
    parser = argparse.ArgumentParser(description="BuildScope worker queue")

    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/buildscope.db"),
    )

    actions = parser.add_subparsers(
        dest="action",
        required=True,
    )

    for name in ("preview", "enqueue"):
        action = actions.add_parser(name)

        action.add_argument(
            "--repository",
            type=UUID,
            required=True,
        )

        action.add_argument(
            "--pipeline",
            required=True,
        )

        action.add_argument(
            "--version",
            type=int,
            required=True,
        )

        if name == "enqueue":
            action.add_argument(
                "--approve",
                action="store_true",
            )

    status = actions.add_parser("status")

    status.add_argument(
        "--job",
        type=UUID,
        required=True,
    )

    recover = actions.add_parser("recover")

    recover.add_argument(
        "--stale-seconds",
        type=int,
        default=30,
    )

    args = parser.parse_args()

    engine = create_sqlite_engine(args.database.expanduser().resolve(strict=True))

    try:
        factory = create_session_factory(engine)
        queue = WorkerQueue(factory)

        if args.action == "status":
            print(queue.get_job(args.job))
            return

        if args.action == "recover":
            recovered = queue.recover_stale(stale_after_seconds=args.stale_seconds)

            for job_id in recovered:
                print(f"Recovered terminal job: {job_id}")

            print(f"Recovered {len(recovered)} job(s). No jobs were requeued.")
            return

        saved = PipelineService(factory).get_version(
            args.repository,
            args.pipeline,
            args.version,
        )

        plan = build_pipeline_plan(saved.definition)

        print(f"Pipeline: {saved.name} version {saved.version}")

        for stage in plan.ordered_stages:
            print(f"  {stage.name}: {list(stage.command)!r}")

        if args.action == "preview":
            print("Preview only. No job was queued.")
            return

        if not args.approve:
            parser.error("Review commands first; --approve is required to enqueue.")

        job = queue.enqueue_version(
            args.repository,
            args.pipeline,
            args.version,
            approved=True,
        )

        print(f"Enqueued job: {job.id} ({job.status})")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
