#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="$HOME/.codex/channels/telegram"
RUNTIME="$CONFIG_DIR/current-session.json"

is_bridge_pid() {
  local candidate="${1:-}"
  local command_line=""
  [[ "$candidate" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$candidate" 2>/dev/null || return 1
  command_line="$(/bin/ps -p "$candidate" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"bridge.py run"* ]]
}

if [[ ! -f "$RUNTIME" ]]; then
  echo "Codex Telegram bridge: inactive"
  exit 0
fi

pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid",""))' "$RUNTIME" 2>/dev/null || true)"
thread_id="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("thread_id",""))' "$RUNTIME" 2>/dev/null || true)"
log_path="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("log_path",""))' "$RUNTIME" 2>/dev/null || true)"

if is_bridge_pid "$pid"; then
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
