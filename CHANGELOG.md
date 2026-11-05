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

### Added

- Job priority scheduling.
- Delayed job eligibility using not_before.
- SchedulingPolicy configuration.
- Global concurrent-job capacity checks.
- Repository fairness using historical claim timestamps.
- Priority-aware atomic SQLite claiming.
- Alembic migration 0007.
- Worker pool concurrency configuration.
- Continuous polling option for scheduled jobs.
- Scheduler integration and concurrency tests.

### Added

- Native C++20 POSIX stage worker.
- Versioned worker result protocol.
- Binary command-argument serialization.
- C++ process execution with fork and execvp.
- Native timeout and process-group cancellation.
- Bounded native stdout and stderr logs.
- CppStageExecutor Python adapter.
- Pluggable PipelineRunner execution backend.
- C++ backend options for Python workers.
- C++ backend support in the multiprocessing launcher.
- Native worker integration tests.
### Added

- GitSnapshot and GitMetadataError.
- Git commit capture before pipeline execution.
- Commit SHA, branch, dirty status, date and subject persistence.
- Alembic migration 0008 for Git-linked run history.
- GitRunSummary and GitRegression models.
- GitRegressionHistoryService.
- Branch-specific execution history queries.
- Success-to-failure regression detection.
- Pipeline-version-aware comparisons.
- Read-only Git history CLI.
- Git snapshot and regression detection tests.

### Added

- Bounded JUnit-style XML parser.
- pytest structured test reporting.
- CTest JUnit result support.
- GoogleTest XML result support.
- Per-attempt unique test report files.
- TestReportRecord SQLAlchemy model.
- TestCaseRecord SQLAlchemy model.
- Alembic migration 0009.
- TestReportService.
- Missing and invalid report tracking.
- Individual test execution durations.
- Failure diagnostics.
- Git-linked failed test queries.
- Retry-aware report ingestion.
- Test parsing and persistence integration tests.

### Added

- TestReliabilityService for read-only test analytics.
- TestIdentity and TestReliabilitySummary models.
- Observed failure-rate calculations.
- Retry fail-then-pass detection.
- Same-commit variability detection.
- Pipeline-version-aware reliability grouping.
- Branch-specific test reliability analysis.
- Dirty working-tree exclusion for clean-commit evidence.
- Stable-pass and consistent-failure classifications.
- Insufficient-data classification.
- Read-only flaky-test reporting CLI.
- JSON reliability report export.
- Database-backed reliability integration tests.
- End-to-end pytest retry detection test.

### Added

- Whole-process performance benchmark wrapper.
- Configurable benchmark repetitions and warmups.
- Per-attempt benchmark JSON reports.
- BenchmarkReportRecord and BenchmarkSampleRecord.
- Alembic migration 0010.
- BenchmarkService for report ingestion.
- BenchmarkAnalysisService for Git revision comparisons.
- Median and MAD performance analysis.
- Configurable slowdown thresholds.
- Workload and environment fingerprint grouping.
- Read-only benchmark analysis CLI.
- Benchmark parser, persistence, migration and regression tests.
### Added

- Stage-first-attempt failure dataset contract.
- FailureExample and DatasetExportResult models.
- SQLAlchemy historical execution extraction.
- Leakage-aware historical feature engineering.
- Command fingerprints without raw argument export.
- Historical test failure-rate features.
- Historical benchmark measurement features.
- First-attempt failure labels.
- Chronological run-grouped dataset splitting.
- Train, validation and test CSV exports.
- Separate provenance export.
- Versioned dataset manifest.
- Class-balance and small-dataset warnings.
- Failure dataset CLI.
- Dataset integration and leakage-prevention tests.
### Added

- Offline first-attempt failure classifier.
- Scikit-learn preprocessing and classification pipeline.
- Categorical one-hot encoding.
- Missing numeric-value imputation.
- Feature scaling.
- Class-balanced logistic regression.
- Fixed-seed training.
- Part 26 dataset schema validation.
- Pipeline-run split contamination checks.
- Chronological holdout validation.
- Precision, recall, F1, ROC AUC, and Brier evaluation.
- Constant prior-probability baseline comparison.
- Versioned model artifact export.
- Source dataset and model checksums.
- Model metadata and dependency tracking.
- Model training CLI.
- Twelve ML training integration tests.

### Added

- TrustedFailureModel for local model inference.
- Model schema and checksum verification.
- ML package-version compatibility checks.
- Strict 20-feature inference validation.
- Live pre-execution feature construction.
- Historical feature reuse.
- StagePrediction and ModelInfo result objects.
- FailurePredictionService for saved pipelines.
- Read-only failure prediction CLI.
- JSON prediction output.
- Opt-in FastAPI prediction endpoint.
- Loopback-only HTTP access.
- Model provenance in prediction responses.
- Inference and API integration tests.