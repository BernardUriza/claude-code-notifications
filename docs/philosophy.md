# Philosophy

## The Notification Exists to Disappear

CC--VH-lite is not a productivity platform.

It is not a dashboard.

It is not an assistant beside the assistant.

It is a servant process that tells the user when Claude Code needs attention, then gets out of the way.

## 1. Never Block Claude

Claude Code is the work.

The notification is smoke.

Every hook must return immediately with exit code 0. Any banner, voice, timer, tray, or GUI behavior must run detached from the hook path.

If Claude waits for the notifier, the notifier has betrayed its purpose.

## 2. Work Naked, Improve Dressed

The app must work with Python stdlib first.

Optional dependencies are allowed only when they improve the experience without becoming part of the survival path.

Fallbacks are not decoration. They are the architecture.

Voice degrades:

Azure OpenAI TTS → edge-tts → local system voice.

GUI degrades:

CustomTkinter → Tkinter → config file.

Notifications degrade:

Preferred native notifier → platform fallback → safe no-op.

## 3. Disk Is the Bus

There is no daemon requirement.

There is no database requirement.

There is no queue requirement.

Small state belongs on disk under `~/.cc-notify/`.

This keeps the system inspectable, recoverable, and boring.

Boring is the point.

## 4. Silence Is Sovereign

If the user says quiet, everything obeys.

The banner does not nag.

The voice does not speak.

The tray does not argue.

A notification tool that ignores silence is just another interruption with better branding.

## 5. What Was Removed Matters

The heavier version failed because it forgot the law.

Qwen, OpenAI TTS, SQLite queues, web UI, and long-running orchestration increased the surface area until the notifier became the problem.

CC--VH-lite is the scar tissue.

Do not rebuild the wound.

## Final Rule

Before adding anything, ask:

Does this reduce terminal staring without increasing ceremony?

If yes, keep it small.

If no, kill it.
