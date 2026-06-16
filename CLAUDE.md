# CC--VH-lite

CC--VH-lite is a lightweight notification system for Claude Code.

It runs on macOS and Windows using Python stdlib first. Optional tools improve the experience, but nothing optional is allowed to become required.

## Core Law

The notification exists to disappear.

Claude Code must never wait for a banner, voice, GUI, tray icon, network call, or optional dependency.

## Non-Negotiables

- Hooks must exit with code 0.
- Hooks must return immediately.
- Notification work must run detached.
- Missing dependencies must degrade gracefully.
- Disk is the shared bus.
- `~/.cc-notify/config.json` is the persistent configuration source.
- Environment variables override config.
- Defaults must always work.
- Silence must silence both banner and voice.
- No daemon is required for core behavior.

## Architecture Rules

- Keep scripts independent.
- Duplicate small path/config logic when it prevents runtime coupling.
- Prefer stdlib.
- Add dependencies only as optional enhancements.
- Never introduce a blocking queue, database, web server, or resident service for the core path.

## Fallback Chains

Voice:

1. Azure OpenAI TTS
2. edge-tts
3. local system voice

Banner:

1. native/installed notifier
2. platform fallback
3. no-op without failure

GUI:

1. CustomTkinter
2. Tkinter
3. config file/manual CLI

## Testing Without Claude

Use the JSON samples in the README for Stop and Notification hooks.

A valid test proves:

- The hook exits immediately.
- The banner appears or degrades.
- The voice plays or degrades.
- Quiet mode suppresses both.
- No missing optional dependency crashes the hook.

## Development Rule

Before adding a feature, answer:

Does this help the user stop watching the terminal?

If not, delete the idea.
