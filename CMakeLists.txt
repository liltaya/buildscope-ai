cmake_minimum_required(VERSION 3.20)

project(
    BuildScopeAI
    VERSION 0.1.0
    LANGUAGES CXX
)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(CTest)

include(FetchContent)

FetchContent_Declare(
    googletest
    URL
    https://github.com/google/googletest/archive/refs/tags/v1.15.2.zip
)

set(
    gtest_force_shared_crt
    ON
    CACHE BOOL
    ""
    FORCE
)

FetchContent_MakeAvailable(
    googletest
)

add_subdirectory(
    cpp-worker
)
