#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
HOOKS_PATH = Path.home() / ".codex" / "hooks.json"
START_COMMAND = f"/bin/bash {ROOT / 'scripts' / 'activate_current_session.sh'}"
END_COMMAND = f"/bin/bash {ROOT / 'scripts' / 'deactivate_session.sh'}"


def load_hooks() -> dict[str, Any]:
    if not HOOKS_PATH.exists():
        return {}
    data = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Expected a JSON object in {HOOKS_PATH}")
    return data


def remove_command(groups: object, command: str) -> list[dict[str, Any]]:
    if not isinstance(groups, list):
        return []
    kept: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        entries = group.get("hooks")
        if not isinstance(entries, list):
            kept.append(group)
            continue
        filtered = [
            entry
            for entry in entries
            if not (isinstance(entry, dict) and entry.get("command") == command)
        ]
        if filtered:
            updated = dict(group)
            updated["hooks"] = filtered
            kept.append(updated)
    return kept


def main() -> int:
    data = load_hooks()
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks

    starts = remove_command(hooks.get("SessionStart"), START_COMMAND)
    starts.append(
        {
            "matcher": "startup|resume|clear|compact",
            "hooks": [
                {
                    "type": "command",
                    "command": START_COMMAND,
                    "timeout": 30,
                    "statusMessage": "Starting session-scoped Telegram companion",
                }
            ],
        }
    )
    hooks["SessionStart"] = starts

    ends = remove_command(hooks.get("SessionEnd"), END_COMMAND)
    ends.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": END_COMMAND,
                    "timeout": 3,
                    "statusMessage": "Stopping session-scoped Telegram companion",
                }
            ]
        }
    )
    hooks["SessionEnd"] = ends

    HOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = HOOKS_PATH.with_name(f".{HOOKS_PATH.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, HOOKS_PATH)
    HOOKS_PATH.chmod(0o600)
    print(f"Configured session companion hooks in {HOOKS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
