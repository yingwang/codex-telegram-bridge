#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="$HOME/.codex/channels/telegram"
RUNTIME="$CONFIG_DIR/current-session.json"
REGISTRY="$CONFIG_DIR/live-sessions.json"

is_bridge_pid() {
  local candidate="${1:-}"
  local command_line=""
  [[ "$candidate" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$candidate" 2>/dev/null || return 1
  command_line="$(/bin/ps -p "$candidate" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"bridge.py run"* ]]
}

if [[ ! -f "$RUNTIME" ]]; then
  echo "Codex Telegram companion: inactive"
  exit 0
fi

pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid",""))' "$RUNTIME" 2>/dev/null || true)"
thread_id="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("thread_id",""))' "$RUNTIME" 2>/dev/null || true)"
log_path="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("log_path",""))' "$RUNTIME" 2>/dev/null || true)"
owner_pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("owner_pid",""))' "$RUNTIME" 2>/dev/null || true)"
session_scoped="$(/usr/bin/python3 -c 'import json,sys; print(str(bool(json.load(open(sys.argv[1])).get("session_scoped",False))).lower())' "$RUNTIME" 2>/dev/null || true)"
multi_session="$(/usr/bin/python3 -c 'import json,sys; print(str(bool(json.load(open(sys.argv[1])).get("multi_session",False))).lower())' "$RUNTIME" 2>/dev/null || true)"
live_count="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("live_session_count",""))' "$RUNTIME" 2>/dev/null || true)"

if is_bridge_pid "$pid"; then
  echo "Codex Telegram companion: active"
  echo "pid: $pid"
  echo "leader_thread: $thread_id"
  echo "live_sessions: $live_count"
  echo "multi_session: $multi_session"
  echo "session_scoped: $session_scoped"
  echo "owner_pid: $owner_pid"
  echo "log: $log_path"
else
  echo "Codex Telegram companion: stale runtime"
  echo "pid: $pid"
  echo "thread: $thread_id"
  echo "runtime: $RUNTIME"
fi
