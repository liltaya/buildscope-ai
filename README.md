# BuildScope AI

BuildScope AI is a local software delivery and reliability intelligence platform.

It analyzes local Git repositories, executes software pipelines, stores execution history,
detects failures and regressions, and later supports machine-learning-assisted analysis.

## Current status

Early development.

The current repository contains only the Python and C++ development foundations.

## Requirements

- macOS
- Python 3.12+
- C++20 compiler
- CMake
- Git

## Python development

Create and activate a virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
## Python quality checks

BuildScope uses several automated quality checks.

Run the Python tests:

```bash
pytest

## Local API

BuildScope currently provides a minimal FastAPI application.

Start the API:

```bash
python -m uvicorn apps.api.main:app \
    --host 127.0.0.1 \
    --port 8000
```

For development with automatic reload:

```bash
python -m uvicorn apps.api.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "buildscope-api",
  "version": "0.1.0"
}
```

Interactive API documentation is available at:

```text
http://127.0.0.1:8000/docs
```
## Database development

BuildScope initially uses SQLite through SQLAlchemy.

The runtime database is stored locally and is not committed to Git.

Example database location:

```text
data/buildscope.db
```

Run the database tests:

```bash
pytest tests/database -v
```

The database layer currently provides:

- SQLite engine creation
- modern SQLite transaction configuration
- foreign-key enforcement
- SQLAlchemy Session creation
- schema initialization

No application domain tables exist yet.

## Repository persistence

BuildScope stores registered local software repositories in SQLite.

A repository currently contains:

- UUID identifier
- name
- normalized local path
- detected language
- detected build system
- detected test framework
- UTC creation timestamp

Detection fields are initially optional because project detection occurs after
registration.

Local repository paths are unique in the database.

Run repository persistence tests:

```bash
pytest tests/repositories -v
```

The HTTP repository-registration API has not been implemented yet.

## Repository service

BuildScope now supports repository persistence through a repository store and application service.

The service supports:

- Registering repositories
- Retrieving a repository by UUID
- Listing registered repositories
- Normalizing local paths
- Rejecting duplicate paths
- Application-level error handling

Run the tests:

```bash
pytest tests/repositories -v
```

Repository path existence and Git validation are not yet implemented.

## Local repository API

Start the API:

```bash
python -m uvicorn apps.api.main:app \
    --host 127.0.0.1 \
    --port 8000
```

Open API documentation:

```text
http://127.0.0.1:8000/docs
```

Available endpoints:

| Method | Endpoint | Description |
|---|---|---|
| POST | /repositories | Register a local directory |
| GET | /repositories | List registered repositories |
| GET | /repositories/{id} | Retrieve a repository by UUID |

Repository paths must exist and point to directories.

Duplicate paths are rejected.

Git repository verification and project-type detection will be implemented later.


## Project detection

BuildScope can identify project languages, build systems, and test frameworks.

Current detection targets:

- Python
- C++
- Mixed Python/C++ repositories
- CMake
- setuptools, Hatchling, Poetry, and Flit
- pytest
- CTest
- GoogleTest

Detection uses deterministic filesystem inspection.

It does not execute project code.

### Detect a registered repository

Send:

POST /repositories/{repository_id}/detect

The response contains:

- detected language
- detected build system
- detected test framework
- supporting file evidence
- detection warnings

Detected metadata is saved to SQLite.

Unsupported projects return null metadata and a descriptive warning.

Detection is heuristic and does not verify that a build or test suite succeeds.

## Git repository inspection

BuildScope can inspect the current Git state of a registered local repository.

Endpoint:

`GET /repositories/{repository_id}/git`

The response contains:

- Whether the directory belongs to a Git working tree
- The actual Git repository root
- Current branch
- Current commit hash
- Detached HEAD status
- Whether the registered directory is the Git root
- Warnings for unsupported or incomplete states

Git inspection uses read-only commands.

No shell interpolation or GitPython dependency is required.

Git metadata is read live and is not currently stored in SQLite.

Repositories without commits and non-Git folders are handled gracefully.

## Pipeline configuration

BuildScope supports declarative pipeline configurations in YAML.

Each pipeline contains a name and one or more stages.

Each stage can specify:

- `name`
- `command`
- `depends_on`
- `timeout_seconds`
- `retry_count`

Commands must be arrays of arguments.

Example:

```yaml
name: python-quality

stages:
  - name: tests
    command: [pytest, -q]
    timeout_seconds: 300
```

Validate and load a pipeline:

```python
from buildscope.pipelines import load_pipeline_file

pipeline = load_pipeline_file("examples/pipelines/python-quality.yaml")

print(pipeline.name)
```

Pipeline loading does not execute commands.

Dependency graph validation and actual execution are not implemented yet.

## Pipeline dependency validation

BuildScope validates pipeline stage dependencies before execution.

It supports:

- Checking referenced stage names
- Rejecting self-dependencies
- Detecting dependency cycles
- Topological sorting
- Deterministic ordering of independent stages

Example:

```python
from buildscope.pipelines import load_pipeline_plan

plan = load_pipeline_plan("examples/pipelines/cpp-quality.yaml")

print(plan.stage_names)
```

Output:

```text
('configure', 'build', 'tests')
```

The planner does not execute commands.

A valid dependency graph is required before pipelines can be persisted
or executed in later milestones.


## Versioned pipeline definitions

BuildScope supports persistent, versioned pipeline configurations.

Each pipeline version belongs to one registered repository.

The configuration is validated using Pydantic and DAG checks before storage.

Saving a pipeline creates a new immutable-by-service version.

Previous versions remain accessible.

### API

- POST /repositories/{id}/pipelines
- GET /repositories/{id}/pipelines/{name}/versions
- GET /repositories/{id}/pipelines/{name}/versions/{version}
- GET /repositories/{id}/pipelines/{name}/latest

### Database migrations

BuildScope uses Alembic for schema migrations.

Apply migrations:

```bash
python -m alembic -c alembic.ini upgrade head
```

Check revision:

```bash
python -m alembic -c alembic.ini current
```

Pipeline configurations are stored in SQLite as JSON.

Pipeline execution is not implemented yet.


## Local command execution

BuildScope provides a standalone executor for explicitly approved local commands.

Features:

- Commands represented as argument arrays
- No shell interpolation
- Repository-relative working directories
- Working-directory containment checks
- stdout and stderr capture
- Exit code reporting
- UTC start and finish timestamps
- Execution duration measurement
- Basic subprocess timeouts
- Structured execution exceptions

Example:

```python
from pathlib import Path
from buildscope.execution import ExecutionRequest, execute_command

request = ExecutionRequest(
    command=("pytest", "-q"),
    repository_root=Path.cwd(),
    timeout_seconds=300,
)

result = execute_command(request)

print(result.returncode)
print(result.stdout)
```

### Security limitations

The executor is not a sandbox.

A command can still execute arbitrary program behavior, access files outside its
working directory, spawn child processes, or produce excessive output.

Commands must be explicitly approved before execution.

Pipeline execution is not yet exposed through the HTTP API.

Process-tree cancellation, output limits, and durable log storage are planned
for later stages.


## Process lifecycle and logging

BuildScope supports local subprocess execution with:

- Explicit argument arrays
- Process-group creation on POSIX
- Cooperative cancellation through threading.Event
- Timeouts followed by process-group termination
- SIGTERM and SIGKILL escalation
- Bounded stdout and stderr capture
- Separate persistent stdout and stderr logs
- Unique log directories per execution
- Truncation indicators
- UTC execution timestamps

Default logs are stored in:

`data/execution-logs/`

Each stream retains at most 1 MiB by default.

The output limit is configurable to a maximum of 10 MiB per stream.

### Limitations

The executor is not a sandbox.

Programs can access files outside the working directory and may create
child processes outside the executor's process group.

Log files are retained until manually removed. Automatic cleanup and
retention policies are not implemented yet.

The executor currently does not expose a remote HTTP execution endpoint.


## Pipeline execution

BuildScope supports sequential execution of saved pipeline versions.

Execution uses:

- Validated pipeline definitions
- DAG-based execution ordering
- Explicit execution approval
- Local subprocess execution
- Persistent pipeline and stage statuses
- Exit codes and UTC timestamps
- Bounded stdout and stderr logs
- Timeout and cancellation reporting
- Basic dependency-failure blocking

Pipeline runs are stored in the `pipeline_runs` table.

Individual stages are stored in `stage_runs`.

A pipeline run references a specific immutable pipeline-definition version.

The runner can retrieve historical execution results by UUID.

### Limitations

The current runner executes stages sequentially.

Automatic retries, worker scheduling, crash recovery, and parallel execution
are not yet implemented.

A machine crash can leave a previously running stage marked RUNNING until
a future recovery mechanism reconciles its state.

Executing pipeline commands requires explicit local approval.

No unauthenticated HTTP execution endpoint is provided.


## Pipeline retries and execution attempts

BuildScope supports configurable retries for individual pipeline stages.

Use `retry_count` in a stage definition:

```yaml
name: quality

stages:
  - name: tests
    command: [pytest, -q]
    retry_count: 2
    timeout_seconds: 120
```

A retry count of 2 allows up to 3 total execution attempts.

Retryable outcomes:

- Nonzero command exit codes
- Command timeouts

Non-retryable outcomes:

- Successful commands
- Cancellation
- Executable launch errors
- Unexpected execution infrastructure failures

Each attempt is persisted separately in SQLite.

The `stage_attempts` table records:

- Attempt number
- Status
- Exit code
- Start and finish timestamps
- stdout and stderr paths
- Error information

StageRunDetails exposes `attempts` and `attempt_count`.

Stages remain RUNNING while retries are in progress.

Dependent stages execute only after their prerequisites have succeeded.

Retries currently have no delay or backoff.

Commands with non-idempotent side effects should not be configured for
automatic retries without careful review.


## Local Python workers

BuildScope supports persistent local pipeline jobs executed by separate
Python worker processes.

Features:

- SQLite-backed job queue
- Explicit local approval before enqueueing
- Atomic job claiming
- Worker ownership tracking
- Per-repository execution serialization
- Multiple spawned Python workers
- Reuse of PipelineRunner and its retry policies
- Persistent links to pipeline execution history

Preview an approved pipeline's commands using the local worker CLI.

Enqueue it with the explicit --approve flag.

Run queued jobs using:

    python -m buildscope.workers.worker --database data/buildscope.db --drain

Start two workers:

    python -m buildscope.workers.launcher --database data/buildscope.db --workers 2

### Limitations

Workers run commands with the local user's permissions.

Workers do not provide sandboxing or isolation from the host filesystem.

Worker heartbeats, leases, crash recovery, and sophisticated scheduling
are not yet implemented.

Only trusted local users should be allowed to enqueue work or modify
the SQLite database.

A crashed worker may leave its job in RUNNING.


## Worker heartbeats and leases

BuildScope supports renewable ownership leases for local Python workers.

Each worker registers:

- Worker identifier
- Process ID
- Startup timestamp
- Last heartbeat timestamp
- Active or stopped status

Each claimed job records:

- Worker identifier
- Unique claim token
- Lease expiration timestamp

Workers periodically extend their leases using a separate heartbeat thread.

A worker may finalize its job only while the worker ID, claim token,
job state and lease are valid.

### Recovery

The local recovery command is:

    python -m buildscope.workers.cli \
      --database data/buildscope.db recover --stale-seconds 30

Recovery requires an expired lease, stale heartbeat and a dead local
worker PID, or applicable legacy job conditions.

Abandoned jobs are not automatically replayed.

Incomplete linked pipeline runs, stages and attempts are reconciled.

Completed historical attempts retain their statuses and logs.

### Limitations

PID checks are best-effort.

A crashed worker may leave subprocess descendants running.

Verify those processes have stopped before allowing further jobs
against the recovered repository.

This is local process coordination, not distributed consensus or
an operating-system sandbox.


## Worker scheduling

BuildScope supports priority-based scheduling for approved local jobs.

Scheduling features:

- Integer priorities from 0 to 9
- Default priority of 5
- Optional not_before execution timestamp
- Global maximum number of active jobs
- One active job per repository
- FIFO ordering within equivalent scheduling conditions
- Fair rotation between repositories at equal priority
- Atomic SQLite job claiming

Priority 9 is the highest priority.

Higher-priority jobs are considered before lower-priority jobs.

Within a priority level, repositories that have waited longer
since their last claim are considered first.

### Worker pool

Run a bounded worker pool:

    python -m buildscope.workers.launcher \
      --database data/buildscope.db \
      --workers 4 \
      --max-running-jobs 2

Use --continuous to keep polling for future scheduled jobs.

All workers sharing a database must use the same global capacity policy.

### Limitations

Scheduling does not provide CPU or memory resource isolation.

The per-repository concurrency limit is one active job.

Strict priority can starve lower-priority work.

A queued job's not_before value is an eligibility deadline,
not a guaranteed execution time.

Crashed jobs remain counted as RUNNING until safely recovered.

Workers must execute only locally approved, trusted pipeline commands.

## Native C++ stage worker

BuildScope supports an optional C++20 POSIX stage-execution backend.

The native worker:

- Executes commands with fork and execvp
- Uses explicit argument arrays
- Does not invoke a shell
- Captures stdout and stderr separately
- Applies output-size limits
- Enforces execution timeouts
- Handles cancellation signals
- Terminates the stage process group
- Returns a versioned execution status protocol

Python continues to own:

- SQLite job queue
- Priority scheduling
- Lease renewal and heartbeats
- DAG ordering
- Retry policies
- Execution attempt persistence

Build:

    cmake --build build --target buildscope_cpp_worker

Run a queue worker with native execution:

    python -m buildscope.workers.worker \
      --database data/buildscope.db \
      --drain \
      --cpp-worker ./build/buildscope_cpp_worker

This backend requires macOS or Linux.

It is not an operating-system sandbox.

## Git-linked pipeline execution history

BuildScope records the Git checkout associated with each new pipeline run.

Captured information:

- Full commit SHA
- Branch name
- Working-tree dirty status
- Commit timestamp
- Commit subject

Git metadata is captured before stage execution.

Non-Git repositories remain supported.

### History

    python -m buildscope.git.history_cli \
      --database data/buildscope.db \
      --repository <UUID> \
      --pipeline quality \
      history

### Regression detection

    python -m buildscope.git.history_cli \
      --database data/buildscope.db \
      --repository <UUID> \
      --pipeline quality \
      regressions

The initial detector identifies clean success-to-failure transitions
between different commits on the same branch and pipeline version.

Dirty runs remain queryable but are excluded from regression detection.

Timeouts, cancellations, and detached-HEAD runs are not automatically
classified as branch regressions.

Detection identifies an observed failure window, not the causal commit.

Git bisect and automated culprit identification are not implemented.

## Automated test result parsing

BuildScope stores structured test results from pytest, CTest, and GoogleTest.

Supported report modes:

- pytest
- ctest
- gtest

Configure a stage with:

    test_report: pytest

For configured stages, BuildScope creates a unique XML report path for
each execution attempt and supplies the corresponding framework flag.

Results are persisted in:

- test_reports
- test_cases

Individual cases store their suite, class, name, outcome, duration and
diagnostic message.

Outcomes are:

- PASSED
- FAILED
- ERROR
- SKIPPED

Reports use these parsing statuses:

- PARSED
- MISSING
- INVALID

Missing or malformed XML is recorded rather than interpreted as success.

Each report belongs to one StageAttemptRecord.

Through the existing execution tables, cases can be linked to their
pipeline run, Git commit and retry attempt.

Limits:

- Maximum XML size: 8 MiB
- Maximum cases per report: 10000
- Maximum stored diagnostic length: 2048 characters

Structured test reporting is optional and does not change earlier saved
pipeline versions.

CTest JUnit reporting requires CMake 3.21 or newer.

## Flaky-test detection

BuildScope analyzes structured test-case history from pytest, CTest,
and GoogleTest.

The analysis is read-only and requires no additional database tables.

The reliability service evaluates one repository, named pipeline, and
pipeline-definition version at a time.

### Classifications

- RETRY_FLAKY
- SAME_COMMIT_VARIABILITY
- MIXED_UNATTRIBUTED
- CONSISTENT_FAILURE
- STABLE_PASS
- INSUFFICIENT_DATA

A retry-flaky candidate has failed and later passed on a retry within
the same stage run.

Same-commit variability requires conflicting results from separate
clean runs on the same branch, commit, and pipeline version.

Mixed results across different Git revisions are not automatically
classified as flaky.

### Metrics

Each test summary contains:

- Total observations
- Number of distinct pipeline runs
- Number of clean commit/branch groups
- Passed, failed, errored, and skipped counts
- Observed failure rate
- Retry recovery count
- Same-commit variability count
- Latest observed outcome
- Reliability classification

Failure rate excludes skipped observations.

Retry attempts count as separate observations, but they are correlated.
The failure rate is descriptive, not an independent probability estimate.

### CLI

    python -m buildscope.testing.flaky_cli \
      --database data/buildscope.db \
      --repository <UUID> \
      --pipeline quality \
      --version 1

Additional options:

- --branch
- --max-runs
- --suspected-only
- --json

### Limitations

Only successfully parsed test reports contribute observations.

Tests absent from a report are not treated as passes or failures.

A test observed to pass repeatedly is not guaranteed to be reliable.

Flakiness classification identifies instability evidence, not root cause.

The analyzer reads up to 200 completed runs by default and supports
at most 1000 selected runs and 100000 test-case observations.


## Performance benchmarks

BuildScope supports repeated whole-process benchmark measurements.

Benchmark stages are configured with:

- benchmark_iterations
- benchmark_warmups
- timeout_seconds

Example:

    benchmark_iterations: 5
    benchmark_warmups: 1

A benchmark stage executes its configured command repeatedly.

Warmup executions are not included in measurements.

Successful measured executions produce a versioned JSON report
containing process wall-clock durations.

Benchmark reports and samples are stored in SQLite using:

- benchmark_reports
- benchmark_samples

Each report belongs to one StageAttemptRecord.

Through the existing execution tables, measurements are linked to
pipeline versions, Git commits and branches.

### Slowdown detection

BenchmarkAnalysisService compares observations from clean Git revisions
of the same pipeline version.

Comparisons require matching:

- Stage name
- Branch
- Command fingerprint
- Local environment fingerprint
- Metric and unit

The service calculates the median runtime and median absolute deviation.

A slowdown is reported only when the increase passes both the configured
relative threshold and the variability threshold.

Default values:

- Minimum samples per revision: 5
- Slowdown threshold: 20%
- Noise multiplier: 3

### CLI

    python -m buildscope.benchmarks.cli \
      --database data/buildscope.db \
      --repository <UUID> \
      --pipeline performance-demo \
      --version 1

Options:

- --branch
- --max-runs
- --threshold
- --slowdowns-only
- --json

### Limitations

Measurements include subprocess startup overhead.

Repeated benchmark commands must be safe to execute multiple times.

The detector reports observed slowdowns, not proven causal regressions.

Matching environment fingerprints do not guarantee identical operating
conditions.

The current algorithm is descriptive and is not a statistical
hypothesis test.

Benchmark reports created before a crash may require manual recovery
if the runner did not finish ingesting them.


## Failure prediction datasets

BuildScope exports historical pipeline execution data for machine-learning
experiments.

Part 26 introduces the stage-first-attempt-v1 dataset contract.

Each row represents the first execution attempt of one pipeline stage.

The binary target is:

- 0: SUCCEEDED
- 1: FAILED or TIMED_OUT

Cancelled and blocked attempts are excluded.

Later retries do not become separate training examples.

### Features

Features include:

- Pipeline and stage configuration
- Command fingerprint and executable
- Git checkout state
- Scheduling context
- Historical failure and timeout rates
- Historical retry recoveries
- Historical parsed test results
- Historical benchmark measurements

Historical features are calculated only from runs completed strictly
before the target pipeline was queued.

Current attempt results are never included as model features.

### Export

    python -m buildscope.datasets.cli \
      --database data/buildscope.db \
      --output data/datasets/failure-v1

Outputs:

- train.csv
- validation.csv
- test.csv
- provenance.csv
- manifest.json

The split is chronological by pipeline queue time and grouped by run.

All stages of one run remain within one partition.

Provenance identifiers and example_id are not model features.

A new export must use a new output directory.

### Limitations

The dataset contains observed historical outcomes, not causal labels.

Splitting by pipeline runs does not eliminate every temporal or
cross-commit dependence.

Overlapping runs may require a temporal embargo.

Past benchmark measurements may originate from different host conditions.

Missing numeric rates are represented as empty CSV fields.

With fewer than three distinct runs, validation and test partitions
remain empty.

Training and model evaluation begin in Part 27.


## Machine-learning failure classification

BuildScope trains a binary first-attempt failure classifier using
the stage-first-attempt-v1 dataset from Part 26.

Labels:

- 0: First attempt succeeded
- 1: First attempt failed or timed out

The model is a scikit-learn LogisticRegression estimator with:

- One-hot categorical encoding
- Missing numeric-value imputation
- Numeric scaling
- Balanced training weights
- Fixed random seed 42
- Fixed classification threshold 0.50

The full preprocessing and classification pipeline is fitted
only on training data.

Validation and test partitions are transformed without refitting.

### Training

Install the ML dependencies:

    python -m pip install "scikit-learn>=1.6,<2.0" \
      "joblib>=1.4,<2.0"

Train:

    python -m buildscope.ml.cli \
      --dataset data/datasets/failure-v1 \
      --output data/models/failure-logreg-v1

Output files:

- model.joblib
- metrics.json
- metadata.json

The training process also evaluates a constant prior-probability
baseline using the training failure rate.

Metrics include accuracy, precision, recall, F1, ROC AUC where
defined, Brier score, and confusion-matrix counts.

### Dataset validation

Training rejects incompatible feature schemas, invalid labels,
incorrect manifest counts, missing provenance, contaminated run
splits, invalid numeric values, and insufficient data.

At least 10 training examples with both classes and nonempty
validation and test partitions are required.

### Limitations

This is an offline baseline model, not a production inference service.

Training probabilities may require calibration.

Small holdout datasets produce unstable evaluation metrics.

The fixed 0.50 threshold has not been optimized for business costs.

The categorical command fingerprint can encourage memorization of
frequent commands rather than generalizable failure patterns.

Model artifacts must not be loaded from untrusted sources.

Model inference and integration with BuildScope workers are deferred.


## Failure prediction service

BuildScope provides read-only stage failure-risk predictions using
the trusted local classifier trained.

The service:

- Loads a trusted versioned model artifact
- Validates the model and feature schema
- Checks ML dependency versions and artifact checksum
- Reads a registered repository and saved pipeline version
- Captures the current Git checkout state
- Computes historical features from earlier completed runs
- Returns one failure-risk score per pipeline stage

Predictions do not approve, enqueue, cancel or execute jobs.

The reported failure scores are not calibrated probabilities.

### CLI

    python -m buildscope.ml.predict_cli \
      --database data/buildscope.db \
      --model data/models/failure-logreg-v1 \
      --trusted-model \
      --repository <UUID> \
      --pipeline python-quality-pipeline \
      --version 1

Add --json for machine-readable output.

### Local API

The HTTP endpoint is disabled by default.

Enable it explicitly:

    export BUILDSCOPE_ENABLE_LOCAL_PREDICTIONS=1
    export BUILDSCOPE_DATABASE_PATH="$PWD/data/buildscope.db"
    export BUILDSCOPE_TRUSTED_MODEL_DIR="$PWD/data/models/failure-logreg-v1"

Start the API:

    python -m uvicorn apps.api.main:app \
      --host 127.0.0.1 --port 8000

Endpoint:

    POST /predictions/stages

The endpoint accepts a registered repository UUID, saved pipeline name,
and pipeline version.

The response includes:

- Pipeline identity
- Model schema and artifact hash
- Training timestamp
- Decision threshold
- Whether probabilities are calibrated
- Ordered stage-level failure scores
- Thresholded failure flags

### Safety

Only load model artifacts from trusted local directories.

Joblib uses pickle-based deserialization and is unsafe for untrusted files.

The endpoint is restricted to loopback requests and is not designed for
public network exposure.

The local worker queue, scheduler and approval flow remain independent
from predictions.

### Limitations

The model is an offline-trained logistic-regression baseline.

Class-weighted scores are not guaranteed to be calibrated probabilities.

Predictions use the current Git state and current historical snapshot,
not an immutable snapshot of an earlier queued job.

Historical feature extraction currently scans bounded first-attempt
history and may become expensive as the database grows.

The model is cached by the local API process. Restart the API after
replacing the trusted model artifact.

Model-driven execution decisions are not implemented.

