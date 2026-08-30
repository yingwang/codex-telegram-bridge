#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: with_lock.py LOCK_PATH COMMAND [ARGS...]", file=sys.stderr)
        return 2
    lock_path = Path(sys.argv[1]).expanduser()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.parent.chmod(0o700)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.set_inheritable(fd, True)
    os.execvp(sys.argv[2], sys.argv[2:])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
