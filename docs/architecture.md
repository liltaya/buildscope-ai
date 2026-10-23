# BuildScope AI Architecture

## Current architecture

BuildScope AI is currently a single local repository containing Python and C++ components.

```text
BuildScope AI
├── Python core
│   └── buildscope/
└── C++ worker foundation
    └── cpp-worker/

## C++ component architecture

The C++ component is separated into a reusable core library and a thin executable.

```text
buildscope_cpp_worker
        |
        v
buildscope_cpp_core
        |
        +-- version