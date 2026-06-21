---
name: telegram-bridge
description: Activate, monitor, stop, or use the private Telegram bridge for the current Codex CLI session. Use when the user asks to enable Telegram for Codex, receive Telegram messages in the current session, send Telegram messages from Codex, check bridge status, stop the bridge, or compare the Codex bridge with Claude Code's Telegram channel without modifying Claude Code.
---

# Telegram Bridge

Use the checked-out `codex-telegram-bridge` repository as the only bridge implementation. The bot token and allowlist live in `~/.codex/channels/telegram/.env`; never print token values.

Do not read or modify Claude Code Telegram files unless the user explicitly asks for a read-only comparison. Never write under `~/.claude`, never reuse Claude Code's Telegram token, and never touch Claude Code plugin settings.

## Core Model

The bridge is a per-Codex-session long-polling process. It opens no port, creates no webhook, installs no LaunchAgent, and does not run as a system service.

Activation from a Codex CLI session binds Telegram to the current `CODEX_THREAD_ID`. The bridge sends incoming Telegram messages to:

```bash
codex exec resume <current CODEX_THREAD_ID>
```

Never use `codex exec resume --last`; it can target the wrong session.

## Activate

When the user asks to activate, enable, start, or connect Telegram for this Codex session:

```bash
cd /path/to/codex-telegram-bridge
./scripts/activate_current_session.sh
./scripts/status.sh
```

Activation performs a Telegram Bot API preflight. If network sandboxing blocks it, rerun the same activation command with scoped network escalation.

If activation says `CODEX_THREAD_ID is missing`, explain that the bridge must be started from inside a Codex CLI session. Do not fall back to `--last` or a generic session.

If another session already owns the bridge, do not replace it silently. Ask before stopping it.

## Receive

Once active, receiving is automatic. The bridge polls Telegram and replies with Codex's final message. Tell the user they can send normal text to the bot, or `/codex <task>` if prefix mode is enabled.

At the start of every turn where this skill is used, check the private inbox for unread Telegram records before doing anything else:

```bash
cd /path/to/codex-telegram-bridge
python3 scripts/pick_inbox.py --limit 20
```

If the script prints unread messages, summarize them briefly and treat the newest Telegram message as relevant context. Do not print secrets.

Supported bot commands:

```text
/start
/id
/help
/status
/stop
/codex <task>
```

Attachments are intentionally ignored for now.

## Send

When the user asks Codex to send a Telegram message, use:

```bash
cd /path/to/codex-telegram-bridge
./scripts/send.sh "message text"
```

For multiline text, pipe stdin:

```bash
printf '%s\n' "message text" | ./scripts/send.sh
```

Keep messages concise enough for Telegram. The script chunks longer messages.

## Stop

When the user asks to stop or disconnect the bridge:

```bash
cd /path/to/codex-telegram-bridge
./scripts/deactivate.sh
./scripts/status.sh
```

The user can also send `/stop` in Telegram. If stale runtime state remains, `deactivate.sh` or the next activation cleans it up.

## Safety

- Keep private files in `~/.codex/channels/telegram`.
- Do not commit `.env`, tokens, logs, PID files, or runtime state.
- Do not use launchctl, webhooks, public ports, or system-level daemons unless the user explicitly changes the requirement.
- If updating the bridge implementation, run `PYTHONPYCACHEPREFIX=/private/tmp/codex-telegram-pycache python3 -m py_compile bridge.py` afterward.
