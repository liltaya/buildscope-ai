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