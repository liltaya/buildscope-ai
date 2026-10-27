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
