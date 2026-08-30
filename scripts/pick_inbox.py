#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_INBOX = Path.home() / ".codex" / "channels" / "telegram" / "inbox.jsonl"
DEFAULT_CURSOR = Path.home() / ".codex" / "channels" / "telegram" / "inbox.cursor"


def read_cursor(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def write_cursor(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")
    path.chmod(0o600)


def load_events(path: Path, start: int) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], start

    events: list[dict[str, Any]] = []
    last_line = start
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if line_no <= start:
                continue
            last_line = line_no
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events, last_line


def render_event(event: dict[str, Any]) -> str:
    direction = event.get("direction", "?")
    label = "Telegram -> Codex" if direction == "in" else "Codex -> Telegram"
    ts = event.get("ts", "unknown-time")
    sender = event.get("sender", "unknown")
    message_id = event.get("message_id")
    text = str(event.get("text", ""))
    header = f"[{ts}] {label} | sender={sender}"
    if message_id:
        header += f" | message_id={message_id}"
    return f"{header}\n{text}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Print unread Codex Telegram inbox events.")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX), help="Path to inbox.jsonl")
    parser.add_argument("--cursor", default=str(DEFAULT_CURSOR), help="Path to cursor file")
    parser.add_argument("--limit", type=int, default=20, help="Maximum events to print")
    parser.add_argument("--peek", action="store_true", help="Do not advance the cursor")
    args = parser.parse_args()

    inbox = Path(args.inbox).expanduser()
    cursor = Path(args.cursor).expanduser()
    start = read_cursor(cursor)
    events, last_line = load_events(inbox, start)

    if not events:
        if not args.peek:
            write_cursor(cursor, last_line)
        print("No unread Telegram inbox records.")
        return 0

    shown = events[-max(args.limit, 1):]
    print(f"Unread Telegram inbox records: {len(events)}")
    for index, event in enumerate(shown, start=1):
        print(f"\n--- {index} ---")
        print(render_event(event))

    if not args.peek:
        write_cursor(cursor, last_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
