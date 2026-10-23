cmake_minimum_required(VERSION 3.20)

project(
    BuildScopeAI

    VERSION 0.1.0

    LANGUAGES CXX
)


set(
    CMAKE_CXX_STANDARD
    20
)

set(
    CMAKE_CXX_STANDARD_REQUIRED
    ON
)

set(
    CMAKE_CXX_EXTENSIONS
    OFF
)

set(
    CMAKE_EXPORT_COMPILE_COMMANDS
    ON
)


include(CTest)


if(BUILD_TESTING)

    include(FetchContent)

    set(
        INSTALL_GTEST
        OFF
        CACHE BOOL
        ""
        FORCE
    )

    FetchContent_Declare(
        googletest

        URL
        https://github.com/google/googletest/archive/refs/tags/v1.15.2.zip

        DOWNLOAD_EXTRACT_TIMESTAMP
        TRUE
    )

    FetchContent_MakeAvailable(
        googletest
    )

endif()


add_subdirectory(
    cpp-worker
)