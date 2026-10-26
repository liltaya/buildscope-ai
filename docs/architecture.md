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