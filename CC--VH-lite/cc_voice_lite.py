#!/usr/bin/env python3
"""
CC--VH-lite — ONYX voice for Claude Code (rebuilt 2026-06-03).

Azure OpenAI TTS, onyx voice ONLY. No Arbor (the nasty voice that hung for 30s
and couldn't be stopped) and no local service. Speaks a snippet of what Claude
Code said, in the background (detached), without blocking Claude.

Respects cc-notify's silence: if ~/.cc-notify/quiet or ~/.cc-voice-off exists,
it does NOT speak (the .py is read fresh on every hook, so `ccn quiet` mutes it
without restarting Claude Code).

Events:
    - Stop / SubagentStop → Claude's last response (from the .jsonl transcript)
    - Notification        → the notification text (the "message" field)

Config: ~/.secrets/azure-openai-key.txt (a notes file with the line
    AZURE_OPENAI_TTS_KEY: <key>, plus Endpoint/Deployment/API-Version).

Manual test:
    python3 cc_voice_lite.py --say "Testing the onyx voice"
"""

import os
import re
import sys
import json
import time
import shutil
import argparse
import subprocess
import tempfile
import urllib.request
from pathlib import Path

# ── Azure OpenAI TTS config (onyx) ──
SECRET_FILE = Path.home() / ".secrets" / "azure-openai-key.txt"

IS_WINDOWS = sys.platform.startswith("win")
# CREATE_NO_WINDOW: keep the PowerShell playback/voice subprocesses from flashing
# a black console window.
CREATE_NO_WINDOW = 0x08000000

# Windows local fallback (SAPI via System.Speech). Empty voice = the system
# default. List the installed ones with:
#   powershell "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices().VoiceInfo.Name"
WIN_SAY_VOICE = os.environ.get("WIN_SAY_VOICE", "")


def _cfg(name: str, default: str = "") -> str:
    """Read 'name: value' (or 'name=value') from the secrets notes file.

    Takes the first token of the value (cuts comments like '(verified ...)').
    Environment variables take priority.
    """
    env = os.environ.get(name)
    if env:
        return env
    if SECRET_FILE.exists():
        for line in SECRET_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(rf"^\s*{re.escape(name)}\s*[:=]\s*(\S+)", line, re.I)
            if m:
                return m.group(1).strip()
    return default


AZURE_KEY = _cfg("AZURE_OPENAI_TTS_KEY") or _cfg("AZURE_OPENAI_KEY")
AZURE_ENDPOINT = _cfg("Endpoint", "https://northcentralus.api.cognitive.microsoft.com/").rstrip("/")
AZURE_DEPLOYMENT = _cfg("Deployment", "tts")
AZURE_API_VERSION = _cfg("API-Version", "2024-02-15-preview")

# Shared config (~/.cc-notify/config.json): voice, min_words, etc.
# We use load_raw() (ONLY what's present in the file) so a config default
# doesn't clobber a lower-priority source (e.g. `Voice:` from the secrets file).
# If cc_config doesn't import, _RAW is empty → everything falls back to defaults.
try:
    import cc_config
    _RAW = cc_config.load_raw()
except Exception:
    cc_config = None
    _RAW = {}


def _conf(key, default):
    """Value from config.json (file), coerced; default if missing/garbage."""
    if cc_config is not None and key in _RAW:
        c = cc_config._coerce(key, _RAW[key])
        if c is not None:
            return c
    return default


# OpenAI voice (alloy, ash, ballad, coral, echo, fable, onyx, nova, sage, shimmer).
# REAL priority: env AZURE_TTS_VOICE > config.json `voice` > secrets `Voice:` > onyx.
# `_conf("voice", None)` returns None if the key is NOT in the file, so the
# `or` falls back to the secrets file instead of clobbering it with a default.
AZURE_VOICE = (_cfg("AZURE_TTS_VOICE") or _conf("voice", None)
               or _cfg("Voice") or "onyx")

# ── edge-tts: FREE neural voice, no API key (Microsoft Edge TTS) ──
# A middle ground between Azure (paid, needs a key) and the robotic local voice.
# Install it with `pip install edge-tts`. List voices with `edge-tts --list-voices`.
EDGE_VOICE = os.environ.get("EDGE_TTS_VOICE", "es-MX-DaliaNeural")

# Last-resort fallback: the macOS local `say` voice (instant, free).
SAY_VOICE = os.environ.get("SAY_VOICE", "Paulina")

# ── Snippet selection (configurable via config.json) ──
# _conf already coerces and falls back to the default if the value is garbage →
# a hand-edited config.json with invalid types does NOT crash the script at
# import time.
MIN_WORDS = _conf("min_words", 30)
MAX_RATIO = 0.5
MAX_CHARS = _conf("max_chars_speech", 600)
SPEAK_REPO = _conf("speak_repo_name", True)
SENTENCE_END = ".!?…"


def _play_audio_file(path: str) -> None:
    """Play an audio file in a blocking way, per platform."""
    if IS_WINDOWS:
        ps = (
            "Add-Type -AssemblyName presentationCore;"
            "$p = New-Object System.Windows.Media.MediaPlayer;"
            "$p.Open([uri]$env:CC_AUDIO_PATH);"
            "Start-Sleep -Milliseconds 300;"
            "$p.Play();"
            "while ($p.NaturalDuration.HasTimeSpan -eq $false) { Start-Sleep -Milliseconds 50 };"
            "Start-Sleep -Seconds $p.NaturalDuration.TimeSpan.TotalSeconds;"
            "$p.Close()"
        )
        env = os.environ.copy()
        env["CC_AUDIO_PATH"] = path
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            env=env, check=False, creationflags=CREATE_NO_WINDOW,
        )
    else:
        subprocess.run(["afplay", path], check=False)


def _say_local(text: str) -> None:
    """Local voice (last resort): SAPI on Windows, `say` on macOS."""
    try:
        if IS_WINDOWS:
            env = os.environ.copy()
            env["CC_VOICE_TEXT"] = text
            env["CC_VOICE_NAME"] = WIN_SAY_VOICE
            ps = (
                "Add-Type -AssemblyName System.Speech;"
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                "if ($env:CC_VOICE_NAME) { try { $s.SelectVoice($env:CC_VOICE_NAME) } catch {} };"
                "$s.Speak($env:CC_VOICE_TEXT)"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                env=env, check=False, creationflags=CREATE_NO_WINDOW,
            )
        else:
            subprocess.run(["say", "-v", SAY_VOICE, text], check=False)
    except Exception:
        pass


def _edge_tts_to_file(text: str, out_path: str) -> bool:
    """Generate an mp3 with edge-tts (neural, free, no key). True if it worked.

    Requires `pip install edge-tts` (not in stdlib). The text goes through a
    subprocess arg (list, no shell) so there are no escaping problems.
    """
    exe = shutil.which("edge-tts")
    if not exe:
        return False
    try:
        r = subprocess.run(
            [exe, "--voice", EDGE_VOICE, "--text", text, "--write-media", out_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        )
        return r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception:
        return False


def speak_blocking(text: str, voice: str) -> None:
    """Generate the audio and play it (afplay on macOS, MediaPlayer on Windows).

    Fallback chain, from best to simplest:
      1. Azure OpenAI TTS onyx (if there's a key in ~/.secrets).
      2. edge-tts (neural, free, no key — if it's installed).
      3. Offline local voice (SAPI on Windows, `say` on macOS).
    """
    if AZURE_KEY:
        try:
            url = (f"{AZURE_ENDPOINT}/openai/deployments/{AZURE_DEPLOYMENT}"
                   f"/audio/speech?api-version={AZURE_API_VERSION}")
            body = json.dumps({
                "model": AZURE_DEPLOYMENT,
                "input": text,
                "voice": voice,
                "response_format": "mp3",
            }).encode("utf-8")
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "api-key": AZURE_KEY,
                "Content-Type": "application/json",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                audio = resp.read()
            if audio:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
                    fh.write(audio)
                    mp3 = fh.name
                try:
                    _play_audio_file(mp3)
                finally:
                    try:
                        os.unlink(mp3)
                    except OSError:
                        pass
                return
        except Exception:
            pass  # fall to the next level
    # 2nd: edge-tts (neural, free, no key) if it's installed.
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
            edge_mp3 = fh.name
        if _edge_tts_to_file(text, edge_mp3):
            try:
                _play_audio_file(edge_mp3)
            finally:
                try:
                    os.unlink(edge_mp3)
                except OSError:
                    pass
            return
        try:
            os.unlink(edge_mp3)
        except OSError:
            pass
    except Exception:
        pass  # fall to the local fallback
    # 3rd: offline local voice (SAPI on Windows, `say` on macOS).
    _say_local(text)


def speak_detached(text: str, voice: str) -> None:
    """Speak in the background and do NOT block Claude Code.

    Re-invokes this script with --speak-now in a detached process, so Claude
    doesn't wait for the audio to finish nor cut it off on exit:
      - macOS:   start_new_session=True (POSIX).
      - Windows: DETACHED_PROCESS, no console window.
    The child process runs speak_blocking (Azure onyx + playback/fallback per
    platform).
    """
    popen_kwargs: dict = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if IS_WINDOWS:
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        popen_kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        popen_kwargs["start_new_session"] = True

    subprocess.Popen(
        [sys.executable or "python3", str(Path(__file__).resolve()),
         "--speak-now", text, "--voice", voice],
        **popen_kwargs,
    )


def read_payload(cli_hook):
    """Read the JSON from stdin. Returns (event, data)."""
    data = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
    except Exception:
        data = {}
    return (cli_hook or data.get("hook_event_name")), data


def last_assistant_text(transcript_path: str):
    """Pull the text of Claude's last response from the .jsonl transcript.

    Walks backwards looking for type='assistant' and the message.content blocks
    with type='text'.
    """
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "assistant":
            continue
        content = (obj.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            txt = " ".join(p for p in parts if p).strip()
            if txt:
                return txt
    return None


def clean_for_speech(text: str) -> str:
    """Strip markdown and code so the voice doesn't read weird symbols."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # markdown links
    text = re.sub(r"[*_`#>~|]", " ", text)                # markdown symbols
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", " ", text)  # emojis
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def pick_words(text: str) -> str:
    """At least MIN_WORDS words, finishing the current sentence.

    The sentence wins: it may exceed the minimum. If the text is shorter than
    the minimum, it says all of it. MAX_RATIO is a safety net when there's no
    punctuation.
    """
    if not text:
        return ""
    words = text.split()
    total = len(words)
    if total <= MIN_WORDS:
        return text.strip(" \")’'»")[:MAX_CHARS]
    # Find the first sentence end after the minimum.
    cut = None
    for i in range(MIN_WORDS, total):
        if words[i] and words[i][-1] in SENTENCE_END:
            cut = i + 1
            break
    if cut is None:
        cut = min(total, max(MIN_WORDS, int(total * MAX_RATIO)))
    return " ".join(words[:cut]).strip(" \")’'»")[:MAX_CHARS]


def project_name(data: dict):
    """Repo/folder name: basename of the cwd the hook sends."""
    cwd = data.get("cwd")
    return Path(cwd).name if cwd else None


def main() -> None:
    parser = argparse.ArgumentParser(description="CC--VH-lite: onyx voice for Claude Code")
    parser.add_argument("--hook", help="Force event (Stop, Notification, ...)")
    parser.add_argument("--say", help="Speak this text and exit (test mode)")
    parser.add_argument("--speak-now", help="(internal) detached process that speaks")
    parser.add_argument("--voice", default=AZURE_VOICE)
    args = parser.parse_args()

    # Internal mode: the detached process that actually speaks.
    if args.speak_now is not None:
        try:
            speak_blocking(args.speak_now, args.voice)
        except Exception:
            pass
        return

    # Manual test mode.
    if args.say:
        speak_detached(args.say, args.voice)
        return

    # The payload carries the session_id, needed for per-session mute.
    event, data = read_payload(args.hook)

    # Silence switch: the voice respects the SAME things as the banner (cc_notify):
    # global quiet, ~/.cc-voice-off, DND timer, and per-session mute. If the payload
    # has no session_id, only the per-session mute check is skipped.
    state = Path.home() / ".cc-notify"
    dnd_active = False
    if (state / "dnd").exists():
        try:
            dnd_active = float((state / "dnd").read_text(encoding="utf-8").strip()) > time.time()
        except (OSError, ValueError):
            dnd_active = False
    sid_raw = data.get("session_id")
    sid = (re.sub(r"[^A-Za-z0-9_-]", "", sid_raw)[:8] or "claude") if sid_raw else ""
    muted = bool(sid) and (state / "mute" / sid).exists()
    if (state / "quiet").exists() or (Path.home() / ".cc-voice-off").exists() \
       or dnd_active or muted:
        sys.exit(0)
    text = None
    if event == "Notification":
        text = data.get("message")
    elif event in ("Stop", "SubagentStop"):
        tp = data.get("transcript_path")
        if tp:
            text = last_assistant_text(tp)

    if text:
        snippet = pick_words(clean_for_speech(text))
        if snippet:
            repo = project_name(data)
            prefix = repo and SPEAK_REPO
            speak_detached(f"{repo}. {snippet}" if prefix else snippet, AZURE_VOICE)

    sys.exit(0)


if __name__ == "__main__":
    main()
