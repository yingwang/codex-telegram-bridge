---
description: Activate or use the Telegram bridge for this Codex session
argument-hint: [activate|status|stop|send MESSAGE]
---

Use $telegram-bridge for the current Codex CLI session.

Interpret `$ARGUMENTS` as follows:

- If empty, `activate`, `start`, or `enable`: activate the Telegram bridge for the current Codex session.
- If `status`: check whether the bridge is active.
- If `stop`, `deactivate`, or `disable`: stop the bridge for the current session.
- If it starts with `send `: send the remaining text to Telegram.

Do not use Claude Code's Telegram channel. Do not read or modify `~/.claude`. Do not use `codex exec resume --last`.

