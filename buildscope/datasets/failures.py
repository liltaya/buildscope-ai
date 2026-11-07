"""Leakage-aware first-attempt failure datasets for BuildScope AI

One row represents the FIRST attempt of a stage. Features are known at
pipeline enqueue time; history is included only after an earlier run ends.

Features describe a stage's own track record: rates over its earlier first
attempts, its last few results and the state of the Git worktree. Cumulative
counts, calendar fields and stage identity are deliberately absent. Under a
chronological split they let a model learn when a run happened rather than
whether it fails, and a stage that only starts failing late in the history is
then scored from its memorised early rates instead of its recent results.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from buildscope.database.session import SessionFactory
from buildscope.pipelines.attempt_models import StageAttemptRecord
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.testing.models import TestCaseRecord, TestReportRecord

SCHEMA_VERSION = "stage-first-attempt-v2"

MAX_ROWS = 50_000
MAX_CASES = 200_000

# How many of a stage's most recent finished first attempts feed the
# recency features. Short enough to react to a new regression within a
# few runs, long enough that one flaky result does not dominate.
RECENT_WINDOW = 5

FEATURE_COLUMNS = (
    "git_worktree_state",
    "git_head_state",
    "prior_failure_rate",
    "prior_timeout_rate",
    "prior_retry_recovery_rate",
    "recent_failure_rate",
    "last_attempt_failed",
    "prior_test_failure_rate",
)

TARGET_COLUMN = "first_attempt_failed"

TRAIN_COLUMNS = (
    "example_id",
    *FEATURE_COLUMNS,
    TARGET_COLUMN,
)

PROVENANCE_COLUMNS = (
    "example_id",
    "split",
    "repository_id",
    "pipeline_run_id",
    "stage_attempt_id",
    "pipeline_name",
    "pipeline_version",
    "stage_name",
    "git_commit_sha",
    "queued_at_utc",
)

_TERMINAL_RUNS = (
    "SUCCEEDED",
    "FAILED",
    "TIMED_OUT",
    "CANCELLED",
)

_ELIGIBLE_ATTEMPTS = (
    "SUCCEEDED",
    "FAILED",
    "TIMED_OUT",
)


class FailureDatasetError(ValueError):
    """The dataset cannot be exported safely or consistently."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _command_info(
    value: object,
) -> tuple[str, str, int]:
    """Fingerprint the command without exporting raw arguments."""
    if not isinstance(value, (list, tuple)) or not value:
        raise FailureDatasetError("Stage command must be a nonempty JSON list")

    if any(not isinstance(argument, str) for argument in value):
        raise FailureDatasetError("Stage command contains non-string arguments")

    command = list(value)

    digest = hashlib.sha256(
        json.dumps(
            command,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    return (
        digest,
        Path(command[0]).name,
        len(command),
    )


@dataclass(frozen=True, slots=True)
class _StageObservation:
    attempt_id: UUID
    stage_id: UUID
    run_id: UUID
    repository_id: UUID
    queued_at: datetime
    finished_at: datetime | None
    pipeline_name: str
    pipeline_version: int
    stage_name: str
    stage_position: int
    command_sha256: str
    executable: str
    argument_count: int
    attempt_status: str
    stage_status: str
    commit_sha: str | None
    branch: str | None
    is_dirty: bool | None
    test_executions: int = 0
    test_failures: int = 0

    @property
    def history_key(
        self,
    ) -> tuple[UUID, str, int, str, str]:
        return (
            self.repository_id,
            self.pipeline_name,
            self.pipeline_version,
            self.stage_name,
            self.command_sha256,
        )

    @property
    def label(self) -> int:
        return int(
            self.attempt_status
            in {
                "FAILED",
                "TIMED_OUT",
            }
        )


@dataclass(slots=True)
class _Prior:
    """A stage's history as it stood when a run was queued, oldest first."""

    attempts: int = 0
    failures: int = 0
    timeouts: int = 0
    retry_recoveries: int = 0
    test_executions: int = 0
    test_failures: int = 0
    recent: deque[int] = field(default_factory=lambda: deque(maxlen=RECENT_WINDOW))

    def add(
        self,
        observation: _StageObservation,
    ) -> None:
        self.attempts += 1
        self.failures += observation.label

        self.timeouts += int(observation.attempt_status == "TIMED_OUT")

        self.retry_recoveries += int(
            observation.label == 1 and observation.stage_status == "SUCCEEDED"
        )

        self.test_executions += observation.test_executions
        self.test_failures += observation.test_failures

        self.recent.append(observation.label)

    def features(self) -> dict[str, object]:
        """Rates and recency only; None where the stage has no history yet."""
        attempts = self.attempts or None
        recent = list(self.recent)

        return {
            "prior_failure_rate": (self.failures / attempts if attempts else None),
            "prior_timeout_rate": (self.timeouts / attempts if attempts else None),
            "prior_retry_recovery_rate": (
                self.retry_recoveries / attempts if attempts else None
            ),
            "recent_failure_rate": (sum(recent) / len(recent) if recent else None),
            "last_attempt_failed": (recent[-1] if recent else None),
            "prior_test_failure_rate": (
                self.test_failures / self.test_executions
                if self.test_executions
                else None
            ),
        }


def git_state_features(
    *,
    is_dirty: bool | None,
    commit_sha: str | None,
    branch: str | None,
) -> dict[str, str]:
    """Describe the worktree without exporting the commit or branch name."""
    if is_dirty is True:
        worktree = "dirty"
    elif is_dirty is False:
        worktree = "clean"
    else:
        worktree = "unknown"

    if commit_sha is None:
        head = "non_git_or_unknown"
    elif branch is None:
        head = "detached"
    else:
        head = "branch"

    return {
        "git_worktree_state": worktree,
        "git_head_state": head,
    }


def stage_features(
    prior: _Prior,
    *,
    is_dirty: bool | None,
    commit_sha: str | None,
    branch: str | None,
) -> dict[str, object]:
    """Assemble one feature row, shared by the export and live prediction."""
    values: dict[str, object] = {
        **git_state_features(
            is_dirty=is_dirty,
            commit_sha=commit_sha,
            branch=branch,
        ),
        **prior.features(),
    }

    return {name: values[name] for name in FEATURE_COLUMNS}


@dataclass(frozen=True, slots=True)
class FailureExample:
    example_id: str
    run_id: UUID
    repository_id: UUID
    attempt_id: UUID
    pipeline_name: str
    pipeline_version: int
    stage_name: str
    commit_sha: str | None
    queued_at: datetime
    features: dict[str, object]
    label: int
    # Size of the history behind the features; provenance, not a feature.
    prior_attempts: int


@dataclass(frozen=True, slots=True)
class DatasetExportResult:
    destination: Path
    examples: int
    distinct_runs: int
    split_counts: dict[str, int]
    warnings: tuple[str, ...]


def _load_observations(
    factory: SessionFactory,
    *,
    max_rows: int,
) -> list[_StageObservation]:
    """Read first attempts and prior-result source measurements."""
    with factory() as session:
        rows = session.execute(
            select(
                StageAttemptRecord,
                StageRunRecord,
                PipelineRunRecord,
                PipelineDefinitionRecord,
            )
            .join(
                StageRunRecord,
                StageAttemptRecord.stage_run_id == StageRunRecord.id,
            )
            .join(
                PipelineRunRecord,
                StageRunRecord.pipeline_run_id == PipelineRunRecord.id,
            )
            .join(
                PipelineDefinitionRecord,
                PipelineRunRecord.pipeline_definition_id == PipelineDefinitionRecord.id,
            )
            .where(
                StageAttemptRecord.attempt_number == 1,
                StageAttemptRecord.status.in_(_ELIGIBLE_ATTEMPTS),
                PipelineRunRecord.status.in_(_TERMINAL_RUNS),
            )
            .order_by(
                PipelineRunRecord.queued_at,
                PipelineRunRecord.id,
                StageRunRecord.position,
                StageAttemptRecord.id,
            )
            .limit(max_rows + 1)
        ).all()

        if len(rows) > max_rows:
            raise FailureDatasetError(
                f"More than {max_rows} eligible first attempts; "
                "lower scope or explicitly increase max_rows"
            )

        stage_ids = [stage.id for _, stage, _, _ in rows]

        case_stats: dict[UUID, list[int]] = defaultdict(lambda: [0, 0])

        if stage_ids:
            seen_cases = 0

            for offset in range(
                0,
                len(stage_ids),
                400,
            ):
                stage_batch = stage_ids[offset : offset + 400]

                case_rows = session.execute(
                    select(
                        StageAttemptRecord.stage_run_id,
                        TestCaseRecord.outcome,
                    )
                    .join(
                        TestReportRecord,
                        TestCaseRecord.test_report_id == TestReportRecord.id,
                    )
                    .join(
                        StageAttemptRecord,
                        TestReportRecord.stage_attempt_id == StageAttemptRecord.id,
                    )
                    .where(
                        StageAttemptRecord.stage_run_id.in_(stage_batch),
                        StageAttemptRecord.attempt_number == 1,
                        TestReportRecord.parse_status == "PARSED",
                    )
                    .limit(MAX_CASES + 1 - seen_cases)
                ).all()

                seen_cases += len(case_rows)

                if seen_cases > MAX_CASES:
                    raise FailureDatasetError(
                        "Too many test cases for a bounded export"
                    )

                for stage_id, outcome in case_rows:
                    if outcome in {
                        "PASSED",
                        "FAILED",
                        "ERROR",
                    }:
                        case_stats[stage_id][0] += 1

                        case_stats[stage_id][1] += int(
                            outcome
                            in {
                                "FAILED",
                                "ERROR",
                            }
                        )

        result: list[_StageObservation] = []

        for attempt, stage, run, definition in rows:
            fingerprint, executable, argc = _command_info(stage.command)

            test_total, test_failed = case_stats[stage.id]

            result.append(
                _StageObservation(
                    attempt_id=attempt.id,
                    stage_id=stage.id,
                    run_id=run.id,
                    repository_id=run.repository_id,
                    queued_at=_utc(run.queued_at),
                    finished_at=(_utc(run.finished_at) if run.finished_at else None),
                    pipeline_name=definition.name,
                    pipeline_version=definition.version,
                    stage_name=stage.stage_name,
                    stage_position=stage.position,
                    command_sha256=fingerprint,
                    executable=executable,
                    argument_count=argc,
                    attempt_status=attempt.status,
                    stage_status=stage.status,
                    commit_sha=run.git_commit_sha,
                    branch=run.git_branch,
                    is_dirty=run.git_is_dirty,
                    test_executions=test_total,
                    test_failures=test_failed,
                )
            )

        return result


def build_failure_examples(
    factory: SessionFactory,
    *,
    max_rows: int = MAX_ROWS,
) -> tuple[FailureExample, ...]:
    """Build examples using only history available before enqueue."""
    if type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS:
        raise ValueError(f"max_rows must be 1-{MAX_ROWS}")

    observations = _load_observations(
        factory,
        max_rows=max_rows,
    )

    sorted_by_queue = sorted(
        observations,
        key=lambda item: (
            item.queued_at,
            str(item.run_id),
            item.stage_position,
        ),
    )

    completed = sorted(
        (item for item in observations if item.finished_at is not None),
        key=lambda item: (
            item.finished_at or datetime.max.replace(tzinfo=UTC),
            str(item.run_id),
            item.stage_position,
        ),
    )

    history: dict[
        tuple[UUID, str, int, str, str],
        _Prior,
    ] = defaultdict(_Prior)

    cursor = 0
    output: list[FailureExample] = []

    for item in sorted_by_queue:
        while cursor < len(completed):
            historical = completed[cursor]
            finished_at = historical.finished_at

            if finished_at is None or finished_at >= item.queued_at:
                break

            if finished_at >= historical.queued_at:
                history[historical.history_key].add(historical)

            cursor += 1

        prior = history[item.history_key]

        output.append(
            FailureExample(
                example_id=hashlib.sha256(str(item.attempt_id).encode()).hexdigest(),
                run_id=item.run_id,
                repository_id=item.repository_id,
                attempt_id=item.attempt_id,
                pipeline_name=item.pipeline_name,
                pipeline_version=item.pipeline_version,
                stage_name=item.stage_name,
                commit_sha=item.commit_sha,
                queued_at=item.queued_at,
                features=stage_features(
                    prior,
                    is_dirty=item.is_dirty,
                    commit_sha=item.commit_sha,
                    branch=item.branch,
                ),
                label=item.label,
                prior_attempts=prior.attempts,
            )
        )

    return tuple(output)


def _split_by_run(
    examples: tuple[FailureExample, ...],
) -> tuple[dict[UUID, str], tuple[str, ...]]:
    """Assign complete pipeline runs to chronological partitions."""
    by_run: dict[UUID, datetime] = {}

    for item in examples:
        by_run[item.run_id] = item.queued_at

    run_order = sorted(
        by_run,
        key=lambda run: (
            by_run[run],
            str(run),
        ),
    )

    n = len(run_order)

    if n < 3:
        warning = (
            "Fewer than three distinct runs: "
            "validation and test are empty; "
            "do not report model accuracy."
        )

        return (
            {run: "train" for run in run_order},
            (warning,),
        )

    train_end = max(
        1,
        int(n * 0.70),
    )

    validation_count = max(
        1,
        int(n * 0.15),
    )

    if train_end + validation_count >= n:
        train_end = n - validation_count - 1

    validation_end = train_end + validation_count

    assigned: dict[UUID, str] = {}

    for index, run in enumerate(run_order):
        assigned[run] = (
            "train"
            if index < train_end
            else ("validation" if index < validation_end else "test")
        )

    return assigned, ()


def export_failure_dataset(
    factory: SessionFactory,
    destination: Path,
    *,
    max_rows: int = MAX_ROWS,
) -> DatasetExportResult:
    """Write CSV splits, provenance, and a manifest into a new directory."""
    examples = build_failure_examples(
        factory,
        max_rows=max_rows,
    )

    if not examples:
        raise FailureDatasetError("No terminal first-attempt observations found")

    destination = destination.expanduser().resolve()

    if destination.exists():
        raise FileExistsError(f"Dataset destination already exists: {destination}")

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    by_run, warnings = _split_by_run(examples)

    counts = {
        split: 0
        for split in (
            "train",
            "validation",
            "test",
        )
    }

    positives = {split: 0 for split in counts}

    staging = Path(
        tempfile.mkdtemp(
            prefix=".failure-export-",
            dir=destination.parent,
        )
    )

    os.chmod(staging, 0o700)

    try:
        for split in counts:
            path = staging / f"{split}.csv"

            with path.open(
                "w",
                newline="",
                encoding="utf-8",
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=TRAIN_COLUMNS,
                )

                writer.writeheader()

                for item in examples:
                    if by_run[item.run_id] != split:
                        continue

                    writer.writerow(
                        {
                            "example_id": item.example_id,
                            **item.features,
                            TARGET_COLUMN: item.label,
                        }
                    )

                    counts[split] += 1
                    positives[split] += item.label

            os.chmod(path, 0o600)

        provenance = staging / "provenance.csv"

        with provenance.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=PROVENANCE_COLUMNS,
            )

            writer.writeheader()

            for item in examples:
                writer.writerow(
                    {
                        "example_id": item.example_id,
                        "split": by_run[item.run_id],
                        "repository_id": str(item.repository_id),
                        "pipeline_run_id": str(item.run_id),
                        "stage_attempt_id": str(item.attempt_id),
                        "pipeline_name": item.pipeline_name,
                        "pipeline_version": item.pipeline_version,
                        "stage_name": item.stage_name,
                        "git_commit_sha": (item.commit_sha or ""),
                        "queued_at_utc": (item.queued_at.isoformat()),
                    }
                )

        os.chmod(provenance, 0o600)

        extra_warnings = list(warnings)

        if positives["train"] == 0 or positives["train"] == counts["train"]:
            extra_warnings.append(
                "Training split has only one label class; "
                "classification training must wait for "
                "both positive and negative examples."
            )

        for split in ("validation", "test"):
            if counts[split] and (
                positives[split] == 0 or positives[split] == counts[split]
            ):
                extra_warnings.append(
                    f"{split} split has one label class; "
                    "metrics such as ROC AUC "
                    "are undefined on that split."
                )

        warnings = tuple(extra_warnings)

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": (datetime.now(UTC).isoformat()),
            "prediction_point": ("before_pipeline_execution_at_queued_at"),
            "target_column": TARGET_COLUMN,
            "positive_label": ("first attempt FAILED or TIMED_OUT"),
            "negative_label": ("first attempt SUCCEEDED"),
            "excluded": ("cancelled and blocked first attempts; later retries"),
            "feature_columns": list(FEATURE_COLUMNS),
            "non_feature_columns": ["example_id"],
            "recent_window_runs": RECENT_WINDOW,
            "provenance_file": "provenance.csv",
            "split_method": ("chronological_by_pipeline_queued_at_grouped_by_run"),
            "split_target_fraction": {
                "train": 0.70,
                "validation": 0.15,
                "test": 0.15,
            },
            "row_counts": counts,
            "positive_counts": positives,
            "distinct_runs": len(by_run),
            "warnings": list(warnings),
            "notes": [
                "Do not use provenance or example_id as model features.",
                "Past-run features include only runs finished strictly before queued_at.",
                "Historical test statistics use first-attempt PARSED reports only.",
                (
                    "recent_failure_rate and last_attempt_failed cover the stage's "
                    f"last {RECENT_WINDOW} finished first attempts."
                ),
                (
                    "Cumulative counts, calendar fields and stage identity are not "
                    "exported: under a chronological split they track time, not "
                    "failure."
                ),
                "An individual run never appears in multiple splits.",
                "Time-overlapping runs may require an embargo for rigorous evaluation.",
            ],
        }

        manifest_file = staging / "manifest.json"

        manifest_file.write_text(
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

        os.chmod(manifest_file, 0o600)

        os.replace(staging, destination)

    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return DatasetExportResult(
        destination=destination,
        examples=len(examples),
        distinct_runs=len(by_run),
        split_counts=counts,
        warnings=warnings,
    )
