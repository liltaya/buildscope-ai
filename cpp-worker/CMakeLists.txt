add_executable(
    buildscope_cpp_worker
    src/main.cpp
)

target_compile_features(
    buildscope_cpp_worker
    PRIVATE
    cxx_std_20
)

add_executable(
    buildscope_cpp_tests
    tests/smoke_test.cpp
)

target_compile_features(
    buildscope_cpp_tests
    PRIVATE
    cxx_std_20
)

target_link_libraries(
    buildscope_cpp_tests
    PRIVATE
    GTest::gtest_main
)

include(GoogleTest)

gtest_discover_tests(
    buildscope_cpp_tests
)
