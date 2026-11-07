#include <gtest/gtest.h>

#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <spawn.h>
#include <string>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>

extern char** environ;

namespace {

namespace fs = std::filesystem;

struct WorkerRun {
    int exit_status = -1;
    std::string protocol;
};

class WorkerTest : public ::testing::Test {
protected:
    void SetUp() override {
        std::string pattern =
            (fs::temp_directory_path() / "bscope-worker-XXXXXX").string();

        ASSERT_NE(mkdtemp(pattern.data()), nullptr);
        directory_ = pattern;
    }

    void TearDown() override {
        std::error_code ignored;
        fs::remove_all(directory_, ignored);
    }

    WorkerRun run(
        const std::vector<std::string>& command,
        int timeout_seconds = 5,
        int max_output = 4096
    ) {
        std::vector<std::string> arguments = {
            BUILDSCOPE_WORKER_PATH,
            "--cwd", directory_.string(),
            "--timeout", std::to_string(timeout_seconds),
            "--max-output", std::to_string(max_output),
            "--stdout", stdout_path().string(),
            "--stderr", stderr_path().string(),
            "--",
        };

        arguments.insert(arguments.end(), command.begin(), command.end());

        return spawn(arguments);
    }

    WorkerRun spawn(const std::vector<std::string>& arguments) {
        const fs::path protocol_path = directory_ / "protocol.txt";

        posix_spawn_file_actions_t actions;
        posix_spawn_file_actions_init(&actions);
        posix_spawn_file_actions_addopen(
            &actions,
            STDOUT_FILENO,
            protocol_path.c_str(),
            O_WRONLY | O_CREAT | O_TRUNC,
            0600
        );

        std::vector<char*> argv;

        for (const auto& value : arguments) {
            argv.push_back(const_cast<char*>(value.c_str()));
        }

        argv.push_back(nullptr);

        pid_t pid = 0;
        const int spawned = posix_spawn(
            &pid,
            argv[0],
            &actions,
            nullptr,
            argv.data(),
            environ
        );

        posix_spawn_file_actions_destroy(&actions);

        WorkerRun result;

        if (spawned != 0) {
            ADD_FAILURE() << "posix_spawn failed: " << spawned;
            return result;
        }

        int status = 0;
        waitpid(pid, &status, 0);

        result.exit_status = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
        result.protocol = read_file(protocol_path);

        return result;
    }

    fs::path stdout_path() const {
        return directory_ / "stdout.log";
    }

    fs::path stderr_path() const {
        return directory_ / "stderr.log";
    }

    static std::string read_file(const fs::path& path) {
        std::ifstream input(path, std::ios::binary);

        return std::string(
            std::istreambuf_iterator<char>(input),
            std::istreambuf_iterator<char>()
        );
    }

    fs::path directory_;
};

bool process_exists(pid_t pid) {
    return kill(pid, 0) == 0 || errno != ESRCH;
}

bool wait_until_gone(pid_t pid, std::chrono::milliseconds limit) {
    const auto deadline = std::chrono::steady_clock::now() + limit;

    while (std::chrono::steady_clock::now() < deadline) {
        if (!process_exists(pid)) {
            return true;
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }

    return !process_exists(pid);
}

}  // namespace

TEST_F(WorkerTest, SuccessReportsExitCodeAndSeparateStreams) {
    const WorkerRun result = run({
        "/bin/sh", "-c", "printf out; printf err >&2",
    });

    EXPECT_EQ(result.exit_status, 0);
    EXPECT_EQ(result.protocol, "BSCOPE1\tOK\t0\t0\t0\n");
    EXPECT_EQ(read_file(stdout_path()), "out");
    EXPECT_EQ(read_file(stderr_path()), "err");
}

TEST_F(WorkerTest, NonZeroExitIsReportedNotFailed) {
    const WorkerRun result = run({"/bin/sh", "-c", "exit 7"});

    EXPECT_EQ(result.exit_status, 0);
    EXPECT_EQ(result.protocol, "BSCOPE1\tOK\t7\t0\t0\n");
}

TEST_F(WorkerTest, ArgumentsAreNotInterpretedByAShell) {
    const WorkerRun result = run({"/bin/echo", "a;b", "$HOME", "*"});

    EXPECT_EQ(result.protocol, "BSCOPE1\tOK\t0\t0\t0\n");
    EXPECT_EQ(read_file(stdout_path()), "a;b $HOME *\n");
}

TEST_F(WorkerTest, OutputBeyondLimitIsTruncated) {
    const WorkerRun result = run(
        {"/bin/sh", "-c", "printf '%0100d' 0"},
        5,
        10
    );

    EXPECT_EQ(result.protocol, "BSCOPE1\tOK\t0\t1\t0\n");
    EXPECT_EQ(fs::file_size(stdout_path()), 10U);
}

TEST_F(WorkerTest, TimeoutStopsStageWithinGracePeriod) {
    const auto started = std::chrono::steady_clock::now();

    const WorkerRun result = run({"/bin/sleep", "30"}, 1);

    const auto elapsed = std::chrono::steady_clock::now() - started;

    EXPECT_EQ(result.protocol.rfind("BSCOPE1\tTIMEOUT\t", 0), 0U)
        << result.protocol;
    EXPECT_LT(elapsed, std::chrono::seconds(5));
}

TEST_F(WorkerTest, MissingExecutableIsLaunchFailure) {
    const WorkerRun result = run({"/nonexistent/buildscope-command"});

    EXPECT_EQ(result.exit_status, 0);
    EXPECT_EQ(result.protocol.rfind("BSCOPE1\tLAUNCH\t", 0), 0U)
        << result.protocol;
}

TEST_F(WorkerTest, InvalidArgumentsExitWithUsageError) {
    const WorkerRun result = spawn({BUILDSCOPE_WORKER_PATH, "--timeout", "0"});

    EXPECT_EQ(result.exit_status, 2);
    EXPECT_TRUE(result.protocol.empty());
}

TEST_F(WorkerTest, DetachedDescendantIsKilledAfterSuccessfulStage) {
    const fs::path pid_file = directory_ / "descendant.pid";

    // The background sleep closes its copies of the output pipes, so
    // the worker cannot detect it by waiting for end-of-file.
    const WorkerRun result = run({
        "/bin/sh",
        "-c",
        "sleep 30 </dev/null >/dev/null 2>&1 & echo $! > " + pid_file.string(),
    });

    ASSERT_EQ(result.protocol, "BSCOPE1\tOK\t0\t0\t0\n");

    const pid_t descendant = std::stoi(read_file(pid_file));

    EXPECT_TRUE(wait_until_gone(descendant, std::chrono::seconds(3)))
        << "descendant " << descendant << " survived the stage";
}

TEST_F(WorkerTest, TimeoutKillsWholeProcessGroup) {
    const fs::path pid_file = directory_ / "descendant.pid";

    const WorkerRun result = run(
        {
            "/bin/sh",
            "-c",
            "sleep 30 </dev/null >/dev/null 2>&1 & echo $! > "
                + pid_file.string()
                + "; sleep 30",
        },
        1
    );

    ASSERT_EQ(result.protocol.rfind("BSCOPE1\tTIMEOUT\t", 0), 0U)
        << result.protocol;

    const pid_t descendant = std::stoi(read_file(pid_file));

    EXPECT_TRUE(wait_until_gone(descendant, std::chrono::seconds(3)))
        << "descendant " << descendant << " survived the timeout";
}
