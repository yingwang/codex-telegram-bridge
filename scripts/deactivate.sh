#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_DIR="$HOME/.codex/channels/telegram"
RUNTIME="$CONFIG_DIR/current-session.json"
LIFECYCLE_LOCK="$CONFIG_DIR/session-lifecycle.lock"

if [[ "${CODEX_COMPANION_LIFECYCLE_LOCKED:-0}" != "1" ]]; then
  export CODEX_COMPANION_LIFECYCLE_LOCKED=1
  exec /usr/bin/python3 "$ROOT/scripts/with_lock.py" "$LIFECYCLE_LOCK" /bin/bash "$0" "$@"
fi

EXPECTED_SESSION=""
if [[ "${1:-}" == "--session-id" ]]; then
  EXPECTED_SESSION="${2:-}"
fi

is_bridge_pid() {
  local candidate="${1:-}"
  local command_line=""
  [[ "$candidate" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$candidate" 2>/dev/null || return 1
  command_line="$(/bin/ps -p "$candidate" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"bridge.py run"* ]]
}

if [[ ! -f "$RUNTIME" ]]; then
  [[ -n "$EXPECTED_SESSION" ]] || echo "No Codex Telegram companion runtime file found."
  exit 0
fi

pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid", ""))' "$RUNTIME" 2>/dev/null || true)"
pgid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pgid", ""))' "$RUNTIME" 2>/dev/null || true)"
thread_id="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("thread_id", ""))' "$RUNTIME" 2>/dev/null || true)"

if [[ -n "$EXPECTED_SESSION" && "$thread_id" != "$EXPECTED_SESSION" ]]; then
  exit 0
fi

if is_bridge_pid "$pid"; then
  actual_pgid="$(/bin/ps -p "$pid" -o pgid= 2>/dev/null | /usr/bin/tr -d ' ' || true)"
  if [[ "$pgid" =~ ^[0-9]+$ && "$actual_pgid" == "$pgid" ]]; then
    kill -TERM -- "-$pgid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi
  [[ -n "$EXPECTED_SESSION" ]] || echo "Stopped Codex Telegram companion: pid=$pid thread=$thread_id"
else
  [[ -n "$EXPECTED_SESSION" ]] || echo "Codex Telegram companion process is not running: pid=$pid thread=$thread_id"
fi

/bin/rm -f "$RUNTIME"
