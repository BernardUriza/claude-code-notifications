#!/usr/bin/env python3
"""
cc_onyx_panel.py — Onyx response feed for CC--VH-lite (embeddable module).

Provides OnyxFeedFrame, the widget that lists every Claude response captured
by cc_onyx_capture.py (~/.cc-notify/onyx-feed/), newest first, each with a
play button that speaks it in the onyx voice. It is embedded as a tab of the
settings GUI (cc_config_gui.py) — ONE window, one UX, no separate app, no web
server, no localhost.

Audio is synthesized LAZILY — only when you press play — then cached as
<id>.mp3 next to its entry, so capturing everything costs nothing and
replaying is instant.

Synthesis chain (mirrors the repo's fallback philosophy):
  1. susurro gateway (SUSURRO_KEY in ~/.secrets/susurro-gateway-key.txt)
  2. Azure OpenAI TTS direct (same secrets contract as cc_voice_lite)
  3. nothing — the ✕ on the button reports the failure honestly

Run the UI:
    python3 cc_config_gui.py
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import tkinter as tk

import cc_theme as C

FEED = Path.home() / ".cc-notify" / "onyx-feed"
VOICE = os.environ.get("CC_ONYX_VOICE", "onyx")
POLL_MS = 3000
MAX_CARDS = 50
IS_WINDOWS = sys.platform.startswith("win")

SUSURRO_SECRETS = Path.home() / ".secrets" / "susurro-gateway-key.txt"
SUSURRO_URL = os.environ.get("SUSURRO_TTS_URL", "https://sus.bernarduriza.com/v1/tts")
AZURE_SECRETS = Path.home() / ".secrets" / "azure-openai-key.txt"


# ─────────────────────────────────────────────────────────────────────────────
# Synthesis (lazy, cached)
# ─────────────────────────────────────────────────────────────────────────────

def _secret(path: Path, name: str) -> str:
    """Read 'name: value' or 'name=value'; first token only (cuts comments)."""
    env = os.environ.get(name)
    if env:
        return env
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(rf"^\s*{re.escape(name)}\s*[:=]\s*(\S+)", line, re.I)
            if m:
                return m.group(1).strip()
    return ""


def _synth_susurro(text: str):
    key = _secret(SUSURRO_SECRETS, "SUSURRO_KEY")
    if not key:
        return None
    body = json.dumps({"input": text, "voice": VOICE, "format": "mp3"}).encode()
    req = urllib.request.Request(SUSURRO_URL, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read() or None


def _synth_azure(text: str):
    key = (_secret(AZURE_SECRETS, "AZURE_OPENAI_TTS_KEY")
           or _secret(AZURE_SECRETS, "AZURE_OPENAI_KEY"))
    if not key:
        return None
    endpoint = (_secret(AZURE_SECRETS, "Endpoint")
                or "https://northcentralus.api.cognitive.microsoft.com").rstrip("/")
    deployment = _secret(AZURE_SECRETS, "Deployment") or "tts"
    api_version = _secret(AZURE_SECRETS, "API-Version") or "2024-02-15-preview"
    url = (f"{endpoint}/openai/deployments/{deployment}"
           f"/audio/speech?api-version={api_version}")
    body = json.dumps({"model": deployment, "input": text,
                       "voice": VOICE, "response_format": "mp3"}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "api-key": key, "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read() or None


def mp3_for(entry_id: str):
    """Cached mp3 path for a feed entry; synthesize on first play. None = failed."""
    mp3 = FEED / (entry_id + ".mp3")
    if mp3.is_file() and mp3.stat().st_size > 0:
        return mp3
    txt = FEED / (entry_id + ".txt")
    if not txt.is_file():
        return None
    text = txt.read_text(encoding="utf-8")
    for synth in (_synth_susurro, _synth_azure):
        try:
            audio = synth(text)
        except Exception:
            audio = None
        if audio:
            mp3.write_bytes(audio)
            return mp3
    return None


class Player:
    """One playback at a time; playing something new stops the previous one."""

    def __init__(self):
        self.proc = None
        self.current = None

    def play(self, mp3: Path, entry_id: str):
        self.stop()
        if IS_WINDOWS:
            ps = ("Add-Type -AssemblyName presentationCore;"
                  "$p = New-Object System.Windows.Media.MediaPlayer;"
                  "$p.Open([uri]$env:CC_AUDIO_PATH); Start-Sleep -Milliseconds 300;"
                  "$p.Play();"
                  "while ($p.NaturalDuration.HasTimeSpan -eq $false) { Start-Sleep -Milliseconds 50 };"
                  "Start-Sleep -Seconds $p.NaturalDuration.TimeSpan.TotalSeconds")
            env = os.environ.copy()
            env["CC_AUDIO_PATH"] = str(mp3)
            self.proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                env=env, creationflags=0x08000000)
        else:
            self.proc = subprocess.Popen(["afplay", str(mp3)])
        self.current = entry_id

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
        self.current = None

    def playing(self, entry_id: str) -> bool:
        return (self.current == entry_id and self.proc is not None
                and self.proc.poll() is None)


def read_feed():
    """Feed entries, newest first: [{id, text, repo, time}]."""
    items = []
    if not FEED.is_dir():
        return items
    files = sorted(FEED.glob("*.txt"), key=lambda f: f.stat().st_mtime,
                   reverse=True)[:MAX_CARDS]
    for f in files:
        meta = {}
        meta_file = FEED / (f.stem + ".json")
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        items.append({
            "id": f.stem,
            "text": f.read_text(encoding="utf-8"),
            "repo": meta.get("repo", ""),
            "time": meta.get("time") or time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
        })
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Embeddable feed widget (plain tk — renders fine inside CTk parents too)
# ─────────────────────────────────────────────────────────────────────────────

class OnyxFeedFrame(tk.Frame):
    """Scrollable list of captured responses with per-card play/stop."""

    def __init__(self, parent, mono: str):
        super().__init__(parent, bg=C.BG)
        self.mono = mono
        self.player = Player()
        self.cards = {}
        self.busy = set()

        bar_row = tk.Frame(self, bg=C.BG)
        bar_row.pack(fill="x", padx=4, pady=(6, 2))
        self.status = tk.Label(bar_row, text="", bg=C.BG, fg=C.TEXT_DIM,
                               font=(mono, 10), anchor="e")
        self.status.pack(side="right")
        tk.Label(bar_row, text="respuestas capturadas · play = voz onyx",
                 bg=C.BG, fg=C.TEXT_DIM, font=(mono, 10),
                 anchor="w").pack(side="left")

        wrap = tk.Frame(self, bg=C.BG)
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg=C.BG, highlightthickness=0)
        bar = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.list = tk.Frame(self.canvas, bg=C.BG)
        self.list.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        win = self.canvas.create_window((0, 0), window=self.list, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            win, width=e.width))
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._wheel)

        self._poll()

    def _wheel(self, event):
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(-1 * int(event.delta), "units")

    def _set_status(self, text):
        try:
            self.status.configure(text=text)
        except tk.TclError:
            pass

    def _make_card(self, item):
        preview = item["text"][:380] + ("…" if len(item["text"]) > 380 else "")
        header = f"{item['repo']}  ·  {item['time']}" if item["repo"] else item["time"]

        card = tk.Frame(self.list, bg=C.BG_CARD,
                        highlightbackground=C.BORDER, highlightthickness=1)
        top = tk.Frame(card, bg=C.BG_CARD)
        top.pack(fill="x", padx=12, pady=(9, 2))
        tk.Label(top, text=header, bg=C.BG_CARD, fg=C.TEXT_DIM,
                 font=(self.mono, 10)).pack(side="left")
        btn = C.flat_button(top, "▶", lambda: None, kind="ghost",
                            font=(self.mono, 11, "bold"), padx=14, pady=2)
        btn.pack(side="right")
        body = tk.Label(card, text=preview, bg=C.BG_CARD, fg=C.TEXT,
                        justify="left", anchor="w", wraplength=520,
                        font=(self.mono, 11))
        body.pack(fill="x", padx=12, pady=(2, 10))
        card.bind("<Configure>", lambda e, b=body: b.configure(
            wraplength=max(200, e.width - 40)))

        btn.bind("<Button-1>",
                 lambda e, i=item["id"], b=btn: self._toggle(i, b))
        return card

    def _toggle(self, entry_id, btn):
        if self.player.playing(entry_id):
            self.player.stop()
            btn.configure(text="▶")
            self._set_status("")
            return
        if entry_id in self.busy:
            return
        self.busy.add(entry_id)
        btn.configure(text="…")
        self._set_status("sintetizando…")

        def work():
            mp3 = mp3_for(entry_id)

            def done():
                self.busy.discard(entry_id)
                if mp3 is None:
                    btn.configure(text="✕")
                    self._set_status("síntesis falló (¿susurro dormido? reintenta)")
                    return
                self.player.play(mp3, entry_id)
                btn.configure(text="■")
                self._set_status("")
                self._watch(entry_id, btn)

            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _watch(self, entry_id, btn):
        """Flip the button back to ▶ when playback ends on its own."""
        if self.player.playing(entry_id):
            self.after(300, lambda: self._watch(entry_id, btn))
        else:
            try:
                btn.configure(text="▶")
            except tk.TclError:
                pass

    def _poll(self):
        for item in reversed(read_feed()):
            if item["id"] in self.cards:
                continue
            card = self._make_card(item)
            existing = list(self.cards.values())
            if existing:
                card.pack(fill="x", pady=5, padx=6, before=existing[-1])
            else:
                card.pack(fill="x", pady=5, padx=6)
            self.cards[item["id"]] = card
        try:
            self.after(POLL_MS, self._poll)
        except tk.TclError:
            pass
