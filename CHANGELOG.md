### Added

- FastAPI application bootstrap.
- FastAPI application factory.
- Application lifespan handling.
- Structured API startup and shutdown logging.
- Health-check endpoint.
- Pydantic health response schema.
- OpenAPI documentation.
- Automated FastAPI endpoint tests.

### Added

- SQLAlchemy database foundation.
- SQLite engine creation.
- SQLite modern transaction configuration.
- SQLite foreign-key enforcement.
- SQLAlchemy declarative base.
- SQLAlchemy Session factory.
- Database schema initialization.
- Database integration tests.
- Transaction rollback test.

### Added

- Repository SQLAlchemy ORM model.
- UUID repository identifiers.
- UTC-aware repository creation timestamps.
- Repository path normalization.
- Unique local repository path constraint.
- Repository database integrity constraints.
- Repository persistence tests.
- Repository schema tests.

### Added

- RepositoryStore persistence abstraction.
- RepositoryService application layer.
- Immutable RepositoryDetails results.
- Repository registration and retrieval.
- Repository listing.
- Application-level repository exceptions.
- Duplicate registration protection.
- Transaction-managed repository operations.
- Repository service integration tests.

### Added

- Repository registration REST API.
- Repository list endpoint.
- Repository lookup by UUID endpoint.
- Filesystem existence validation.
- Directory validation.
- HTTP error mapping for invalid, duplicate, and missing repositories.
- FastAPI database lifecycle integration.
- Repository API integration tests.

### Added

- Rule-based Python project detection.
- C++ source and CMake detection.
- pytest, CTest, and GoogleTest detection.
- Python build-backend detection.
- Mixed-language project classification.
- Detection evidence and warnings.
- Repository detection service operation.
- Persistence of detected repository metadata.
- POST /repositories/{id}/detect endpoint.
- Project detection unit and integration tests.

### Added

- Read-only GitInspector module.
- Git repository and working-tree detection.
- Git repository root resolution.
- Current branch and commit inspection.
- Detached HEAD detection.
- Support for repositories without commits.
- Graceful non-Git directory handling.
- Git subprocess timeout and error handling.
- GET /repositories/{id}/git endpoint.
- Git inspection unit and integration tests.

### Added

- Pydantic pipeline configuration schema.
- StageDefinition and PipelineDefinition models.
- Safe YAML loading with PyYAML.
- Command argument-array validation.
- Timeout and retry limits.
- Unique stage-name validation.
- Pipeline configuration error handling.
- Python and C++ sample pipeline files.
- Pipeline schema and loader tests.

### Added

- Pipeline dependency graph validation.
- Missing-dependency detection.
- Self-dependency validation.
- Dependency cycle detection with diagnostic paths.
- Deterministic topological sorting.
- PipelinePlan model.
- load_pipeline_plan() entry point.
- Pipeline graph unit and integration tests.

### Added

- PipelineDefinitionRecord SQLAlchemy model.
- PipelineStore and PipelineService.
- Pipeline version numbering.
- Persistent JSON pipeline configurations.
- Pipeline version history retrieval.
- Pipeline foreign-key and uniqueness constraints.
- Alembic migrations.
- Existing database baseline migration.
- Pipeline persistence REST endpoints.
- Pipeline persistence and API integration tests.

### Added

- ExecutionRequest and ExecutionResult models.
- Synchronous local command executor.
- Direct subprocess execution without shell interpolation.
- Repository working-directory validation.
- Working-directory traversal and symlink escape rejection.
- stdout and stderr capture.
- Exit code and execution duration reporting.
- UTC execution timestamps.
- Executable launch error handling.
- Basic subprocess timeout handling.
- 24 automated executor tests.

### Added

- POSIX process-group execution.
- Graceful SIGTERM and SIGKILL escalation.
- Running-command cancellation through threading.Event.
- Bounded stdout and stderr capture.
- Persistent per-run log files.
- Output truncation reporting.
- Partial log preservation on timeout and cancellation.
- Detection and cleanup of lingering output pipes.
- Process lifecycle and cancellation tests.

### Added

- Pipeline run state machine.
- PipelineRunRecord SQLAlchemy model.
- StageRunRecord SQLAlchemy model.
- Alembic migration for execution history.
- Sequential PipelineRunner.
- DAG-ordered stage execution.
- Persistent pipeline and stage statuses.
- Dependency-blocking behavior.
- Timeout and cancellation status integration.
- Execution log-path persistence.
- Historical pipeline-run retrieval.
- Explicit approval requirement for local execution.
- Pipeline-run integration tests.

### Added

- StageAttemptRecord database model.
- Alembic migration 0004 for execution attempts.
- Retry-aware PipelineRunner.
- Configurable retry counts per stage.
- Individual attempt status persistence.
- Retry handling for command failures and timeouts.
- Cancellation-aware retry termination.
- Dependency blocking after retries are exhausted.
- Historical attempt retrieval.
- StageAttemptDetails and attempt_count.
- Retry and failure propagation integration tests.

### Added

- Durable worker job queue.
- WorkerJobRecord SQLAlchemy model.
- Alembic migration 0005.
- Atomic SQLite job claiming.
- Worker ownership validation.
- Per-repository execution serialization.
- Local worker CLI.
- Independent Python worker processes.
- Spawn-based multiprocessing launcher.
- Job-to-pipeline-run linkage.
- Worker queue integration tests.

### Added

- WorkerHeartbeatRecord database model.
- Claim tokens and renewable job leases.
- Alembic migration 0006.
- Independent worker heartbeat thread.
- Lease ownership validation.
- Early pipeline-run linkage.
- Stale worker detection.
- Conservative abandoned-job recovery.
- Reconciliation of incomplete pipeline execution records.
- Heartbeat, lease, and crash-recovery tests.