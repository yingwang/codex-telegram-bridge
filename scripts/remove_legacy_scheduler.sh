#!/usr/bin/env bash
set -euo pipefail

GUI_DOMAIN="gui/$(/usr/bin/id -u)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

for label in com.ying.codex.telegram.news com.ying.codex.telegram.rp; do
  if /bin/launchctl print "$GUI_DOMAIN/$label" >/dev/null 2>&1; then
    /bin/launchctl bootout "$GUI_DOMAIN/$label"
  fi
  plist="$LAUNCH_AGENTS_DIR/$label.plist"
  if [[ -f "$plist" ]]; then
    /bin/rm -f "$plist"
  fi
done

echo "Removed legacy Codex Telegram LaunchAgents; scheduler state was preserved."
