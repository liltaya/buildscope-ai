cmake_minimum_required(VERSION 3.20)

project(
    BuildScopeAI
    VERSION 0.1.0
    LANGUAGES CXX
)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT UNIX)
    message(FATAL_ERROR "The BuildScope C++ worker requires a POSIX platform")
endif()

include(CTest)

include(FetchContent)

FetchContent_Declare(
    googletest
    URL
        https://github.com/google/googletest/archive/refs/tags/v1.18.0.zip
    URL_HASH
        SHA256=63b9c77751a5b8f492486005f67533fdc58682b67476fcb91650be5958d5195a
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)

set(
    INSTALL_GTEST
    OFF
    CACHE BOOL
    ""
    FORCE
)

FetchContent_MakeAvailable(googletest)

add_subdirectory(cpp-worker)
