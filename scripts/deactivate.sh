#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="$HOME/.codex/channels/telegram"
RUNTIME="$CONFIG_DIR/current-session.json"

if [[ ! -f "$RUNTIME" ]]; then
  echo "No Codex Telegram bridge runtime file found."
  exit 0
fi

pid="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pid",""))' "$RUNTIME" 2>/dev/null || true)"
thread_id="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("thread_id",""))' "$RUNTIME" 2>/dev/null || true)"

if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
  fi
  echo "Stopped Codex Telegram bridge: pid=$pid thread=$thread_id"
else
  echo "Codex Telegram bridge process is not running: pid=$pid thread=$thread_id"
fi

rm -f "$RUNTIME"

