---
name: telegram-bridge
description: Activate, monitor, stop, or use the private Telegram bridge while any interactive Codex CLI session is alive. Use when the user asks to enable Telegram for Codex, receive Telegram text, images, Markdown, PDFs, voice notes, or audio, send Telegram messages/files/optional TTS audio from Codex, check bridge status, stop the bridge, or compare the Codex bridge with Claude Code's Telegram channel without modifying Claude Code.
---

# Telegram Bridge

Use the checked-out `codex-telegram-bridge` repository as the only bridge implementation. The bot token and allowlist live in `~/.codex/channels/telegram/.env`; never print token values.

Do not read or modify Claude Code Telegram files unless the user explicitly asks for a read-only comparison. Never write under `~/.claude`, never reuse Claude Code's Telegram token, and never touch Claude Code plugin settings.

## Core Model

The bridge is an any-live-Codex-session long-polling process. It opens no port, creates no webhook, installs no LaunchAgent, and does not run as a system service. SessionStart registers exact thread/owner leases; one sticky live leader receives Telegram turns, another verified lease takes over if it ends, and the bridge stops only after the final lease ends.

Activation from a Codex CLI session registers the current exact `CODEX_THREAD_ID`. The bridge handles each incoming Telegram message with an ephemeral fork of the current verified leader:

```bash
codex exec fork --ephemeral <current CODEX_THREAD_ID>
```

The fork inherits the exact conversation context without competing for or modifying the interactive thread's active writer. Never use `codex exec resume --last`; it can target the wrong session.

Each Telegram-triggered fork also receives a bounded recent history from the same Telegram chat. The bridge excludes records from other chats and the current inbound message, which is already present as the prompt. This preserves follow-ups such as “继续” while keeping every execution ephemeral.

## Activate

When the user asks to activate, enable, start, or connect Telegram for this Codex session:

```bash
cd /path/to/codex-telegram-bridge
./scripts/activate_current_session.sh
./scripts/status.sh
```

Activation performs a Telegram Bot API preflight. If network sandboxing blocks it, rerun the same activation command with scoped network escalation.

If activation says `CODEX_THREAD_ID is missing`, explain that the bridge must be started from inside a Codex CLI session. Do not fall back to `--last` or a generic session.

Opening another interactive Codex CLI session registers another lease but does not steal the sticky leader. If the leader ends, the bridge moves to a remaining live lease; if the final session ends, it stops. Never use LaunchAgent or a system daemon to keep it alive without a Codex session.

## Receive

Once active, receiving is automatic. The bridge polls Telegram and replies with Codex's final message. Tell the user they can send normal text to the bot, or `/codex <task>` if prefix mode is enabled.

The bridge accepts Telegram photos, image documents (`.jpg`, `.jpeg`, `.png`, `.webp`), Markdown (`.md`, `.markdown`), PDF files, and voice/audio messages when `TELEGRAM_AUDIO_TRANSCRIBE_COMMAND` is configured. The caption is the task. In prefix mode, the caption must begin with `/codex`, `/voice`, `/text`, or `/both`. Incoming files live only in a private per-request directory under `CODEX_WORKDIR` and are deleted after processing.

Voice notes and audio messages are downloaded into the same private request directory, passed to the configured local transcriber, and injected into the Codex prompt as transcripts. A common setup is the generic `cc-telegram-voice/transcribe.py` CLI; use only its local transcription command and do not read or write Claude Code files. If audio arrives without a configured transcriber, the bridge replies with a configuration hint instead of sending unsupported-attachment text.

The bridge may inject a local persistent persona and recent selective memory on each Telegram-triggered `codex exec` call. By default these live outside the repository:

```text
~/.codex/memories/telegram-persona.md
~/.codex/memories/telegram-memory.jsonl
```

Treat these as private local state. Do not commit or print them unless the user explicitly asks. Normal Telegram messages go to inbox. Long-term memory is selective: Codex judges whether a Telegram exchange contains a stable preference, durable persona fact, project/workflow rule, or correction worth remembering. `/remember <text>` or messages starting with `记住：` / `remember:` force a memory.

At the start of every turn where this skill is used, check the private inbox for unread Telegram records before doing anything else:

```bash
cd /path/to/codex-telegram-bridge
python3 scripts/pick_inbox.py --limit 20
```

If the script prints unread messages, summarize them briefly and treat the newest Telegram message as relevant context. Do not print secrets.
If the sandbox cannot write the private inbox directory, the picker uses a private cursor in the system temporary directory and continues instead of replaying everything with an exception.

Supported bot commands:

```text
/start
/id
/help
/status
/stop
/persona
/remember
/memory
/codex <task>
/voice <task>
/text <task>
/both <task>
```

Video, stickers, archives, and other attachment types are not supported. Voice notes and audio messages are supported only after local transcription is configured.

Codex can return generated images, Markdown, and PDF files. It must write them to the exact per-request artifacts directory included in the bridge prompt and append a `<telegram_attachments>` JSON block. The bridge validates that each path remains inside that directory before uploading it.

Codex can also return synthesized speech when `TELEGRAM_TTS_COMMAND` is configured. `TELEGRAM_TTS_MODE=on_demand` sends audio for `/voice`, `/both`, or natural requests such as `语音回复` / `reply in voice`; `/text` or natural text-only requests suppress audio. `mirror` sends audio for inbound voice/audio unless suppressed, and `always` sends audio for every reply unless suppressed. TTS may be sent as Telegram `audio` or `voice` according to `TELEGRAM_TTS_SEND_AS`.

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

To send a supported file directly:

```bash
./scripts/send_file.sh "/path/to/file.pdf" "optional caption"
```

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
