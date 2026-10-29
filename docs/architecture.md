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
