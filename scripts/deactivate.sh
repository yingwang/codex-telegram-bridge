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
  echo "No Codex Telegram bridge runtime file found."
  exit 0
fi

pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid",""))' "$RUNTIME" 2>/dev/null || true)"
thread_id="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("thread_id",""))' "$RUNTIME" 2>/dev/null || true)"

if is_bridge_pid "$pid"; then
  kill "$pid"
  sleep 1
  if is_bridge_pid "$pid"; then
    kill -TERM "$pid" 2>/dev/null || true
  fi
  echo "Stopped Codex Telegram bridge: pid=$pid thread=$thread_id"
else
  echo "Codex Telegram bridge process is not running: pid=$pid thread=$thread_id"
fi

rm -f "$RUNTIME"
