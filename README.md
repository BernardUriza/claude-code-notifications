# claude-code-notifications 🔔

> **CC--VH-lite** = Claude Code Voice Handler — lite.

Notifications for Claude Code on **macOS and Windows**, pure Python stdlib
(every extra dependency is optional). It tells you when a Claude Code session
finishes or asks for permission, so you don't have to watch the terminal. Two
coordinated pieces that live in [`CC--VH-lite/`](CC--VH-lite/):

| Script | What it does |
|---|---|
| **`cc_notify.py`** | Native per-session banner. **macOS**: `terminal-notifier`/Hammerspoon/`osascript`. **Windows**: toast via BurntToast or, with no dependencies, WinRT (`Windows.UI.Notifications`). Grouped: each terminal replaces its own banner, no stacking 8 of them. Fine-grained control with the `ccn` command. |
| **`cc_voice_lite.py`** | Voice that reads a snippet of Claude's last response. Fallback chain: **Azure OpenAI TTS (onyx)** → **edge-tts** (free neural voice, no key) → local voice (`say` on macOS, SAPI on Windows). **Respects cc-notify's silence**: if you mute with `ccn quiet`, the voice goes quiet too. |
| **`cc_onyx_capture.py`** | *(optional)* Stop hook that saves Claude's **full** last response into the onyx feed (`~/.cc-notify/onyx-feed/`) — one small file write, exits instantly. Feeds the panel below. Off-switch: `touch ~/.cc-notify/onyx-off`. |
| **`cc_onyx_panel.py`** | *(optional)* **Native Python GUI** (CustomTkinter → Tkinter fallback, cc_theme palette — same DNA as the config GUI) listing every captured response, newest first, each with a ▶ button that speaks it in the **onyx** voice. Audio is synthesized **lazily** (only when you press play: susurro gateway → Azure TTS → nothing) and cached next to the entry, so capturing everything costs zero. One playback at a time; ▶ toggles to ■. No web server, no localhost, no browser. |

Zero daemon, zero queue, zero web UI. The banner and voice are scripts the
hooks invoke and exit instantly (audio/banner run in the background, never
block Claude). The onyx panel is an on-demand window, not a resident service.

### Onyx panel (optional)

```bash
# capture: add the cc_onyx_capture.py line from settings.snippet.json to your
# Stop/SubagentStop hooks (or re-run install.py) and restart Claude Code.
# view: open the panel whenever you want to LISTEN to what Claude answered.
python3 CC--VH-lite/cc_onyx_panel.py
```

New responses appear in the open window within ~3s (it polls the feed on
disk). Entries are pruned at 200; the panel shows the latest 50.

## Install (one command)

```bash
git clone <this-repo>
python CC--VH-lite/install.py        # macOS/Linux: use python3 if that's your binary
```

The installer detects your platform, the clone path, and the right interpreter
(`python` vs `python3`), **merges** the `Stop` + `Notification` hooks into your
`~/.claude/settings.json` without clobbering what you already have (it backs up
first), and tells you which optional dependencies are missing. It's idempotent —
you can re-run it.

```bash
python CC--VH-lite/install.py --dry-run     # see what it would do, without writing
python CC--VH-lite/install.py --uninstall   # remove only these hooks
```

Then **restart Claude Code**.

> **Manual install** (if you prefer): open `CC--VH-lite/settings.snippet.json`,
> replace `/ABSOLUTE/PATH/TO` with your real clone path, and merge the `hooks`
> block into your `~/.claude/settings.json` (don't overwrite an existing `hooks`
> block — add the keys). On Windows use `python` and `/` paths.

## Optional dependencies

Everything works with nothing extra (banner + local voice). To raise the bar:

| You want… | Install |
|---|---|
| Free neural voice (recommended) | `pip install edge-tts` |
| Azure onyx voice (paid) | create `~/.secrets/azure-openai-key.txt` (format below) |
| "Pretty" Windows banner | `Install-Module BurntToast` (PowerShell). Without it, falls back to WinRT, equally functional. |
| macOS banner with a button | `brew install terminal-notifier` and/or Hammerspoon |

Format of `~/.secrets/azure-openai-key.txt`:
```
AZURE_OPENAI_TTS_KEY: <your-key>
Endpoint: https://<region>.api.cognitive.microsoft.com/
Deployment: tts
API-Version: 2024-02-15-preview
```

## The `ccn` command (cc-notify control)

```
ccn list            recent sessions and which ones are muted
ccn mute <sid>      mute a session       (or `mute all`)
ccn unmute <sid>    re-enable a session  (or `unmute all`)
ccn quiet           global silence on/off (toggle) — also mutes the voice
ccn sound           sound on/off (toggle) — banner without the ding
ccn clear           clear on-screen banners + reset state
ccn status          current state (quiet, sound, muted sessions)
ccn stop            cut off the voice that's currently playing
```

## Test without Claude

```bash
# Banner (use `python` on Windows, `python3` on macOS)
echo '{"hook_event_name":"Stop","cwd":"/tmp/my-repo","session_id":"abc123"}' \
  | python CC--VH-lite/cc_notify.py

# Voice (reads a real transcript)
echo '{"hook_event_name":"Stop","transcript_path":"/path/to/transcript.jsonl"}' \
  | python CC--VH-lite/cc_voice_lite.py

# Voice, direct test mode
python CC--VH-lite/cc_voice_lite.py --say "Testing, one two three"
```

## Platform notes

- Claude Code runs the hooks through a POSIX shell (**Git Bash on Windows**),
  which is why paths use `/` and `python` must be on your PATH. The installer
  handles this for you.
- On Windows there's a known bug where `settings.json` hooks are **not** invoked
  inside the **Claude Desktop App** (they do fire in the CLI).
- On Linux the voice works (edge-tts); the `cc_notify.py` banners are aimed at
  macOS/Windows.

## Why it doesn't block Claude

Each script launches in a detached process (`start_new_session=True` on POSIX,
`DETACHED_PROCESS` on Windows): Claude doesn't wait for the banner or the audio,
and the hook exits with `exit 0` instantly.

---

> History: it started as a **voice** notification (`say`), which turned out to be
> intrusive with several terminals talking over each other. `cc_notify.py`
> replaced it with native, mutable banners, and the voice was rebuilt to
> integrate with its silencer. The original over-engineered version (Qwen +
> OpenAI TTS + SQLite queue + web UI) is frozen in the git history.
