#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_DIR="$HOME/.codex/channels/telegram"
REGISTRY="$CONFIG_DIR/live-sessions.json"
LIFECYCLE_LOCK="$CONFIG_DIR/session-lifecycle.lock"
HOOK_LOG="$CONFIG_DIR/session-end-hook.log"

if [[ "${CODEX_COMPANION_LIFECYCLE_LOCKED:-0}" != "1" ]]; then
  export CODEX_COMPANION_LIFECYCLE_LOCKED=1
  exec /usr/bin/python3 "$ROOT/scripts/with_lock.py" "$LIFECYCLE_LOCK" /bin/bash "$0" "$@"
fi

session_id="$(/usr/bin/python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
if data.get("hook_event_name") == "SessionEnd":
    print(data.get("session_id", ""))
' 2>/dev/null || true)"
[[ -n "$session_id" ]] || exit 0

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
touch "$HOOK_LOG"
chmod 600 "$HOOK_LOG"
exec >>"$HOOK_LOG" 2>&1

process_start_token() {
  /bin/ps -p "${1:-0}" -o lstart= 2>/dev/null | /usr/bin/sed 's/^ *//;s/ *$//' || true
}

find_owner_codex_pid() {
  local candidate="$PPID" comm="" command_line="" parent="" attempts=0
  while [[ "$candidate" =~ ^[0-9]+$ ]] && (( candidate > 1 )) && (( attempts < 16 )); do
    comm="$(/bin/ps -p "$candidate" -o comm= 2>/dev/null | /usr/bin/sed 's/^ *//;s/ *$//' || true)"
    command_line="$(/bin/ps -p "$candidate" -o command= 2>/dev/null || true)"
    if [[ "${comm##*/}" == "codex" && "$command_line" != *"codex exec"* && "$command_line" != *"app-server"* && "$command_line" != *"codex-code-mode-host"* ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    parent="$(/bin/ps -p "$candidate" -o ppid= 2>/dev/null | /usr/bin/tr -d ' ' || true)"
    [[ -n "$parent" && "$parent" != "$candidate" ]] || break
    candidate="$parent"
    attempts=$((attempts + 1))
  done
  return 1
}

if [[ ! -f "$REGISTRY" ]]; then
  exit 0
fi

owner_pid="$(find_owner_codex_pid || true)"
owner_start=""
if [[ "$owner_pid" =~ ^[0-9]+$ ]]; then
  owner_start="$(process_start_token "$owner_pid")"
fi

if [[ "$owner_pid" =~ ^[0-9]+$ && -n "$owner_start" ]]; then
  result="$(/usr/bin/python3 "$ROOT/scripts/session_registry.py" --path "$REGISTRY" unregister \
    --session-id "$session_id" --owner-pid "$owner_pid" --owner-start "$owner_start")"
else
  # If the owner has already vanished, pruning removes its exact PID/start
  # lease without risking another client that has the same thread open.
  result="$(/usr/bin/python3 "$ROOT/scripts/session_registry.py" --path "$REGISTRY" prune)"
fi

live_count="$(printf '%s' "$result" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("count", 0))')"
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] SessionEnd thread=$session_id live_sessions=$live_count"
if [[ "$live_count" == "0" ]]; then
  /bin/bash "$ROOT/scripts/deactivate.sh" >/dev/null 2>&1 || true
fi
