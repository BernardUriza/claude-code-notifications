#!/usr/bin/env python3
"""
cc_onyx_panel.py — Onyx response panel for CC--VH-lite (native Python GUI).

Lists every Claude response captured by cc_onyx_capture.py
(~/.cc-notify/onyx-feed/), newest first, each with a Play button that speaks
it in the onyx voice. Audio is synthesized LAZILY — only when you press play —
then cached as <id>.mp3 next to its entry, so capturing everything costs
nothing and replaying is instant.

Same GUI DNA as cc_config_gui.py: CustomTkinter if installed, plain Tkinter
fallback (bundled with Python), cc_theme palette. No web server, no browser,
no localhost — a window (Core Law: nothing optional becomes required; this
panel is an on-demand app, not a daemon).

Synthesis chain (mirrors the repo's fallback philosophy):
  1. susurro gateway (SUSURRO_KEY in ~/.secrets/susurro-gateway-key.txt)
  2. Azure OpenAI TTS direct (same secrets contract as cc_voice_lite)
  3. local `say`/SAPI voice as last resort

Usage:
    python3 cc_onyx_panel.py
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

import cc_theme

USING_CTK = False
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    USING_CTK = True
except ImportError:
    import tkinter as ctk          # type: ignore

import tkinter as tk

FEED = Path.home() / ".cc-notify" / "onyx-feed"
VOICE = os.environ.get("CC_ONYX_VOICE", "onyx")
POLL_MS = 3000
MAX_CARDS = 50
IS_WINDOWS = sys.platform.startswith("win")

SUSURRO_SECRETS = Path.home() / ".secrets" / "susurro-gateway-key.txt"
SUSURRO_URL = os.environ.get("SUSURRO_TTS_URL", "https://sus.bernarduriza.com/v1/tts")
AZURE_SECRETS = Path.home() / ".secrets" / "azure-openai-key.txt"

C = cc_theme


# ─────────────────────────────────────────────────────────────────────────────
# Synthesis (lazy, cached) — no GUI code below this banner
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


# ─────────────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────────────

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


class OnyxPanel:
    def __init__(self):
        self.player = Player()
        self.cards = {}          # entry_id -> card frame (insertion tracking)
        self.busy = set()        # entry_ids currently synthesizing

        if USING_CTK:
            self.root = ctk.CTk()
            self.root.configure(fg_color=C.BG)
        else:
            self.root = tk.Tk()
            self.root.configure(bg=C.BG)
        self.root.title("Onyx — respuestas de Claude")
        self.root.geometry("680x760")
        self.root.minsize(480, 400)

        self._build_header()
        self._build_list()
        self._poll()
        self.root.mainloop()

    # ── layout ──
    def _build_header(self):
        if USING_CTK:
            head = ctk.CTkFrame(self.root, fg_color="transparent")
            head.pack(fill="x", padx=16, pady=(14, 6))
            ctk.CTkLabel(head, text="🔊 Onyx — respuestas de Claude",
                         text_color=C.TEXT,
                         font=(C.MONO_STACK[0], 17, "bold")).pack(side="left")
            self.status = ctk.CTkLabel(head, text="", text_color=C.TEXT_DIM,
                                       font=(C.MONO_STACK[0], 12))
            self.status.pack(side="right")
        else:
            head = tk.Frame(self.root, bg=C.BG)
            head.pack(fill="x", padx=16, pady=(14, 6))
            tk.Label(head, text="🔊 Onyx — respuestas de Claude", bg=C.BG,
                     fg=C.TEXT, font=(C.MONO_STACK[0], 15, "bold")).pack(side="left")
            self.status = tk.Label(head, text="", bg=C.BG, fg=C.TEXT_DIM)
            self.status.pack(side="right")

    def _build_list(self):
        if USING_CTK:
            self.list_frame = ctk.CTkScrollableFrame(
                self.root, fg_color=C.BG, scrollbar_button_color=C.BORDER)
            self.list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        else:
            wrap = tk.Frame(self.root, bg=C.BG)
            wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            canvas = tk.Canvas(wrap, bg=C.BG, highlightthickness=0)
            bar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
            self.list_frame = tk.Frame(canvas, bg=C.BG)
            self.list_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            win = canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
            canvas.bind("<Configure>",
                        lambda e: canvas.itemconfigure(win, width=e.width))
            canvas.configure(yscrollcommand=bar.set)
            canvas.pack(side="left", fill="both", expand=True)
            bar.pack(side="right", fill="y")

    def _make_card(self, item):
        preview = item["text"][:400] + ("…" if len(item["text"]) > 400 else "")
        header = f"{item['repo']}  ·  {item['time']}" if item["repo"] else item["time"]

        if USING_CTK:
            card = ctk.CTkFrame(self.list_frame, fg_color=C.BG_CARD,
                                corner_radius=10)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(10, 2))
            ctk.CTkLabel(top, text=header, text_color=C.TEXT_DIM,
                         font=(C.MONO_STACK[0], 11)).pack(side="left")
            btn = ctk.CTkButton(top, text="▶", width=44,
                                fg_color=C.ACCENT_LO, hover_color=C.ACCENT,
                                font=(C.MONO_STACK[0], 13, "bold"))
            btn.pack(side="right")
            ctk.CTkLabel(card, text=preview, text_color=C.TEXT, justify="left",
                         wraplength=560, font=(C.MONO_STACK[0], 12)
                         ).pack(fill="x", padx=12, pady=(0, 10))
        else:
            card = tk.Frame(self.list_frame, bg=C.BG_CARD,
                            highlightbackground=C.BORDER, highlightthickness=1)
            top = tk.Frame(card, bg=C.BG_CARD)
            top.pack(fill="x", padx=10, pady=(8, 2))
            tk.Label(top, text=header, bg=C.BG_CARD, fg=C.TEXT_DIM,
                     font=(C.MONO_STACK[0], 10)).pack(side="left")
            btn = tk.Button(top, text="▶", width=3, bg=C.BG_INPUT, fg=C.ACCENT,
                            activebackground=C.ACCENT_LO, relief="flat")
            btn.pack(side="right")
            tk.Label(card, text=preview, bg=C.BG_CARD, fg=C.TEXT,
                     justify="left", wraplength=560, anchor="w",
                     font=(C.MONO_STACK[0], 11)).pack(fill="x", padx=10,
                                                      pady=(0, 8))

        btn.configure(command=lambda i=item["id"], b=btn: self._toggle(i, b))
        return card

    # ── behavior ──
    def _set_status(self, text):
        try:
            self.status.configure(text=text)
        except Exception:
            pass

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
            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _watch(self, entry_id, btn):
        """Flip the button back to ▶ when playback ends on its own."""
        if self.player.playing(entry_id):
            self.root.after(300, lambda: self._watch(entry_id, btn))
        else:
            try:
                btn.configure(text="▶")
            except Exception:
                pass

    def _poll(self):
        for item in reversed(read_feed()):
            if item["id"] in self.cards:
                continue
            card = self._make_card(item)
            existing = [w for w in self.cards.values()]
            card.pack(fill="x", pady=5, padx=4,
                      before=existing[-1] if existing else None)
            self.cards[item["id"]] = card
        self.root.after(POLL_MS, self._poll)


if __name__ == "__main__":
    OnyxPanel()
