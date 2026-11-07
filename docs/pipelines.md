# Pipelines and workers

## Pipeline definition

A pipeline is a YAML document saved against a registered repository. Saving a
pipeline whose name already exists creates the next version; old versions are
never modified.

```yaml
name: python-quality
stages:
  - name: lint
    command: [ruff, check, .]

  - name: tests
    command: [pytest, -q]
    depends_on: [lint]
    timeout_seconds: 300
    retry_count: 1
    test_report: pytest

  - name: bench-parser
    command: [python, benchmarks/parse.py]
    depends_on: [lint]
    benchmark_iterations: 9
    benchmark_warmups: 1
```

| Field | Type | Default | Limits |
|---|---|---|---|
| `name` | string | required | letter first, then letters, digits, `_`, `-`; at most 64 characters |
| `stages` | list | required | 1 to 100 stages with unique names |
| `stages[].name` | string | required | same rule as the pipeline name |
| `stages[].command` | list of strings | required | 1 to 64 arguments, none blank or containing NUL |
| `stages[].depends_on` | list of stage names | `[]` | at most 32, no duplicates |
| `stages[].timeout_seconds` | integer | `300` | 1 to 3600, per attempt |
| `stages[].retry_count` | integer | `0` | 0 to 10 extra attempts after a failure or timeout |
| `stages[].test_report` | `pytest`, `ctest` or `gtest` | none | not combined with benchmarking |
| `stages[].benchmark_iterations` | integer | none | 5 to 30 timed runs |
| `stages[].benchmark_warmups` | integer | `1` | 0 to 5 untimed runs |

Commands are argument lists, never shell strings. Use `[sh, -c, "..."]` only if
you want a shell.

Validation rejects unknown fields, unknown dependencies, self-dependencies and
cycles (the error names the cycle). Stages run in a deterministic topological
order: dependencies first, ties broken by declaration order. A stage whose
dependency did not succeed is marked `BLOCKED` and never started.

### Test reports

With `test_report` set, BuildScope adds the report flag and parses the file after
each attempt:

| Kind | Command must be | Flag added |
|---|---|---|
| `pytest` | `pytest`, `py.test` or `python -m pytest` | `--junitxml=<path>` |
| `ctest` | `ctest` | `--output-junit <path>` |
| `gtest` | any GoogleTest binary | `--gtest_output=xml:<path>` |

Reports are parsed with `defusedxml`, with DTDs and entity declarations
refused, and capped at 8 MiB. A missing or unparseable report is stored as
`MISSING` or `INVALID` with the reason; it does not change the stage result.

### Benchmarks

With `benchmark_iterations` set, the command is wrapped by a timing harness that
runs the warmups, then times each iteration and writes a JSON report with the
command fingerprint and an environment key. Samples are only compared within the
same stage, branch, command and environment, and only from clean, successful runs.

## Saving and running

Register a repository and save a pipeline through the API:

```bash
curl -s -X POST http://127.0.0.1:8000/repositories \
  -H 'content-type: application/json' \
  -d '{"name": "my-service", "local_path": "/path/to/checkout"}'

curl -s -X POST http://127.0.0.1:8000/repositories/<id>/pipelines \
  -H 'content-type: application/json' \
  --data-binary @<(python -c 'import json,sys;print(json.dumps({"yaml": open(sys.argv[1]).read()}))' pipeline.yaml)
```

Runs go through the durable queue. Preview the commands first, then approve:

```bash
python -m buildscope.workers.cli --database data/buildscope.db \
  preview --repository <id> --pipeline python-quality --version 1

python -m buildscope.workers.cli --database data/buildscope.db \
  enqueue --repository <id> --pipeline python-quality --version 1 --approve --priority 7

python -m buildscope.workers.launcher --database data/buildscope.db --workers 2
```

`--approve` is required: BuildScope never runs a command you have not reviewed.
Without `--continuous`, the launcher drains the queue and exits.

Useful queue commands:

```bash
python -m buildscope.workers.cli --database data/buildscope.db status --job <job-id>
python -m buildscope.workers.cli --database data/buildscope.db recover
```

## C++ backend

```bash
cmake -S . -B build/dev -DCMAKE_BUILD_TYPE=Release
cmake --build build/dev
python -m buildscope.workers.launcher --database data/buildscope.db \
  --cpp-worker build/dev/cpp-worker/buildscope_cpp_worker
```

Each stage attempt is then supervised by the native worker instead of Python. The
persisted results are the same; `tests/pipelines/test_backend_parity.py` checks this.

## Analysis commands

```bash
python -m buildscope.git.history_cli --repository <id> --pipeline ci regressions
python -m buildscope.testing.flaky_cli --repository <id> --pipeline ci --version 2 --suspected-only
python -m buildscope.benchmarks.cli --repository <id> --pipeline ci --version 2 --slowdowns-only
```

## Failure model

```bash
python -m buildscope.datasets.cli --database data/buildscope.db --output data/dataset
python -m buildscope.ml.cli --dataset data/dataset --output data/model
python -m buildscope.ml.predict_cli --model data/model --trusted-model \
  --repository <id> --pipeline ci --version 2
```

`data/model` must be owned by you and not writable by anyone else; the loader
refuses it otherwise. `metrics.json` reports precision, recall, F1, ROC AUC and
Brier score for the model and a constant-prior baseline on the same holdout.

Features are rates over the stage's earlier first attempts, its last five
results and the Git worktree state. Cumulative counts, calendar fields and
stage identity are not exported. Under a chronological split they track time
rather than failure, and a model that learns them ranks below chance on its own
holdout as soon as a different stage starts failing.
