"""Authorize hook-triggered shutdown only for the bridge's owning session."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys


def may_stop(payload: str, runtime: Path, sessions: Path) -> bool:
    # Preserve explicit manual invocation without a hook payload.
    if not payload.strip():
        return True
    try:
        event = json.loads(payload)
        state = json.loads(runtime.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    if not isinstance(event, dict) or not isinstance(state, dict):
        return False
    if event.get("hook_event_name") != "SessionEnd":
        return False
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return False
    if session_id != state.get("thread_id"):
        return False
    # Match the activation hook's automation exclusion, even if an automation
    # was bound by an older implementation. Never inspect another thread.
    try:
        for rollout in sessions.rglob(f"rollout-*-{session_id}.jsonl"):
            with rollout.open(encoding="utf-8") as stream:
                for _, line in zip(range(10), stream):
                    if "Automation ID:" in line:
                        return False
    except (OSError, UnicodeError):
        return False
    return True


if __name__ == "__main__":
    payload = "" if sys.stdin.isatty() else sys.stdin.read()
    allowed = may_stop(payload, Path(sys.argv[1]), Path.home() / ".codex" / "sessions")
    raise SystemExit(0 if allowed else 1)
