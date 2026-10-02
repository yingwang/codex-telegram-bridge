#!/usr/bin/env python3
"""Bring the bridge back when a live Codex conversation has none.

The bridge is started by the SessionStart hook, and that hook does not always fire. On
2026-10-01 a terminal Codex opened while the app-server daemon was down; its conversation
was only created when the daemon restarted four hours later, no SessionStart ran for it,
and the bridge stayed down for a day while Telegram messages went unanswered.

Queue mode can only reach a conversation the shared app-server daemon has loaded, so the
daemon itself is the judge of what is live: it is asked over its control socket for
`thread/loaded/list`. A bridge bound to a conversation that is no longer loaded is as good
as none, because every message it accepts is queued where nothing will ever pick it up.

launchd runs this every two minutes (scripts/install_watchdog.sh). It acts only after the
same problem has been seen on two runs in a row, so it never races a SessionStart hook that
is in the middle of starting the bridge, and it only ever binds to a conversation a person
started: never an automation, a one-off `codex exec`, a subagent or a guardian review.
A bridge stopped on purpose (/stop, deactivate.sh) while its conversation is still open
stays stopped.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path.home() / ".codex" / "channels" / "telegram"
RUNTIME = CONFIG_DIR / "current-session.json"
STATE = CONFIG_DIR / "watchdog-state.json"
LOG = CONFIG_DIR / "watchdog.log"
SESSIONS = Path.home() / ".codex" / "sessions"
CONTROL_SOCKET = Path.home() / ".codex" / "app-server-control" / "app-server-control.sock"
ACTIVATE = ROOT / "scripts" / "activate_current_session.sh"

# One launchd interval is 120 s; a problem must outlast a run before anything is done.
GRACE_SECONDS = 100


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


class Daemon:
    """The few calls needed from the app-server daemon, over its WebSocket control socket."""

    def __init__(self, path: Path, timeout: float = 10.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(os.path.realpath(path))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("control socket closed during the handshake")
            head += chunk
        status, self.pending = head.split(b"\r\n\r\n", 1)
        if b" 101 " not in status.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"control socket refused the upgrade: {status[:80]!r}")
        self.next_id = 1

    def _send(self, message: dict) -> None:
        data = json.dumps(message).encode()
        mask = os.urandom(4)
        size = len(data)
        if size < 126:
            header = bytes([0x81, 0x80 | size])
        elif size < 65536:
            header = bytes([0x81, 0x80 | 126]) + struct.pack(">H", size)
        else:
            header = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", size)
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, size: int) -> bytes:
        while len(self.pending) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("control socket closed")
            self.pending += chunk
        out, self.pending = self.pending[:size], self.pending[size:]
        return out

    def _frame(self) -> tuple[int, bytes]:
        first, second = self._read(2)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack(">H", self._read(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", self._read(8))[0]
        mask = self._read(4) if second & 0x80 else None
        data = self._read(size)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return first & 0x0F, data

    def call(self, method: str, params: dict) -> dict:
        request_id = self.next_id
        self.next_id += 1
        self._send({"id": request_id, "method": method, "params": params})
        while True:
            opcode, data = self._frame()
            if opcode == 8:
                raise ConnectionError("control socket closed")
            if opcode != 1:
                continue
            message = json.loads(data)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result") or {}

    def notify(self, method: str) -> None:
        self._send({"method": method})

    def close(self) -> None:
        self.sock.close()


def loaded_threads(path: Path = CONTROL_SOCKET) -> list[str] | None:
    """Conversations the daemon has loaded, or None when the daemon cannot be asked."""
    if not path.exists():
        return None
    try:
        daemon = Daemon(path)
    except (OSError, ConnectionError):
        return None
    try:
        daemon.call("initialize", {"clientInfo": {"name": "telegram-bridge-watchdog", "version": "1"}})
        daemon.notify("initialized")
        threads: list[str] = []
        cursor = None
        while True:
            result = daemon.call("thread/loaded/list", {"cursor": cursor} if cursor else {})
            threads.extend(t for t in result.get("data", []) if isinstance(t, str))
            cursor = result.get("nextCursor")
            if not cursor:
                return threads
    except (OSError, ConnectionError, RuntimeError, ValueError):
        return None
    finally:
        daemon.close()


def session_meta(thread: str, sessions: Path = SESSIONS) -> dict | None:
    """The opening session_meta of a conversation's rollout, written before any hook runs."""
    for rollout in sessions.rglob(f"rollout-*-{thread}.jsonl"):
        try:
            with rollout.open(encoding="utf-8") as handle:
                payload = json.loads(handle.readline()).get("payload", {})
        except (OSError, ValueError):
            return None
        if isinstance(payload, dict):
            payload["_mtime"] = rollout.stat().st_mtime
            return payload
        return None
    return None


def started_by_a_person(meta: dict | None) -> bool:
    # Automations say thread_source "automation"; subagents and guardian reviews carry a
    # dict as their source; `codex exec` says source "exec". Only a conversation someone
    # opened has thread_source "user" and a plain client name as its source.
    if not meta:
        return False
    source = meta.get("source")
    return meta.get("thread_source") == "user" and isinstance(source, str) and source != "exec"


def bridge_runtime(path: Path = RUNTIME) -> tuple[int | None, str | None]:
    """The running bridge's pid and conversation; the pid is None when nothing is running."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    pid = state.get("pid")
    thread = state.get("thread_id")
    if not isinstance(pid, int) or pid <= 0:
        return None, thread
    try:
        os.kill(pid, 0)
        command = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True
        ).stdout
    except OSError:
        return None, thread
    return (pid if "bridge.py run" in command else None), thread


def decide(
    pid: int | None,
    bound_thread: str | None,
    loaded: list[str] | None,
    metas: dict[str, dict | None],
    state: dict,
    now: float,
) -> tuple[str | None, bool, dict]:
    """What to do this run: (thread to bind or None, whether to replace a bridge, new state).

    `state` remembers three things between runs: `last_bound`, the conversation the bridge
    served while healthy; `held`, a conversation whose bridge was stopped on purpose; and
    `problem_since`, the first run that saw the current problem.
    """
    if loaded is None:
        return None, False, state
    if pid is not None and bound_thread in loaded:
        return None, False, {"last_bound": bound_thread}
    # A bridge that disappears while its conversation is still open was stopped on purpose
    # (/stop from Telegram, or deactivate.sh): sessions that end take their conversation
    # out of the loaded list with them. That conversation is left without a bridge until
    # it closes; a new session gets one from its own SessionStart hook, or from here.
    held = state.get("held")
    if pid is None and state.get("last_bound") in loaded:
        held = state.get("last_bound")
    if held not in loaded:
        held = None
    kept = {"held": held} if held else {}
    live = [t for t in loaded if started_by_a_person(metas.get(t)) and t != held]
    if not live:
        return None, False, kept
    since = state.get("problem_since")
    if not isinstance(since, (int, float)):
        return None, False, dict(kept, problem_since=now)
    if now - since < GRACE_SECONDS:
        return None, False, dict(kept, problem_since=since)
    # Keep the conversation the bridge last served when it is live again; otherwise the
    # one most recently written to.
    if bound_thread in live:
        target = bound_thread
    else:
        target = max(live, key=lambda t: (metas.get(t) or {}).get("_mtime", 0))
    return target, pid is not None, kept


def activate(thread: str, replace: bool) -> None:
    env = dict(os.environ)
    env["CODEX_THREAD_ID"] = thread
    env["TELEGRAM_REPLACE_EXISTING"] = "1" if replace else "0"
    result = subprocess.run(
        ["/bin/bash", str(ACTIVATE)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=90,
    )
    output = " | ".join(line for line in (result.stdout + result.stderr).splitlines() if line.strip())
    log(f"activate thread={thread} replace={replace} rc={result.returncode}: {output}")


def main() -> int:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    pid, bound_thread = bridge_runtime()
    loaded = loaded_threads()
    metas = {thread: session_meta(thread) for thread in loaded or []}
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    target, replace, new_state = decide(pid, bound_thread, loaded, metas, state, time.time())
    if new_state != state:
        if new_state.get("problem_since") and not state.get("problem_since"):
            what = "bound to an unloaded conversation" if pid else "not running"
            log(f"bridge {what} (thread={bound_thread}); live conversations: {loaded}; waiting one more run")
        if new_state.get("held") and new_state.get("held") != state.get("held"):
            log(f"bridge stopped while thread={new_state['held']} is still open; leaving it stopped")
        STATE.write_text(json.dumps(new_state), encoding="utf-8")
    if target:
        activate(target, replace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
