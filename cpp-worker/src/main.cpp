#include <algorithm>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <poll.h>
#include <string>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace {

volatile std::sig_atomic_t interrupted = 0;

extern "C" void handle_term(int) {
    interrupted = 1;
}

struct Options {
    std::string cwd;
    std::string stdout_path;
    std::string stderr_path;
    std::string args_file;
    int timeout = 300;
    std::size_t limit = 1048576;
    std::vector<std::string> command;
};

bool number(const std::string& value, long& out) {
    if (value.empty()) return false;

    char* end = nullptr;
    errno = 0;

    const long parsed = std::strtol(
        value.c_str(), &end, 10
    );

    if (errno || *end != '\0') return false;

    out = parsed;
    return true;
}

bool parse(int argc, char** argv, Options& options) {
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];

        if (arg == "--") {
            for (++i; i < argc; ++i) {
                options.command.emplace_back(argv[i]);
            }
            break;
        }

        if (i + 1 >= argc) return false;

        const std::string value = argv[++i];

        if (arg == "--cwd") {
            options.cwd = value;
        } else if (arg == "--stdout") {
            options.stdout_path = value;
        } else if (arg == "--stderr") {
            options.stderr_path = value;
        } else if (arg == "--args-file") {
            options.args_file = value;
        } else if (arg == "--timeout") {
            long n = 0;
            if (!number(value, n) || n < 1 || n > 3600) {
                return false;
            }
            options.timeout = static_cast<int>(n);
        } else if (arg == "--max-output") {
            long n = 0;
            if (
                !number(value, n)
                || n < 1
                || n > 10485760
            ) {
                return false;
            }
            options.limit = static_cast<std::size_t>(n);
        } else {
            return false;
        }
    }

    return !options.cwd.empty()
        && !options.stdout_path.empty()
        && !options.stderr_path.empty()
        && (
            (!options.command.empty()
             && options.args_file.empty())
            || (options.command.empty()
                && !options.args_file.empty())
        )
        && options.command.size() <= 64;
}

bool read_u32(
    std::ifstream& input,
    std::uint32_t& value
) {
    unsigned char bytes[4];

    if (!input.read(
        reinterpret_cast<char*>(bytes), 4
    )) {
        return false;
    }

    value = static_cast<std::uint32_t>(bytes[0])
        | (static_cast<std::uint32_t>(bytes[1]) << 8)
        | (static_cast<std::uint32_t>(bytes[2]) << 16)
        | (static_cast<std::uint32_t>(bytes[3]) << 24);

    return true;
}

bool load_arguments(Options& options) {
    if (options.args_file.empty()) return true;

    std::ifstream input(
        options.args_file,
        std::ios::binary
    );

    char magic[8];

    if (
        !input.read(magic, 8)
        || std::memcmp(magic, "BSARGS1\n", 8)
    ) {
        return false;
    }

    std::uint32_t count = 0;

    if (
        !read_u32(input, count)
        || count < 1
        || count > 64
    ) {
        return false;
    }

    for (std::uint32_t i = 0; i < count; ++i) {
        std::uint32_t length = 0;

        if (
            !read_u32(input, length)
            || length < 1
            || length > 8192
        ) {
            return false;
        }

        std::string item(length, '\0');

        if (
            !input.read(item.data(), length)
            || item.find('\0') != std::string::npos
        ) {
            return false;
        }

        options.command.push_back(
            std::move(item)
        );
    }

    return input.peek()
        == std::char_traits<char>::eof();
}

void set_nonblocking(int fd) {
    const int flags = fcntl(fd, F_GETFL);

    if (flags >= 0) {
        (void)fcntl(
            fd,
            F_SETFL,
            flags | O_NONBLOCK
        );
    }
}

void close_pair(int pair[2]) {
    close(pair[0]);
    close(pair[1]);
}

void notify_exec_error(int fd) {
    const int code = errno;

    (void)write(
        fd,
        &code,
        sizeof(code)
    );

    _exit(127);
}

struct Sink {
    int fd = -1;
    std::size_t saved = 0;
    std::size_t limit = 0;
    bool truncated = false;

    void accept(
        const char* data,
        std::size_t size
    ) {
        const auto keep = std::min(
            size,
            limit - saved
        );

        std::size_t done = 0;

        while (done < keep) {
            const auto n = write(
                fd,
                data + done,
                keep - done
            );

            if (n < 0 && errno == EINTR) {
                continue;
            }

            if (n <= 0) {
                truncated = true;
                break;
            }

            done += static_cast<std::size_t>(n);
        }

        saved += done;

        if (done < size) {
            truncated = true;
        }
    }
};

int run(const Options& options) {
    const int out_fd = open(
        options.stdout_path.c_str(),
        O_WRONLY | O_CREAT | O_EXCL,
        0600
    );

    if (out_fd < 0) {
        std::cerr << "Cannot create stdout log\n";
        return 2;
    }

    const int err_fd = open(
        options.stderr_path.c_str(),
        O_WRONLY | O_CREAT | O_EXCL,
        0600
    );

    if (err_fd < 0) {
        close(out_fd);
        std::cerr << "Cannot create stderr log\n";
        return 2;
    }

    int out_pipe[2];
    int err_pipe[2];
    int exec_pipe[2];

    if (
        pipe(out_pipe)
        || pipe(err_pipe)
        || pipe(exec_pipe)
    ) {
        std::cerr << "Cannot create pipes\n";
        close(out_fd);
        close(err_fd);
        return 2;
    }

    (void)fcntl(
        exec_pipe[1],
        F_SETFD,
        FD_CLOEXEC
    );

    const pid_t pid = fork();

    if (pid < 0) {
        std::cerr << "Cannot fork\n";

        close_pair(out_pipe);
        close_pair(err_pipe);
        close_pair(exec_pipe);
        close(out_fd);
        close(err_fd);
        return 2;
    }

    if (pid == 0) {
        close(out_pipe[0]);
        close(err_pipe[0]);
        close(exec_pipe[0]);
        close(out_fd);
        close(err_fd);

        if (setpgid(0, 0) < 0) {
            notify_exec_error(exec_pipe[1]);
        }

        if (chdir(options.cwd.c_str()) < 0) {
            notify_exec_error(exec_pipe[1]);
        }

        const int input = open(
            "/dev/null",
            O_RDONLY
        );

        if (input < 0) {
            notify_exec_error(exec_pipe[1]);
        }

        if (
            dup2(input, STDIN_FILENO) < 0
            || dup2(out_pipe[1], STDOUT_FILENO) < 0
            || dup2(err_pipe[1], STDERR_FILENO) < 0
        ) {
            notify_exec_error(exec_pipe[1]);
        }

        close(input);
        close(out_pipe[1]);
        close(err_pipe[1]);

        std::vector<char*> args;

        for (const auto& value : options.command) {
            args.push_back(
                const_cast<char*>(value.c_str())
            );
        }

        args.push_back(nullptr);

        execvp(args[0], args.data());

        notify_exec_error(exec_pipe[1]);
    }

    close(out_pipe[1]);
    close(err_pipe[1]);
    close(exec_pipe[1]);

    set_nonblocking(out_pipe[0]);
    set_nonblocking(err_pipe[0]);

    Sink out{
        out_fd,
        0,
        options.limit
    };

    Sink err{
        err_fd,
        0,
        options.limit
    };

    struct pollfd pipes[2] = {
        {
            out_pipe[0],
            POLLIN | POLLHUP,
            0
        },
        {
            err_pipe[0],
            POLLIN | POLLHUP,
            0
        },
    };

    std::signal(SIGTERM, handle_term);
    std::signal(SIGINT, handle_term);

    const auto start =
        std::chrono::steady_clock::now();

    auto term_start = start;
    auto leader_exit = start;

    bool reaped = false;
    bool terminating = false;
    bool force_sent = false;

    int wait_status = 0;
    std::string outcome = "OK";

    while (true) {
        const auto now =
            std::chrono::steady_clock::now();

        if (!reaped) {
            const pid_t waited = waitpid(
                pid,
                &wait_status,
                WNOHANG
            );

            if (waited == pid) {
                reaped = true;
                leader_exit = now;
            }
        }

        bool open_pipes =
            pipes[0].fd >= 0
            || pipes[1].fd >= 0;

        if (!terminating && interrupted) {
            outcome = "CANCELLED";
        } else if (
            !terminating
            && now - start >= std::chrono::seconds(
                options.timeout
            )
        ) {
            outcome = "TIMEOUT";
        } else if (
            !terminating
            && reaped
            && open_pipes
            && now - leader_exit
                >= std::chrono::milliseconds(500)
        ) {
            outcome = "ERROR";
        }

        if (!terminating && outcome != "OK") {
            terminating = true;
            term_start = now;

            (void)kill(-pid, SIGTERM);
        }

        if (
            terminating
            && !force_sent
            && now - term_start
                >= std::chrono::milliseconds(250)
        ) {
            (void)kill(-pid, SIGKILL);
            force_sent = true;
        }

        if (
            terminating
            && now - term_start
                >= std::chrono::seconds(2)
        ) {
            for (auto& pipefd : pipes) {
                if (pipefd.fd >= 0) {
                    close(pipefd.fd);
                    pipefd.fd = -1;
                }
            }
        }

        open_pipes =
            pipes[0].fd >= 0
            || pipes[1].fd >= 0;

        if (reaped && !open_pipes) {
            break;
        }

        (void)poll(pipes, 2, 40);

        for (int i = 0; i < 2; ++i) {
            if (
                pipes[i].fd < 0
                || !(pipes[i].revents & (
                    POLLIN | POLLHUP | POLLERR
                ))
            ) {
                continue;
            }

            char buffer[16384];

            const ssize_t n = read(
                pipes[i].fd,
                buffer,
                sizeof(buffer)
            );

            if (n > 0) {
                (i == 0 ? out : err).accept(
                    buffer,
                    static_cast<std::size_t>(n)
                );
            } else if (
                n == 0
                || (
                    n < 0
                    && errno != EINTR
                    && errno != EAGAIN
                )
            ) {
                close(pipes[i].fd);
                pipes[i].fd = -1;
            }
        }
    }

    if (!reaped) {
        (void)waitpid(pid, &wait_status, 0);
    }

    // Descendants that detached from the output pipes would otherwise
    // outlive a successful stage, so the group is always cleared.
    (void)kill(-pid, SIGKILL);

    int exec_error = 0;

    const ssize_t exec_read = read(
        exec_pipe[0],
        &exec_error,
        sizeof(exec_error)
    );

    close(exec_pipe[0]);
    close(out_fd);
    close(err_fd);

    int exit_code = -1;

    if (WIFEXITED(wait_status)) {
        exit_code = WEXITSTATUS(wait_status);
    } else if (WIFSIGNALED(wait_status)) {
        exit_code = 128 + WTERMSIG(wait_status);
    }

    if (exec_read == sizeof(exec_error)) {
        outcome = "LAUNCH";
        exit_code = exec_error;
    }

    std::cout
        << "BSCOPE1\t"
        << outcome << '\t'
        << exit_code << '\t'
        << (out.truncated ? 1 : 0) << '\t'
        << (err.truncated ? 1 : 0)
        << '\n';

    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    Options options;

    if (
        !parse(argc, argv, options)
        || !load_arguments(options)
    ) {
        std::cerr
            << "Usage: buildscope_cpp_worker "
            << "--cwd DIR --timeout SEC "
            << "--max-output BYTES "
            << "--stdout FILE --stderr FILE "
            << "(-- COMMAND [ARGS...] "
            << "| --args-file FILE)\n";

        return 2;
    }

    return run(options);
}
