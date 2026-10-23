add_library(
    buildscope_cpp_core
    STATIC

    src/version.cpp
)

target_include_directories(
    buildscope_cpp_core

    PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(
    buildscope_cpp_core

    PUBLIC
    cxx_std_20
)


add_executable(
    buildscope_cpp_worker

    src/main.cpp
)

target_link_libraries(
    buildscope_cpp_worker

    PRIVATE
    buildscope_cpp_core
)

target_compile_features(
    buildscope_cpp_worker

    PRIVATE
    cxx_std_20
)


function(buildscope_enable_warnings target_name)

    if(MSVC)

        target_compile_options(
            ${target_name}
            PRIVATE
            /W4
        )

    else()

        target_compile_options(
            ${target_name}
            PRIVATE
            -Wall
            -Wextra
            -Wpedantic
        )

    endif()

endfunction()


buildscope_enable_warnings(
    buildscope_cpp_core
)

buildscope_enable_warnings(
    buildscope_cpp_worker
)


if(BUILD_TESTING)

    add_executable(
        buildscope_cpp_tests

        tests/version_test.cpp
    )

    target_link_libraries(
        buildscope_cpp_tests

        PRIVATE
        buildscope_cpp_core
        GTest::gtest_main
    )

    target_compile_features(
        buildscope_cpp_tests

        PRIVATE
        cxx_std_20
    )

    buildscope_enable_warnings(
        buildscope_cpp_tests
    )

    include(GoogleTest)

    gtest_discover_tests(
        buildscope_cpp_tests
    )

endif()