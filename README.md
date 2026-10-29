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
