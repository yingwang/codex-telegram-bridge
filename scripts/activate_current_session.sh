#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_DIR="$HOME/.codex/channels/telegram"
RUNTIME="$CONFIG_DIR/current-session.json"
LOG="$CONFIG_DIR/current-session.log"
THREAD_ID="${CODEX_THREAD_ID:-${CODEX_SESSION_ID:-}}"

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [[ -z "$THREAD_ID" ]]; then
  echo "CODEX_THREAD_ID is missing. Activate from inside a Codex CLI session." >&2
  exit 2
fi

mkdir -p "$CONFIG_DIR"

existing_pid=""
existing_thread=""
if [[ -f "$RUNTIME" ]]; then
  existing_pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid",""))' "$RUNTIME" 2>/dev/null || true)"
  existing_thread="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("thread_id",""))' "$RUNTIME" 2>/dev/null || true)"
fi

if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
  if [[ "$existing_thread" == "$THREAD_ID" ]]; then
    echo "Telegram bridge already active for current Codex session: pid=$existing_pid thread=$THREAD_ID"
    exit 0
  fi
  echo "Telegram bridge is already active for another Codex session: pid=$existing_pid thread=$existing_thread" >&2
  echo "Stop it first with ./scripts/deactivate.sh or /stop from Telegram." >&2
  exit 3
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
if ! kill -0 "$pid" 2>/dev/null; then
  echo "Telegram bridge failed to stay running. Check log: $LOG" >&2
  exit 1
fi

echo "Telegram bridge activated for current Codex session: pid=$pid thread=$THREAD_ID"
echo "Log: $LOG"
