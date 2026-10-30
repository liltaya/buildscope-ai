"""Local-only command line interface to preview, enqueue and inspect jobs."""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import UUID

from buildscope.database import create_session_factory, create_sqlite_engine
from buildscope.pipelines.graph import build_pipeline_plan
from buildscope.pipelines.service import PipelineService
from buildscope.workers.queue import WorkerQueue


def main() -> None:
    parser = argparse.ArgumentParser(description="Local BuildScope worker queue")
    parser.add_argument("--database", type=Path, default=Path("data/buildscope.db"))
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("preview", "enqueue"):
        action = commands.add_parser(name)
        action.add_argument("--repository", type=UUID, required=True)
        action.add_argument("--pipeline", required=True)
        action.add_argument("--version", type=int, required=True)
        if name == "enqueue":
            action.add_argument("--approve", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("--job", type=UUID, required=True)
    args = parser.parse_args()

    engine = create_sqlite_engine(args.database.expanduser().resolve(strict=True))
    try:
        factory = create_session_factory(engine)
        if args.action == "status":
            print(WorkerQueue(factory).get_job(args.job))
            return
        saved = PipelineService(factory).get_version(args.repository, args.pipeline, args.version)
        plan = build_pipeline_plan(saved.definition)
        print(f"Pipeline: {args.pipeline} version {args.version}")
        for stage in plan.ordered_stages:
            print(f"  {stage.name}: {list(stage.command)!r}")
        if args.action == "preview":
            print("Preview only. No job was queued.")
            return
        if not args.approve:
            parser.error(
                "Execution not approved. Review the commands, then pass --approve to enqueue."
            )
        job = WorkerQueue(factory).enqueue_version(
            args.repository, args.pipeline, args.version, approved=True
        )
        print(f"Enqueued job: {job.id} ({job.status})")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
