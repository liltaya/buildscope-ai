# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-07

First release: a local pipeline runner with history analysis and a dashboard.

### Added

- Repository registration with project detection (language, build system, test
  framework) and live Git inspection, including detached HEAD and repositories
  without commits.
- Pipeline definitions in YAML with schema validation, DAG validation (unknown
  dependencies, self-dependencies, cycles) and deterministic stage order.
- Immutable, versioned pipeline storage with Alembic migrations shipped inside the
  package.
- Stage execution in isolated process groups with timeouts, cancellation,
  per-attempt retries, bounded logs and cleanup of every descendant process.
- An optional C++20 stage worker with the same contract, built with CMake and
  tested with GoogleTest.
- A durable SQLite job queue with approval, priorities, delayed start, a global
  concurrency cap, one running job per repository and round-robin fairness.
- Worker heartbeats, fenced leases and recovery that reconciles jobs, runs,
  stages and attempts after a worker dies, without replaying work.
- Git-linked run history and regression windows (last good commit to first bad
  commit).
- Parsing and storage of pytest, CTest and GoogleTest XML results.
- Test reliability classification: retry flaky, same-commit variability, mixed
  across commits, consistent failure, stable pass, insufficient data.
- Benchmark timing with median and MAD per revision and slowdown detection
  against both a relative threshold and a noise floor.
- Leakage-safe failure dataset export with chronological, run-grouped splits and
  track-record features (failure, timeout and retry-recovery rates, the last
  five results, the test failure rate and the Git state), with no counts,
  calendar fields or stage identity.
- A logistic-regression failure model with holdout metrics against a
  constant-prior baseline.
- Read-only stage failure scores from a trusted local model.
- A local dashboard with a stage-by-run attempt matrix, pipeline graph, regression
  timeline, unstable-test table, benchmark chart and risk scores.
- `buildscope serve`, `buildscope db upgrade` and
  `python -m buildscope.demo seed` for a reproducible demo history.
- `python -m buildscope.release` with `preflight`, `db-check`, `backup` and
  `restore-test`.
- CI on Ubuntu and macOS: Ruff, mypy, CTest, pytest, wheel install test,
  dependency audit and a full-history secret scan; a tag-triggered release
  workflow with checksums.

### Security

- Every API route rejects non-loopback clients, foreign `Host` headers and
  cross-origin requests.
- Model files are refused if they are symlinks, owned by another user or
  writable by others, and are verified against their checksum before loading.
- Test-report XML is parsed with DTDs and entities disabled.

### Known limitations

- Stages are not sandboxed; approve only pipelines you trust.
- If a worker process is killed, its running stage process is not stopped by
  recovery.
- Failure scores are not calibrated, and some features grow with history length,
  so models trained on short histories can rank poorly. The dashboard shows each
  model's holdout ROC AUC.
- Scheduling has no aging; low-priority jobs can wait indefinitely behind a steady
  stream of higher-priority work.
- macOS and Linux only.

[0.1.0]: https://github.com/liltaya/buildscope-ai/releases/tag/v0.1.0
