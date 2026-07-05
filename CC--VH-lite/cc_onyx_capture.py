#!/usr/bin/env python3
"""
CC--VH-lite — onyx panel capture hook (added 2026-07-05).

Stop/SubagentStop hook that captures Claude's FULL last response as a text
entry in the onyx feed (~/.cc-notify/onyx-feed/). The companion daemon
cc_onyx_panel.py serves that feed as a local web page where each response has
a play button — audio is synthesized lazily (only when played), so capturing
every response costs nothing.

Core Law compliant: this hook only writes one small text file to disk (the
shared bus) and exits 0 immediately. No network, no audio, no blocking. The
panel daemon is a separate OPTIONAL piece; without it this file is inert.

Silence: create ~/.cc-notify/onyx-off to stop capturing (the capture itself
is soundless, so it deliberately ignores voice-quiet switches).

Transcript extraction and markdown cleanup are duplicated from
cc_voice_lite.py on purpose (Architecture Rules: keep scripts independent).

Manual test:
    echo '{"hook_event_name":"Stop","transcript_path":"/path/to/session.jsonl","cwd":"/repo"}' \
        | python3 cc_onyx_capture.py
"""

import hashlib
import json
import re
import sys
import time
from pathlib import Path

FEED = Path.home() / ".cc-notify" / "onyx-feed"
OFF_SWITCH = Path.home() / ".cc-notify" / "onyx-off"
MIN_CHARS = 60
MAX_ENTRIES = 200


def last_assistant_text(transcript_path):
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
            txt = "\n".join(p for p in parts if p).strip()
            if txt:
                return txt
    return None


def clean_for_speech(text):
    text = re.sub(r"```.*?```", " . código omitido . ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`#>~|]", " ", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def prune(feed):
    entries = sorted(feed.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in entries[MAX_ENTRIES:]:
        stem = old.stem
        for ext in (".txt", ".json", ".mp3"):
            (feed / (stem + ext)).unlink(missing_ok=True)


def main():
    if OFF_SWITCH.exists():
        sys.exit(0)
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        sys.exit(0)

    if data.get("hook_event_name") not in (None, "Stop", "SubagentStop"):
        sys.exit(0)
    tp = data.get("transcript_path")
    if not tp:
        sys.exit(0)

    text = last_assistant_text(tp)
    if not text:
        sys.exit(0)
    clean = clean_for_speech(text)
    if len(clean) < MIN_CHARS:
        sys.exit(0)

    entry_id = hashlib.sha1(clean.encode()).hexdigest()[:12]
    FEED.mkdir(parents=True, exist_ok=True)
    txt_file = FEED / (entry_id + ".txt")
    if not txt_file.exists():
        txt_file.write_text(clean, encoding="utf-8")
        cwd = data.get("cwd")
        meta = {
            "repo": Path(cwd).name if cwd else "",
            "time": time.strftime("%Y-%m-%d %H:%M"),
        }
        (FEED / (entry_id + ".json")).write_text(json.dumps(meta), encoding="utf-8")
        prune(FEED)

    sys.exit(0)


if __name__ == "__main__":
    main()
