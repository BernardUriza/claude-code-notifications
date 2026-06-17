#!/usr/bin/env python3
"""
cc-notify — Native macOS notifications for Claude Code.

Replaces the intrusive voice. Each Claude Code session that finishes fires a
macOS banner (via terminal-notifier), GROUPED per session (no stacking 8
banners: each terminal replaces its own). Click the banner to mute that
session. Fine-grained control with the `ccn` command.

── How the Claude Code hooks call it ──
The hook sends JSON over stdin with: hook_event_name, cwd, transcript_path,
session_id. No subcommand = hook mode (read stdin and notify).

── Control (alias `ccn`) ──
    ccn list            list recent sessions and which ones are muted
    ccn mute <sid>      mute a session (stops notifying)
    ccn mute all        mute ALL sessions at once
    ccn unmute <sid>    re-enable a session     (or `unmute all`)
    ccn quiet           global silence on/off (toggle)
    ccn stop            cut the voice playing RIGHT NOW (instant, any time)
    ccn dnd <min>       Do Not Disturb for N minutes (auto-restores)
    ccn dnd             show how much DND time is left
    ccn dnd off         cancel DND before it expires
    ccn sound           sound on/off (toggle) — banner without the ding
    ccn clear           clear all on-screen banners + reset state
    ccn status          current state (quiet, dnd, sound, muted sessions)

── Manual test ──
    echo '{"hook_event_name":"Stop","cwd":"/tmp/symfarmia","session_id":"abc123"}' \
        | python3 cc_notify.py
"""

import os
import re
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path

# The Windows console defaults to cp1252 and blows up on emojis (the `ccn
# list/status` prints use them). UTF-8 so it doesn't crash.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

IS_WINDOWS = sys.platform.startswith("win")
# CREATE_NO_WINDOW: keep PowerShell subprocesses from flashing a black console
# window when they run from a hook (Windows would otherwise pop one up).
CREATE_NO_WINDOW = 0x08000000
# Our own AppUserModelID. Windows 11 only shows the floating banner for an AUMID
# registered as an app (via a Start Menu shortcut + registry); the generic
# PowerShell AUMID gets dropped straight into the Action Center with no banner.
# `install.py --register-toast` sets this up. Without it the toast still lands
# in the Action Center, just without the popup.
TOAST_AUMID = "ClaudeCode.VoiceHandler"

# ── State on disk (flags as files: simple, no daemon) ──
STATE = Path.home() / ".cc-notify"
MUTE_DIR = STATE / "mute"          # mute/<sid> = muted session
SESS_DIR = STATE / "sessions"      # sessions/<sid>.json = latest info (for `list`)
QUIET = STATE / "quiet"            # exists = global silence
NOSOUND = STATE / "nosound"        # exists = banner without sound

# Config (~/.cc-notify/config.json) with sensible defaults if it's missing.
try:
    import cc_config
    _CFG = cc_config.load()
except Exception:
    _CFG = {"sound": "Glass", "max_chars_banner": 140, "dnd_default_min": 60}

SOUND = _CFG.get("sound", "Glass")        # banner sound (macOS); "" = silent. `ccn sound` toggles it
MAX_CHARS = _CFG.get("max_chars_banner", 140)  # cut the message here (banner won't fit more)
DND_DEFAULT_MIN = _CFG.get("dnd_default_min", 60)  # default DND duration
SELF = str(Path(__file__).resolve())
PY = sys.executable or "python3"
TN = shutil.which("terminal-notifier")
DND = STATE / "dnd"               # exists with a timestamp = Do Not Disturb until that time
VOICE_LOCK = STATE / "voice.lock"  # active speaker's PID; poison it to cut the voice mid-flight


def ensure_dirs() -> None:
    for d in (STATE, MUTE_DIR, SESS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def short_sid(sid: str | None) -> str:
    """Short, readable SID for groups and commands."""
    if not sid:
        return "claude"
    return re.sub(r"[^A-Za-z0-9_-]", "", sid)[:8] or "claude"


# ─────────────────────────────────────────────────────────────────────────────
# HOOK MODE
# ─────────────────────────────────────────────────────────────────────────────
def read_stdin_json() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def clean(text: str) -> str:
    """Strip markdown/code/emojis for a clean banner."""
    if "```" in text:
        text = text.split("```", 1)[0]
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # [txt](url) -> txt
    text = re.sub(r"[*_`#>~|]", "", text)                  # markdown symbols
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text) # emojis
    text = " ".join(text.split())
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def last_assistant_text(transcript_path: str) -> str | None:
    p = Path(transcript_path)
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content")
        texts = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t:
                        texts.append(t)
        elif isinstance(content, str) and content.strip():
            texts.append(content.strip())
        if texts:
            return " ".join(texts)
    return None


def _xml_escape(s: str) -> str:
    """Escape the characters that would break the WinRT toast XML."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def _notify_windows(title: str, subtitle: str, message: str, group: str) -> None:
    """Native Windows 10/11 toast, NON-blocking.

    Grouped per session (each terminal replaces its own toast via Tag/group):
      - BurntToast if the module is installed (better UX, `Install-Module BurntToast`).
      - Otherwise WinRT (Windows.UI.Notifications) inline with PowerShell's AppId
        — zero dependencies, works on any Windows 10/11.
    Text/title go through env vars to avoid fighting PowerShell's escaping.
    """
    body = f"{subtitle}: {message}" if message else subtitle
    silent = NOSOUND.exists()
    audio_xml = '<audio silent="true"/>' if silent else ''
    # Full XML built in Python (values already escaped): PowerShell only does
    # LoadXml($env:CC_TOAST_XML), so there's no fighting quotes on the line.
    toast_xml = (
        '<toast><visual><binding template="ToastGeneric">'
        f'<text>{_xml_escape(title)}</text>'
        f'<text>{_xml_escape(body)}</text>'
        f'</binding></visual>{audio_xml}</toast>'
    )
    env = os.environ.copy()
    env["CC_TITLE"] = title
    env["CC_BODY"] = body
    env["CC_GROUP"] = group or "claude"
    env["CC_TOAST_XML"] = toast_xml
    env["CC_AUMID"] = TOAST_AUMID

    silent_bt = "-Silent" if silent else ""
    ps = (
        "$ErrorActionPreference='Stop';"
        "if (Get-Module -ListAvailable -Name BurntToast) {"
        "  Import-Module BurntToast;"
        f"  New-BurntToastNotification -Text $env:CC_TITLE,$env:CC_BODY -UniqueIdentifier $env:CC_GROUP {silent_bt};"
        "} else {"
        "  [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "  [Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;"
        "  $doc=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "  $doc.LoadXml($env:CC_TOAST_XML);"
        "  $t=New-Object Windows.UI.Notifications.ToastNotification $doc;"
        "  $t.Tag=$env:CC_GROUP; $t.Group='claude-code';"
        # Remove this session's previous toast first: re-Showing the SAME tag is
        # treated as a silent update (no banner). Removing then Showing makes
        # Windows pop the banner again, while still not stacking 8 of them.
        "  try { [Windows.UI.Notifications.ToastNotificationManager]::History.Remove($env:CC_GROUP,'claude-code',$env:CC_AUMID) } catch {};"
        "  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:CC_AUMID).Show($t);"
        "}"
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        env=env,
        creationflags=CREATE_NO_WINDOW,   # no console window flash on the banner
    )


def notify(title: str, subtitle: str, message: str, group: str) -> None:
    """macOS banner with a "Stop voice" button via Hammerspoon (hs.notify).

    Hammerspoon is the only thing that shows banners WITH a button on macOS 26
    (terminal-notifier 2.0.0 is broken; osascript doesn't support buttons). The
    "Stop voice" button cuts the afplay of the Arbor voice. If `hs` doesn't
    respond, it falls back to osascript (banner without a button); to mute use
    `ccn mute`/`ccn quiet`.

    On Windows it routes to native toasts (BurntToast / WinRT).
    """
    if IS_WINDOWS:
        _notify_windows(title, subtitle, message, group)
        return

    hs = shutil.which("hs")
    if hs:
        def _lua(s: str) -> str:
            s = str(s).replace("\\", "\\\\").replace('"', '\\"')
            return '"' + s.replace("\n", "\\n").replace("\r", " ") + '"'

        snd = "" if NOSOUND.exists() else SOUND
        call = (
            f"ccNotify({_lua(title)},{_lua(subtitle)},"
            f"{_lua(message or ' ')},{_lua(snd)})"
        )
        try:
            r = subprocess.run([hs, "-c", call], capture_output=True, timeout=5)
            if r.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass

    # osascript fallback (no button). ensure_ascii=False for emoji/accents.
    sound = "" if NOSOUND.exists() else f' sound name "{SOUND}"'
    script = (
        f"display notification {json.dumps(message or ' ', ensure_ascii=False)} "
        f"with title {json.dumps(title, ensure_ascii=False)} "
        f"subtitle {json.dumps(subtitle, ensure_ascii=False)}{sound}"
    )
    subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_hook() -> None:
    ensure_dirs()
    data = read_stdin_json()
    event = data.get("hook_event_name", "Stop")
    sid = short_sid(data.get("session_id"))

    # Silences: global, DND timer, or per-session → don't even bother.
    dnd_active = False
    if DND.exists():
        try:
            exp = float(DND.read_text(encoding="utf-8").strip())
            if exp > time.time():
                dnd_active = True
            else:
                DND.unlink(missing_ok=True)   # expired on its own
        except (OSError, ValueError):
            DND.unlink(missing_ok=True)

    if QUIET.exists() or dnd_active or (MUTE_DIR / sid).exists():
        sys.exit(0)

    cwd = data.get("cwd") or ""
    project = Path(cwd).name or "Claude Code"

    if event == "Notification":
        message = clean(data.get("message") or "Needs your attention")
        subtitle = "Needs input"
    else:  # Stop / SubagentStop
        tp = data.get("transcript_path")
        message = clean(last_assistant_text(tp) or "Done") if tp else "Done"
        subtitle = "Done" if event == "Stop" else "Subagent done"

    # Save metadata for `ccn list`.
    try:
        (SESS_DIR / f"{sid}.json").write_text(json.dumps({
            "sid": sid, "project": project, "subtitle": subtitle,
            "message": message, "ts": time.time(),
        }), encoding="utf-8")
    except OSError:
        pass

    notify(f"✅ {project}", subtitle, message, group=sid)
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# CONTROL MODE (alias `ccn`)
# ─────────────────────────────────────────────────────────────────────────────
def remove_banner(group: str) -> None:
    if TN:
        subprocess.run([TN, "-remove", group],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_list() -> None:
    ensure_dirs()
    files = sorted(SESS_DIR.glob("*.json"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        print("No sessions recorded yet.")
        return
    gq = " (GLOBAL SILENCE active)" if QUIET.exists() else ""
    print(f"Recent sessions{gq}:")
    print(f"  {'SID':<10} {'PROJECT':<22} {'STATE':<6} LAST")
    now = time.time()
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = d.get("sid", f.stem)
        muted = "MUTE" if (MUTE_DIR / sid).exists() else "on"
        ago = int(now - d.get("ts", now))
        when = f"{ago}s" if ago < 60 else f"{ago // 60}m"
        msg = (d.get("message", "") or "")[:34]
        print(f"  {sid:<10} {d.get('project', '?'):<22} {muted:<6} {when:>4} · {msg}")
    print("\n  ccn mute <sid> · ccn unmute <sid> · ccn quiet · ccn clear")


def cmd_mute(target: str | None) -> None:
    ensure_dirs()
    if target in (None, ""):
        print("Usage: ccn mute <sid>   (or  ccn mute all)")
        return
    if target == "all":
        n = 0
        for f in SESS_DIR.glob("*.json"):
            (MUTE_DIR / f.stem).touch()
            remove_banner(f.stem)
            n += 1
        cut_active_voice()   # silence silences both: cut whatever is playing now
        print(f"🔇 Muted {n} sessions. (ccn unmute all to revert)")
        return
    sid = short_sid(target)
    (MUTE_DIR / sid).touch()
    remove_banner(sid)
    print(f"🔇 Session {sid} muted.")


def cmd_unmute(target: str | None) -> None:
    ensure_dirs()
    if target == "all":
        n = 0
        for f in MUTE_DIR.glob("*"):
            f.unlink(missing_ok=True)
            n += 1
        print(f"🔔 Re-enabled {n} sessions.")
        return
    if not target:
        print("Usage: ccn unmute <sid>   (or  ccn unmute all)")
        return
    sid = short_sid(target)
    (MUTE_DIR / sid).unlink(missing_ok=True)
    print(f"🔔 Session {sid} re-enabled.")


def cmd_quiet() -> None:
    ensure_dirs()
    if QUIET.exists():
        QUIET.unlink(missing_ok=True)
        print("🔔 Global silence OFF — notifications are back.")
    else:
        QUIET.touch()
        cut_active_voice()   # silence silences both: cut whatever is playing now
        print("🔇 Global silence ON — zero notifications (ccn quiet to revert).")


def cmd_sound() -> None:
    ensure_dirs()
    if NOSOUND.exists():
        NOSOUND.unlink(missing_ok=True)
        print(f"🔔 Sound ON ({SOUND}).")
    else:
        NOSOUND.touch()
        print("🤫 Sound OFF — silent banners (still visible).")


def cmd_clear() -> None:
    ensure_dirs()
    if TN:
        subprocess.run([TN, "-remove", "ALL"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for f in SESS_DIR.glob("*.json"):
        f.unlink(missing_ok=True)
    cut_active_voice()   # clear means clear: cut whatever is playing now too
    print("🧹 Banners cleared and list reset.")


def cmd_status() -> None:
    ensure_dirs()
    q = "ON 🔇" if QUIET.exists() else "off"
    s = "off 🤫" if NOSOUND.exists() else f"on ({SOUND})"
    muted = sorted(f.name for f in MUTE_DIR.glob("*"))

    dnd_str = "off"
    if DND.exists():
        try:
            exp = float(DND.read_text(encoding="utf-8").strip())
            rem = exp - time.time()
            if rem > 0:
                h, m = divmod(int(rem), 3600)
                m2 = m // 60
                dnd_str = f"⏳ {h}h{m2:02d}min left (until {_fmt_time(exp)})" if h else f"⏳ {m2}min left (until {_fmt_time(exp)})"
            else:
                DND.unlink(missing_ok=True)
        except (OSError, ValueError):
            DND.unlink(missing_ok=True)

    print(f"Global silence: {q}")
    print(f"DND:            {dnd_str}")
    print(f"Sound:          {s}")
    print(f"Muted sessions: {', '.join(muted) if muted else '(none)'}")
    if IS_WINDOWS:
        bt = "yes" if shutil.which("powershell") else "NO (powershell missing)"
        print(f"toast backend:  Windows (BurntToast/WinRT) · powershell: {bt}")
    else:
        print(f"terminal-notifier: {'yes' if TN else 'NO (using osascript)'}")


def cmd_dnd(arg: str | None) -> None:
    """DND with a timer: `ccn dnd 60` mutes for 60 min and auto-restores."""
    ensure_dirs()
    if arg in (None, ""):
        # Show current state
        if DND.exists():
            try:
                exp = float(DND.read_text(encoding="utf-8").strip())
                rem = exp - time.time()
                if rem > 0:
                    h, m = divmod(int(rem), 3600)
                    m2 = m // 60
                    s2 = m % 60
                    if h:
                        print(f"⏳ DND active — {h}h{m2:02d}min left (until {_fmt_time(exp)}).")
                    else:
                        print(f"⏳ DND active — {m2}min {s2:02d}s left (until {_fmt_time(exp)}).")
                    return
            except (OSError, ValueError):
                pass
        print("DND inactive. Usage: ccn dnd <minutes>  |  ccn dnd off")
        return
    if arg in ("off", "cancel", "0"):
        DND.unlink(missing_ok=True)
        print("🔔 DND canceled — notifications are back.")
        return
    try:
        minutes = int(arg)
    except ValueError:
        print(f"Error: '{arg}' is not a number of minutes. E.g.: ccn dnd 60")
        return
    if minutes <= 0:
        DND.unlink(missing_ok=True)
        print("🔔 DND canceled.")
        return
    exp = time.time() + minutes * 60
    DND.write_text(str(exp), encoding="utf-8")
    cut_active_voice()   # silence silences both: cut whatever is playing now
    h, m = divmod(minutes, 60)
    dur = f"{h}h{m:02d}min" if h else f"{minutes}min"
    print(f"🤫 DND enabled for {dur} (until {_fmt_time(exp)}). Auto-restores.")


def _fmt_time(ts: float) -> str:
    """Local time HH:MM to show when DND expires."""
    import time as _t
    lt = _t.localtime(ts)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"


def cut_active_voice() -> None:
    """Stop any voice playing RIGHT NOW — instantly and cooperatively.

    Primary mechanism: poison the shared voice lock with a sentinel. Every active
    player (MediaPlayer / SAPI / afplay) polls this lock every 200ms and stops
    itself the moment the owner PID no longer matches (cc_voice_lite._play_audio_file
    / _say_local). Cut in ≤200ms, no process kill, no PID-reuse hazard. The next
    legitimate voice re-claims the slot with its own PID, so the sentinel never
    poisons future speech.

    Belt-and-suspenders: also hard-kill any player still in its ~300ms pre-roll
    (before its poll loop starts). Best-effort; never raises.
    """
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        VOICE_LOCK.write_text("stop", encoding="utf-8")
    except OSError:
        pass
    if IS_WINDOWS:
        # Kill the player powershell (MediaPlayer or SAPI). Both carry
        # $env:CC_VOICE_LOCK literally in their -Command, so it's a safe match.
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" "
             "| Where-Object { $_.CommandLine -match 'CC_VOICE_LOCK' } "
             "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW)
    else:
        subprocess.run(["pkill", "-x", "afplay"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-f", "cc_voice_lite.py --speak-now"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_stop() -> None:
    """Cut the voice that's currently playing and any pending speech."""
    cut_active_voice()
    print("⏹️  Voice stopped.")


def main() -> None:
    if len(sys.argv) < 2:
        run_hook()
        return
    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    dispatch = {
        "list": lambda: cmd_list(),
        "ls": lambda: cmd_list(),
        "mute": lambda: cmd_mute(arg),
        "unmute": lambda: cmd_unmute(arg),
        "quiet": lambda: cmd_quiet(),
        "sound": lambda: cmd_sound(),
        "clear": lambda: cmd_clear(),
        "status": lambda: cmd_status(),
        "stop": lambda: cmd_stop(),
        "dnd": lambda: cmd_dnd(arg),
    }
    fn = dispatch.get(cmd)
    if fn:
        fn()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
