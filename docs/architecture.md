# BuildScope AI Architecture

## Current architecture

BuildScope AI is currently a single local repository containing Python and C++ components.

```text
BuildScope AI
├── Python core
│   └── buildscope/
└── C++ worker foundation
    └── cpp-worker/

## HTTP API

BuildScope exposes a local FastAPI application.

Current architecture:

```text
HTTP client
    |
    v
FastAPI application
    |
    v
API router
    |
    v
health endpoint
## Database layer

BuildScope initially uses SQLite through SQLAlchemy.

Current database architecture:

```text
Application code
      |
      v
SQLAlchemy Session
      |
      v
SQLAlchemy Engine
      |
      v
SQLite

## Registered repository model

BuildScope now persists registered local repositories.

```text
Repository
├── id: UUID
├── name: string
├── local_path: unique string
├── language: optional string
├── build_system: optional string
├── test_framework: optional string
└── created_at: UTC datetime

## Repository persistence and service layers

```text
RepositoryService
       |
       v
RepositoryStore
       |
       v
SQLAlchemy Session
       |
       v
SQLite
```

The RepositoryStore owns persistence queries.

The RepositoryService owns registration rules and transaction boundaries.

The service returns immutable RepositoryDetails objects instead of exposing live ORM objects.

Duplicate repository paths are rejected by both an application lookup and a database uniqueness constraint.

HTTP route integration is not implemented yet.

## Local repository registration API

```text
FastAPI Repository Routes
          |
          v
RepositoryService
          |
          v
Filesystem Validation
          |
          v
RepositoryStore
          |
          v
SQLAlchemy
          |
          v
SQLite
```

Request schemas use Pydantic.

The service owns application validation and transaction boundaries.

The store owns persistence queries.

FastAPI translates application errors into HTTP responses.

The database engine is initialized during application startup and disposed during shutdown.

Each service operation uses its own short-lived database session.

## Project detection

The project detector is implemented in:

`buildscope/detection/detector.py`

It is independent of FastAPI and SQLAlchemy.

Architecture:

Registered Repository
        |
        v
RepositoryService
        |
        v
ProjectDetector
        |
        v
ProjectDetection
        |
        v
RepositoryStore
        |
        v
SQLite

Detection is deterministic and rule-based.

The detector analyzes configuration and source files without executing them.

The supported search depth is limited to avoid recursively scanning arbitrary
dependency and build directories.

The service persists the detected language, build system, and test framework.

Evidence and warnings are returned by the detection operation but are not
persisted in the repository table.

Git metadata detection is outside the scope of this implementation.

## Git inspection

Git inspection is implemented in:

`buildscope/git/inspector.py`

The inspector is independent of HTTP and database code.

Architecture:

    FastAPI
       |
       v
    RepositoryService
       |
       v
    GitInspector
       |
       v
    Local Git CLI

RepositoryService retrieves the registered path and closes its SQLAlchemy
session before starting Git inspection.

Git commands use fixed argument arrays, no shell, and timeouts.

The inspector supports ordinary Git branches, detached HEAD, repositories
without commits, nested working-tree directories, and non-Git directories.

The current branch and commit are not persisted because they are mutable
working-tree state. Historical Git metadata will be modeled separately.


## Pipeline configuration architecture

Pipeline configuration uses PyYAML and Pydantic.

Architecture:

    YAML configuration
           |
           v
       Safe Loader
           |
           v
     Pydantic Schema
           |
           v
    PipelineDefinition
           |
           v
     StageDefinition

The loader is responsible for reading YAML safely.

Pydantic models enforce field types, names, bounds, and uniqueness rules.

Command definitions are stored as tuples of arguments rather than shell strings.

Configurations do not execute commands during parsing.

Dependency existence checks, cycle detection, and topological sorting will be
implemented in the next stage.

Pipeline persistence and execution will be implemented in later stages.

## Pipeline dependency graph

Pipeline planning extends the YAML configuration system.

Architecture:

    YAML
      |
      v
    Safe Loader
      |
      v
    Pydantic Schema
      |
      v
    PipelineDefinition
      |
      v
    Dependency Graph Validation
      |
      v
    Topological Sorting
      |
      v
    PipelinePlan

The graph planner is implemented in:

`buildscope/pipelines/graph.py`

It performs:

- Unknown dependency detection
- Self-dependency rejection
- Cycle detection using depth-first search
- Deterministic topological sorting using Kahn's algorithm

Dependencies are represented as directed edges from prerequisites to
dependent stages.

The planner returns stage objects in a valid dependency order.

The original PipelineDefinition remains unchanged.

The planner does not start subprocesses or perform database writes.

Scheduling, parallel execution, retries, and failure propagation
are separate responsibilities implemented later.


## Pipeline persistence and versioning

Pipeline definitions are persisted through SQLAlchemy.

Architecture:

    Pipeline YAML
          |
          v
    Pydantic Schema
          |
          v
    DAG Validation
          |
          v
    PipelineService
          |
          v
    PipelineStore
          |
          v
    SQLite

PipelineDefinitionRecord stores:

- UUID identifier
- parent repository UUID
- pipeline name
- version number
- validated configuration JSON
- UTC creation timestamp

The unique constraint on repository_id, name, and version prevents duplicate
version numbers within the same repository pipeline.

The service does not modify previous versions.

Schema migrations are managed through Alembic.

The repository baseline migration recognizes previously created Part 6
databases so existing registered repositories can be preserved.

The current implementation does not automatically retry concurrent version
allocation conflicts.

Pipeline execution remains outside this component.


## Local subprocess execution

BuildScope's first execution layer is implemented in:

`buildscope/execution/executor.py`

Architecture:

    ExecutionRequest
           |
           v
    Request Validation
           |
           v
    Working Directory Validation
           |
           v
    subprocess.run()
           |
           v
    ExecutionResult

Execution requests contain:

- Command arguments
- Repository root
- Relative working directory
- Timeout

The executor returns:

- Exit code
- stdout
- stderr
- UTC start and finish timestamps
- Duration
- Success indicator

Commands are invoked directly without a shell.

The executor does not access SQLite or FastAPI.

The current executor is synchronous and captures output in memory.

This is not an operating-system sandbox. No untrusted pipeline is automatically
executed.

Process groups, cancellation, bounded output, and durable logs will be added
in the next part.


## Process lifecycle management

BuildScope uses subprocess.Popen to launch direct child processes.

Each command starts in a new POSIX session and process group.

The executor monitors both stdout and stderr using selectors.

Output is continuously drained from pipes to prevent blocking.

Each stream has a configurable byte limit.

Captured output is written to unique per-execution log files.

Cancellation uses a threading.Event.

Timeout and cancellation trigger process-group SIGTERM followed by SIGKILL
after a short grace period.

The executor retains partial output and log file paths when interrupted.

ExecutionResult describes completed commands.

ExecutionTimeoutError and ExecutionCancelledError describe interrupted runs.

This component is not a sandbox and cannot guarantee control of descendants
that escape the created process group.

Pipeline-level scheduling and state transitions remain separate concerns.


## Pipeline execution engine

PipelineRunner connects versioned pipeline configuration to local process execution.

Architecture:

    PipelineService
          |
          v
    PipelineDefinition
          |
          v
    DAG Planner
          |
          v
    PipelineRunner
          |
          +-- State Machine
          |
          +-- Local Executor
          |
          +-- PipelineRunRecord
          |
          +-- StageRunRecord
          |
          v
    SQLite Execution History

Pipeline state transitions are explicitly validated.

Each stage starts in QUEUED.

Before execution, a stage transitions to RUNNING.

Terminal states include:

- SUCCEEDED
- FAILED
- TIMED_OUT
- CANCELLED
- BLOCKED

Stages with failed prerequisites are blocked rather than executed.

Independent stages may continue executing after another stage fails.

Each state transition is persisted in a short database transaction.

Run history references the exact saved pipeline-definition version.

The executor stores bounded stdout and stderr logs outside SQLite.
Database records contain paths to those log files.

The current implementation uses synchronous sequential execution.

Automatic retries, distributed workers, scheduling, and crash recovery
will be implemented later.


## Stage attempts and retry policies

BuildScope distinguishes a stage run from its individual command attempts.

Data relationships:

    PipelineRunRecord
           |
           v
      StageRunRecord
           |
           v
     StageAttemptRecord

A stage can have multiple execution attempts.

The retry policy is read from the saved StageDefinition.

Maximum attempts equal retry_count + 1.

Each attempt follows the state machine:

    QUEUED -> RUNNING -> terminal

A stage remains RUNNING while retry attempts remain.

The final attempt determines the terminal stage status.

Attempt and final stage outcomes are committed atomically when a stage
finishes.

The runner retries nonzero exit codes and timeouts.

Cancellation and execution infrastructure failures stop retries.

Dependent stages remain ineligible until prerequisites reach SUCCEEDED.

After retries are exhausted, dependent stages are marked BLOCKED.

Independent stages may continue executing.

Current limitations include immediate retries without backoff,
synchronous execution, no distributed workers, and no automatic
crash recovery.


## Python worker processes

The local worker architecture separates job submission from execution.

Components:

- WorkerJobRecord: durable queued job
- WorkerQueue: enqueue, claim, complete and inspect
- Worker process: independently opens a database engine
- Launcher: creates workers using multiprocessing spawn
- PipelineRunner: performs the saved pipeline execution

Job claims use an atomic SQLite UPDATE with RETURNING.

Workers identify themselves using unique local worker IDs.

At most one active job per repository is allowed at a time.

Different repository jobs can execute concurrently.

Jobs link to PipelineRunRecord when execution completes.

Crash recovery, worker leases, and heartbeats are deferred to Part 19.


## Worker leasing and recovery

WorkerQueue now manages durable leases and worker heartbeats.

Each claim receives a unique token and lease expiration.

Heartbeats update the worker's last-seen timestamp and renew its
active job lease in a database transaction.

The worker heartbeat thread runs independently from PipelineRunner.

WorkerQueue.complete validates the claim token and the current lease.

Recovery checks lease expiration, heartbeat staleness and local
process liveness before declaring work abandoned.

Linked RUNNING pipeline, stage and attempt records are reconciled
to terminal states.

Queued stages become BLOCKED.

Previously completed stage and attempt records are not overwritten.

Recovered jobs are never automatically requeued.

The queue continues using atomic SQLite claiming and per-repository
serialization for active jobs.

Known limitations include PID reuse, process descendants surviving
worker death, and the narrow crash window before a newly created
pipeline run is linked to its worker job.


## Priority scheduler

The scheduler is implemented in:

`buildscope/workers/scheduler.py`

It extends WorkerQueue with:

- Priority-based ordering
- Earliest execution timestamps
- Per-repository fairness
- Global running-job capacity

The scheduler filters eligible QUEUED jobs.

Jobs are ordered by descending priority.

Within a priority tier, the repository with the oldest last-claimed
timestamp is preferred.

Queue time and UUID provide deterministic tie-breaking.

The last repository claim timestamp is derived from worker job history.

The global capacity check is part of the same atomic SQLite UPDATE
that claims the job.

A correlated subquery prevents claims when another job for the same
repository is RUNNING.

Part 19 claim tokens, heartbeat renewal, lease ownership and recovery
remain unchanged.

The scheduler does not execute commands, manage subprocess resources,
or replace PipelineRunner.

Current limitations include strict-priority starvation, no persistent
global scheduling configuration, and no CPU/memory-based resource quotas.

## C++ execution backend

CppStageExecutor implements the standard PipelineRunner stage-executor
interface.

The adapter serializes command arguments into a length-prefixed binary file.

The native C++20 process loads the arguments, starts the command with
fork/execvp, captures bounded stdout and stderr, and returns a BSCOPE1
status record.

The adapter translates native outcomes into existing ExecutionResult and
ExecutionError types.

The Python coordinator remains responsible for queue ownership, worker
heartbeats, leases, scheduling, retries and SQLite persistence.

The default Python backend remains available.

Native execution is optional and explicitly configured per Python worker.

## Git regression history

PipelineRunner captures Git checkout metadata before creating a run.

The snapshot is persisted on PipelineRunRecord.

Fields include:

- git_commit_sha
- git_branch
- git_is_dirty
- git_commit_at
- git_commit_subject

GitRegressionHistoryService joins pipeline runs to versioned
pipeline definitions.

History is filtered by repository, pipeline name, and optionally branch.

Regression comparison requires:

- A clean working tree
- The same branch
- The same pipeline definition version
- A different Git commit SHA
- A previous SUCCEEDED run followed by a FAILED run

The service does not execute Git bisect or determine causality.

Historical runs created before migration 0008 do not contain Git metadata.

The worker queue, leases, scheduling, retry engine, and C++ execution
backend are unchanged.

## Structured test results

The reporting subsystem has three parts.

1. reports.py validates and parses JUnit-style XML.
2. models.py defines TestReportRecord and TestCaseRecord.
3. service.py stores and queries test results.

Relationship:

    PipelineRunRecord
        |
        v
    StageRunRecord
        |
        v
    StageAttemptRecord
        |
        v
    TestReportRecord
        |
        v
    TestCaseRecord

Every retry attempt has a separate report path.

PipelineRunner prepares the report-aware command before executing the
stage and ingests the report when the attempt ends.

A command's exit code remains independent from XML parsing status.

TestReportService can retrieve all cases for an attempt and find
failed cases together with their stage, attempt number and Git SHA.

Historical attempts created before this milestone do not contain
structured test cases.

Flaky-test classification and statistical reliability analysis are
deferred to Part 24.


## Test reliability analysis

TestReliabilityService analyzes persisted TestCaseRecord observations
through the existing execution hierarchy:

    PipelineDefinitionRecord
          |
          v
    PipelineRunRecord
          |
          v
    StageRunRecord
          |
          v
    StageAttemptRecord
          |
          v
    TestReportRecord
          |
          v
    TestCaseRecord

Tests are identified using stage name, suite name, class name,
and test name.

Reliability analysis is scoped to a single immutable pipeline version.

The service reads terminal pipeline runs and terminal attempts.

Only reports with parse_status PARSED contribute observations.

Two main forms of instability evidence are evaluated:

1. A failed or errored test passes on a later attempt in the same stage.
2. Separate clean runs on the same branch and commit have conflicting
   results.

The second rule does not count retries within a single run as
independent runs.

The analyzer calculates descriptive observed failure rates.

Skipped tests are excluded from the rate denominator.

Mixed results from different Git commits are not automatically classified
as flaky because they may represent deterministic regressions.

The service does not update pipeline statuses, retry policies, or
historical test records.

No new migration is required for Part 24.


## Benchmark collection and analysis

The benchmark subsystem consists of:

- measure.py: repeated process execution and timing
- reports.py: report preparation and JSON validation
- models.py: benchmark persistence
- service.py: report ingestion
- analysis.py: Git-linked performance comparisons
- cli.py: read-only benchmark analysis

Data relationships:

    PipelineDefinitionRecord
        |
        v
    PipelineRunRecord
        |
        v
    StageRunRecord
        |
        v
    StageAttemptRecord
        |
        v
    BenchmarkReportRecord
        |
        v
    BenchmarkSampleRecord

PipelineRunner recognizes optional benchmark configuration on stages.

Each benchmark attempt receives a unique JSON report path.

The same Python or C++ execution backend supervises the benchmark
wrapper.

The wrapper measures individual whole-process invocations with
time.perf_counter_ns.

Completed reports are validated and persisted against the stage attempt.

The analysis service compares clean-commit observations with matching
stage, pipeline version, branch, command fingerprint and environment key.

Median runtimes and median absolute deviations are used for a
conservative slowdown heuristic.

No independent C++ queue consumer or new worker protocol is introduced.

Formal significance testing, CPU profiling, and performance visualization
are deferred.


## Failure dataset generation

The datasets package converts persisted execution history into a
versioned machine-learning dataset.

The stage-first-attempt-v1 contract produces one labeled example for
each eligible first stage execution attempt.

Source tables:

- pipeline_definitions
- pipeline_runs
- stage_runs
- stage_attempts
- test_reports
- test_cases
- benchmark_reports
- benchmark_samples

The dataset builder joins the execution hierarchy and calculates
historical features using a strict temporal cutoff.

A prior result contributes to a target only when:

    prior_run.finished_at < target_run.queued_at

Historical aggregates are keyed by repository, pipeline name,
pipeline version, stage name, and command fingerprint.

The export writes three chronological, run-grouped CSV splits.

A separate provenance file stores database and Git identifiers.

The manifest records feature names, label definitions, split counts,
class counts, export version, and limitations.

Dataset export does not modify SQLite records.

No new database migration is introduced.

Machine-learning preprocessing, model selection and evaluation are
deferred

## Failure classification model

The ML training layer consumes the CSV dataset contract.

Components:

- training.py: validation, preprocessing, training, evaluation, persistence
- cli.py: offline training entry point

The training data is split chronologically by pipeline run.

Provenance is inspected to verify grouping and chronological boundaries,
but its identifiers are not model inputs.

The model pipeline includes:

1. OneHotEncoder for categorical fields
2. SimpleImputer for missing numeric fields
3. StandardScaler for numeric fields
4. LogisticRegression with class_weight balanced

Only train.csv is used to fit preprocessing and classifier parameters.

validation.csv and test.csv are used exclusively for evaluation.

The classifier is compared against a constant-probability baseline.

The fixed decision threshold is 0.50.

Model artifacts are saved to a new versioned directory.

The output includes:

- Fitted model
- Validation and test metrics
- Training metadata
- Dataset file SHA-256 hashes
- Package versions
- Model artifact SHA-256 hash

The model does not access SQLite directly.

Online prediction and model serving belong to Part 28.

