add_executable(
    buildscope_cpp_worker
    src/main.cpp
)

target_compile_features(
    buildscope_cpp_worker
    PRIVATE
    cxx_std_20
)

target_compile_options(
    buildscope_cpp_worker
    PRIVATE
    -Wall
    -Wextra
    -Wpedantic
    -Werror
)

add_executable(
    buildscope_cpp_worker_tests
    tests/worker_test.cpp
)

target_compile_features(
    buildscope_cpp_worker_tests
    PRIVATE
    cxx_std_20
)

# The tests drive the real binary as a subprocess, the same way the
# Python adapter does, so they need its path rather than its symbols.
target_compile_definitions(
    buildscope_cpp_worker_tests
    PRIVATE
    BUILDSCOPE_WORKER_PATH="$<TARGET_FILE:buildscope_cpp_worker>"
)

add_dependencies(
    buildscope_cpp_worker_tests
    buildscope_cpp_worker
)

target_link_libraries(
    buildscope_cpp_worker_tests
    PRIVATE
    GTest::gtest_main
)

include(GoogleTest)

gtest_discover_tests(
    buildscope_cpp_worker_tests
)
