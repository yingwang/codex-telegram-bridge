#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_DIR="$HOME/.codex/channels/telegram"
RUNTIME="$CONFIG_DIR/current-session.json"
LOG="$CONFIG_DIR/current-session.log"
THREAD_ID="${CODEX_THREAD_ID:-${CODEX_SESSION_ID:-}}"
ENV_FILE="$CONFIG_DIR/.env"
HOOK_LOG="$CONFIG_DIR/session-start-hook.log"

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

hook_session_id=""
if [[ ! -t 0 ]]; then
  hook_session_id="$(/usr/bin/python3 -c 'import json,sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
if data.get("hook_event_name") == "SessionStart":
    print(data.get("session_id", ""))
' 2>/dev/null || true)"
fi

if [[ -n "$hook_session_id" ]]; then
  THREAD_ID="$hook_session_id"
  mkdir -p "$CONFIG_DIR"
  touch "$HOOK_LOG"
  chmod 600 "$HOOK_LOG"
  exec >>"$HOOK_LOG" 2>&1
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] SessionStart activation thread=$THREAD_ID"
fi

is_bridge_pid() {
  local candidate="${1:-}"
  local command_line=""
  [[ "$candidate" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$candidate" 2>/dev/null || return 1
  command_line="$(/bin/ps -p "$candidate" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"bridge.py run"* ]]
}

replace_enabled() {
  case "${1:-}" in
    1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Yy]|[Oo][Nn]) return 0 ;;
    *) return 1 ;;
  esac
}

replace_existing="${TELEGRAM_REPLACE_EXISTING:-}"
if [[ -z "$replace_existing" && -f "$ENV_FILE" ]]; then
  replace_existing="$(/usr/bin/python3 -c 'import pathlib,sys
path = pathlib.Path(sys.argv[1])
for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == "TELEGRAM_REPLACE_EXISTING":
        print(value.strip().strip("\"'\''"))
        break
' "$ENV_FILE" 2>/dev/null || true)"
fi

if [[ -z "$THREAD_ID" ]]; then
  echo "CODEX_THREAD_ID is missing. Activate from inside a Codex CLI session." >&2
  exit 2
fi

export CODEX_THREAD_ID="$THREAD_ID"
mkdir -p "$CONFIG_DIR"

existing_pid=""
existing_thread=""
if [[ -f "$RUNTIME" ]]; then
  existing_pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid",""))' "$RUNTIME" 2>/dev/null || true)"
  existing_thread="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("thread_id",""))' "$RUNTIME" 2>/dev/null || true)"
fi

if is_bridge_pid "$existing_pid"; then
  if [[ "$existing_thread" == "$THREAD_ID" ]]; then
    echo "Telegram bridge already active for current Codex session: pid=$existing_pid thread=$THREAD_ID"
    exit 0
  fi
  if replace_enabled "$replace_existing"; then
    echo "Replacing Telegram bridge from another Codex session: pid=$existing_pid thread=$existing_thread"
    kill "$existing_pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      if ! is_bridge_pid "$existing_pid"; then
        break
      fi
      sleep 0.2
    done
    if is_bridge_pid "$existing_pid"; then
      kill -9 "$existing_pid" 2>/dev/null || true
    fi
  else
  echo "Telegram bridge is already active for another Codex session: pid=$existing_pid thread=$existing_thread" >&2
  echo "Stop it first with ./scripts/deactivate.sh or /stop from Telegram." >&2
  echo "Or set TELEGRAM_REPLACE_EXISTING=1 in $ENV_FILE to let new sessions replace old bridges automatically." >&2
  exit 3
  fi
fi

STATE_FILE="$CONFIG_DIR/state-$THREAD_ID.json"

echo "Checking Telegram Bot API access..."
cd "$ROOT"
/usr/bin/python3 bridge.py get-me >/dev/null

export CODEX_BIND_CURRENT_SESSION=1
export TELEGRAM_STATE_PATH="$STATE_FILE"
export TELEGRAM_RUNTIME_PATH="$RUNTIME"
: > "$LOG"
chmod 600 "$LOG"

pid="$(/usr/bin/python3 "$ROOT/scripts/launch_detached.py" "$ROOT" "$LOG" /usr/bin/python3 bridge.py run)"
tmp="$RUNTIME.tmp"
printf '{\n  "pid": %s,\n  "thread_id": "%s",\n  "state_path": "%s",\n  "log_path": "%s",\n  "started_at": "%s"\n}\n' \
  "$pid" "$THREAD_ID" "$STATE_FILE" "$LOG" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$RUNTIME"

sleep 1
if ! is_bridge_pid "$pid"; then
  echo "Telegram bridge failed to stay running. Check log: $LOG" >&2
  exit 1
fi

echo "Telegram bridge activated for current Codex session: pid=$pid thread=$THREAD_ID"
echo "Log: $LOG"
