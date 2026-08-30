#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_DIR="$HOME/.codex/channels/telegram"
RUNTIME="$CONFIG_DIR/current-session.json"
REGISTRY="$CONFIG_DIR/live-sessions.json"
LIFECYCLE_LOCK="$CONFIG_DIR/session-lifecycle.lock"
LOG="$CONFIG_DIR/current-session.log"
ENV_FILE="$CONFIG_DIR/.env"
HOOK_LOG="$CONFIG_DIR/session-start-hook.log"
GLOBAL_STATE="$CONFIG_DIR/session-companion-state.json"
THREAD_ID="${CODEX_THREAD_ID:-${CODEX_SESSION_ID:-}}"

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [[ "${CODEX_COMPANION_LIFECYCLE_LOCKED:-0}" != "1" ]]; then
  # One-time migration for the first multi-session build, whose detached
  # process accidentally kept the lifecycle flock descriptor open.  It must
  # be stopped before attempting to acquire that same lock.  Validate the
  # exact recorded PID and command, and stop only the bridge parent so an
  # in-flight Telegram-triggered Codex child can finish normally.
  if [[ -f "$RUNTIME" ]]; then
    legacy_pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid", ""))' "$RUNTIME" 2>/dev/null || true)"
    legacy_multi="$(/usr/bin/python3 -c 'import json,sys; print(str(bool(json.load(open(sys.argv[1])).get("multi_session", False))).lower())' "$RUNTIME" 2>/dev/null || true)"
    legacy_lock_safe="$(/usr/bin/python3 -c 'import json,sys; print(str(bool(json.load(open(sys.argv[1])).get("lifecycle_lock_safe", False))).lower())' "$RUNTIME" 2>/dev/null || true)"
    legacy_command="$(/bin/ps -p "${legacy_pid:-0}" -o command= 2>/dev/null || true)"
    if [[ "$legacy_pid" =~ ^[0-9]+$ && "$legacy_multi" == "true" && "$legacy_lock_safe" != "true" && "$legacy_command" == *"bridge.py run"* ]]; then
      kill -TERM "$legacy_pid" 2>/dev/null || true
      for _ in 1 2 3 4 5; do
        kill -0 "$legacy_pid" 2>/dev/null || break
        sleep 0.2
      done
    fi
  fi
  export CODEX_COMPANION_LIFECYCLE_LOCKED=1
  exec /usr/bin/python3 "$ROOT/scripts/with_lock.py" "$LIFECYCLE_LOCK" /bin/bash "$0" "$@"
fi

hook_session_id=""
hook_source=""
if [[ ! -t 0 ]]; then
  hook_record="$(/usr/bin/python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
if data.get("hook_event_name") == "SessionStart":
    print(str(data.get("session_id", "")) + "\t" + str(data.get("source", "")))
' 2>/dev/null || true)"
  if [[ -n "$hook_record" ]]; then
    IFS=$'\t' read -r hook_session_id hook_source <<<"$hook_record"
    THREAD_ID="$hook_session_id"
  fi
fi

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

is_bridge_pid() {
  local candidate="${1:-}" command_line=""
  [[ "$candidate" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$candidate" 2>/dev/null || return 1
  command_line="$(/bin/ps -p "$candidate" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"bridge.py run"* ]]
}

stop_bridge_process() {
  local candidate="${1:-}" pgid="${2:-}" actual_pgid=""
  is_bridge_pid "$candidate" || return 0
  actual_pgid="$(/bin/ps -p "$candidate" -o pgid= 2>/dev/null | /usr/bin/tr -d ' ' || true)"
  if [[ "$pgid" =~ ^[0-9]+$ && "$actual_pgid" == "$pgid" ]]; then
    kill -TERM -- "-$pgid" 2>/dev/null || kill -TERM "$candidate" 2>/dev/null || true
  else
    kill -TERM "$candidate" 2>/dev/null || true
  fi
  for _ in 1 2 3 4 5; do
    is_bridge_pid "$candidate" || return 0
    sleep 0.2
  done
  if [[ "$pgid" =~ ^[0-9]+$ && "$(/bin/ps -p "$candidate" -o pgid= 2>/dev/null | /usr/bin/tr -d ' ' || true)" == "$pgid" ]]; then
    kill -KILL -- "-$pgid" 2>/dev/null || kill -KILL "$candidate" 2>/dev/null || true
  else
    kill -KILL "$candidate" 2>/dev/null || true
  fi
}

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
if [[ -n "$hook_session_id" ]]; then
  touch "$HOOK_LOG"
  chmod 600 "$HOOK_LOG"
  exec >>"$HOOK_LOG" 2>&1
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] SessionStart source=$hook_source thread=$THREAD_ID"
fi

if [[ -z "$THREAD_ID" ]]; then
  echo "CODEX_THREAD_ID is missing. Activate from inside a Codex CLI session." >&2
  exit 2
fi

OWNER_PID="${CODEX_SESSION_OWNER_PID_OVERRIDE:-}"
if [[ -n "$OWNER_PID" && ! "$OWNER_PID" =~ ^[0-9]+$ ]]; then
  echo "CODEX_SESSION_OWNER_PID_OVERRIDE must be a numeric PID." >&2
  exit 2
fi
if [[ -z "$OWNER_PID" ]]; then
  OWNER_PID="$(find_owner_codex_pid || true)"
fi
if [[ -z "$OWNER_PID" && -f "$RUNTIME" ]]; then
  runtime_thread="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("thread_id", ""))' "$RUNTIME" 2>/dev/null || true)"
  runtime_owner_pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("owner_pid", ""))' "$RUNTIME" 2>/dev/null || true)"
  runtime_owner_start="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("owner_start", ""))' "$RUNTIME" 2>/dev/null || true)"
  if [[ "$runtime_thread" == "$THREAD_ID" && "$runtime_owner_pid" =~ ^[0-9]+$ && -n "$runtime_owner_start" && "$(process_start_token "$runtime_owner_pid")" == "$runtime_owner_start" ]]; then
    OWNER_PID="$runtime_owner_pid"
  fi
fi
if [[ ! "$OWNER_PID" =~ ^[0-9]+$ ]]; then
  echo "Could not identify an interactive Codex CLI owner; refusing an unbounded companion." >&2
  exit 2
fi
OWNER_START="$(process_start_token "$OWNER_PID")"
if [[ -z "$OWNER_START" ]]; then
  echo "The owning Codex CLI process is no longer running." >&2
  exit 2
fi
SESSION_STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

registry_json="$(/usr/bin/python3 "$ROOT/scripts/session_registry.py" --path "$REGISTRY" register \
  --session-id "$THREAD_ID" --owner-pid "$OWNER_PID" --owner-start "$OWNER_START" --started-at "$SESSION_STARTED_AT")"
leader_thread="$(printf '%s' "$registry_json" | /usr/bin/python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("leader") or {}).get("session_id", ""))')"
leader_pid="$(printf '%s' "$registry_json" | /usr/bin/python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("leader") or {}).get("owner_pid", ""))')"
leader_start="$(printf '%s' "$registry_json" | /usr/bin/python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("leader") or {}).get("owner_start", ""))')"
live_count="$(printf '%s' "$registry_json" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("count", 0))')"
if [[ -z "$leader_thread" || ! "$leader_pid" =~ ^[0-9]+$ || -z "$leader_start" ]]; then
  echo "Live-session registry did not select a valid leader." >&2
  exit 1
fi

existing_pid="" existing_pgid="" existing_state="" existing_multi="" existing_lock_safe=""
if [[ -f "$RUNTIME" ]]; then
  existing_pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid", ""))' "$RUNTIME" 2>/dev/null || true)"
  existing_pgid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pgid", ""))' "$RUNTIME" 2>/dev/null || true)"
  existing_state="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("state_path", ""))' "$RUNTIME" 2>/dev/null || true)"
  existing_multi="$(/usr/bin/python3 -c 'import json,sys; print(str(bool(json.load(open(sys.argv[1])).get("multi_session", False))).lower())' "$RUNTIME" 2>/dev/null || true)"
  existing_lock_safe="$(/usr/bin/python3 -c 'import json,sys; print(str(bool(json.load(open(sys.argv[1])).get("lifecycle_lock_safe", False))).lower())' "$RUNTIME" 2>/dev/null || true)"
fi

if is_bridge_pid "$existing_pid" && [[ "$existing_multi" == "true" && "$existing_lock_safe" == "true" ]]; then
  echo "Telegram companion already covers $live_count live Codex session(s): pid=$existing_pid"
  exit 0
fi

if [[ -z "$hook_session_id" ]]; then
  echo "Checking Telegram Bot API access..."
  (cd "$ROOT" && /usr/bin/python3 bridge.py get-me >/dev/null)
fi

if [[ ! -f "$GLOBAL_STATE" && -n "$existing_state" && -f "$existing_state" ]]; then
  /bin/cp "$existing_state" "$GLOBAL_STATE"
  chmod 600 "$GLOBAL_STATE"
fi
if is_bridge_pid "$existing_pid"; then
  if [[ "$existing_multi" == "true" && "$existing_lock_safe" != "true" ]]; then
    # The first multi-session build accidentally inherited the lifecycle lock.
    # Stop only its parent so an in-flight Telegram Codex child can finish.
    echo "Replacing the legacy lock-holding Telegram companion: pid=$existing_pid"
    kill -TERM "$existing_pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      is_bridge_pid "$existing_pid" || break
      sleep 0.2
    done
  else
    echo "Replacing the previous single-session Telegram bridge: pid=$existing_pid"
    stop_bridge_process "$existing_pid" "$existing_pgid"
  fi
fi

export CODEX_THREAD_ID="$leader_thread"
export CODEX_BIND_CURRENT_SESSION=1
export CODEX_SESSION_SCHEDULER=1
export CODEX_SESSION_COMPANION_STARTED_AT="$SESSION_STARTED_AT"
export CODEX_SESSION_OWNER_PID="$leader_pid"
export CODEX_SESSION_OWNER_START="$leader_start"
export CODEX_SESSION_REGISTRY_PATH="$REGISTRY"
export TELEGRAM_STATE_PATH="$GLOBAL_STATE"
export TELEGRAM_RUNTIME_PATH="$RUNTIME"
: >"$LOG"
chmod 600 "$LOG"

cd "$ROOT"
launch_info="$(/usr/bin/python3 "$ROOT/scripts/launch_detached.py" "$ROOT" "$LOG" /usr/bin/python3 bridge.py run)"
read -r pid pgid <<<"$launch_info"
if [[ ! "$pid" =~ ^[0-9]+$ || ! "$pgid" =~ ^[0-9]+$ ]]; then
  echo "Telegram companion launcher returned invalid process metadata." >&2
  exit 1
fi

tmp="$RUNTIME.tmp"
printf '{\n  "pid": %s,\n  "pgid": %s,\n  "thread_id": "%s",\n  "state_path": "%s",\n  "log_path": "%s",\n  "started_at": "%s",\n  "owner_pid": %s,\n  "owner_start": "%s",\n  "registry_path": "%s",\n  "live_session_count": %s,\n  "session_scoped": true,\n  "lifecycle_lock_safe": true,\n  "multi_session": true\n}\n' \
  "$pid" "$pgid" "$leader_thread" "$GLOBAL_STATE" "$LOG" "$SESSION_STARTED_AT" "$leader_pid" "$leader_start" "$REGISTRY" "$live_count" >"$tmp"
chmod 600 "$tmp"
mv "$tmp" "$RUNTIME"

sleep 1
if ! is_bridge_pid "$pid"; then
  echo "Telegram companion failed to stay running. Check log: $LOG" >&2
  exit 1
fi

echo "Telegram companion active while any Codex session remains: pid=$pid live_sessions=$live_count"
