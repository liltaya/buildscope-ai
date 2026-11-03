/*
 * BuildScope C++20 native stage launcher.
 *
 * Usage:
 *   buildscope_stage_exec -- command arg1 arg2
 *
 * Supported on macOS and Linux.
 */

#include <cerrno>
#include <cstdio>
#include <cstring>

#include <unistd.h>

namespace {

constexpr int kUsageError = 64;
constexpr int kCommandNotExecutable = 126;
constexpr int kCommandNotFound = 127;

void print_usage(const char* program) {
    std::fprintf(
        stderr,
        "Usage: %s -- command [args...]\n",
        program
    );
}

int execute_stage(int argc, char* argv[]) {
    if (argc < 3) {
        print_usage(argv[0]);
        return kUsageError;
    }

    if (std::strcmp(argv[1], "--") != 0) {
        print_usage(argv[0]);
        return kUsageError;
    }

    // Replace the launcher process with the stage command.
    // No intermediate shell is started.
    ::execvp(
        argv[2],
        argv + 2
    );

    const int error_code = errno;

    std::fprintf(
        stderr,
        "BuildScope C++ launcher: cannot execute '%s': %s\n",
        argv[2],
        std::strerror(error_code)
    );

    if (error_code == ENOENT) {
        return kCommandNotFound;
    }

    return kCommandNotExecutable;
}

}  // namespace

int main(int argc, char* argv[]) {
    return execute_stage(argc, argv);
}

