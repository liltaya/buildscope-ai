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

## C++ development

BuildScope contains a C++20 core library and worker executable.

Configure:

```bash
cmake \
    -S . \
    -B build \
    -DBUILD_TESTING=ON \
    -DCMAKE_BUILD_TYPE=Debug
```

Build:

```bash
cmake --build build --parallel
```

Run the C++ tests:

```bash
ctest --test-dir build --output-on-failure
```

Run the current worker executable:

```bash
./build/cpp-worker/buildscope_cpp_worker
```

Expected output:

```text
BuildScope C++ worker 0.1.0
```

Check C++ formatting:

```bash
clang-format --dry-run --Werror \
    cpp-worker/include/buildscope_cpp/version.hpp \
    cpp-worker/src/version.cpp \
    cpp-worker/src/main.cpp \
    cpp-worker/tests/version_test.cpp
```