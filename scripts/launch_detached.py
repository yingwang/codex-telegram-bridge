#!/usr/bin/env python3
from __future__ import annotations

import os
import sys


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: launch_detached.py <cwd> <log_path> <command> [args...]", file=sys.stderr)
        return 2

    cwd = sys.argv[1]
    log_path = sys.argv[2]
    command = sys.argv[3:]

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid:
        os.close(write_fd)
        data = b""
        while True:
            chunk = os.read(read_fd, 64)
            if not chunk:
                break
            data += chunk
        os.close(read_fd)
        _, status = os.waitpid(pid, 0)
        if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0 and data:
            print(data.decode("utf-8").strip())
            return 0
        return 1

    os.close(read_fd)
    os.setsid()

    grandchild_pid = os.fork()
    if grandchild_pid:
        os.write(write_fd, f"{grandchild_pid}\n".encode("utf-8"))
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    os.chdir(cwd)

    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    null_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null_fd, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(null_fd)
    os.close(log_fd)

    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
