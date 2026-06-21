#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="$HOME/.codex/channels/telegram"
RUNTIME="$CONFIG_DIR/current-session.json"

if [[ ! -f "$RUNTIME" ]]; then
  echo "Codex Telegram bridge: inactive"
  exit 0
fi

pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid",""))' "$RUNTIME" 2>/dev/null || true)"
thread_id="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("thread_id",""))' "$RUNTIME" 2>/dev/null || true)"
log_path="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("log_path",""))' "$RUNTIME" 2>/dev/null || true)"

if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  echo "Codex Telegram bridge: active"
  echo "pid: $pid"
  echo "thread: $thread_id"
  echo "log: $log_path"
else
  echo "Codex Telegram bridge: stale runtime"
  echo "pid: $pid"
  echo "thread: $thread_id"
  echo "runtime: $RUNTIME"
fi

