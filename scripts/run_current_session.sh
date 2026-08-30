#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [[ -z "${CODEX_THREAD_ID:-}" && -z "${CODEX_SESSION_ID:-}" ]]; then
  echo "CODEX_THREAD_ID is missing. Start this script from inside a Codex CLI session." >&2
  exit 2
fi

export CODEX_BIND_CURRENT_SESSION=1

cd "$ROOT"
exec /usr/bin/python3 bridge.py run

