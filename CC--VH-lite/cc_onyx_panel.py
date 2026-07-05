#!/usr/bin/env python3
"""
CC--VH-lite — onyx panel daemon (added 2026-07-05).

OPTIONAL local web panel that lists every Claude response captured by
cc_onyx_capture.py, each with an in-browser audio player speaking in the onyx
voice. Exists because some terminals (Apple Terminal.app) make NO URL
clickable — the browser is the reliable clickable surface.

This is an optional enhancement, NOT the core path (Core Law: no daemon is
required for core behavior — banner and voice work without this). Run it via
the LaunchAgent (see README) or by hand:

    python3 cc_onyx_panel.py

Then open http://localhost:4949 and keep the tab around. New responses show
up within ~3s; audio is synthesized ONLY when you press play, then cached as
<id>.mp3 next to its text.

Synthesis chain (mirrors the repo's fallback philosophy):
  1. susurro gateway (Bernard's own STT/TTS provider) — SUSURRO_KEY in
     ~/.secrets/susurro-gateway-key.txt.
  2. Azure OpenAI TTS direct — same secrets file contract as cc_voice_lite.
  3. HTTP 502 (the panel shows the entry, playback just fails honestly).
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request
import http.server
from pathlib import Path

PORT = int(os.environ.get("CC_ONYX_PANEL_PORT", "4949"))
FEED = Path.home() / ".cc-notify" / "onyx-feed"
VOICE = os.environ.get("CC_ONYX_VOICE", "onyx")

SUSURRO_SECRETS = Path.home() / ".secrets" / "susurro-gateway-key.txt"
SUSURRO_URL = os.environ.get("SUSURRO_TTS_URL", "https://sus.bernarduriza.com/v1/tts")
AZURE_SECRETS = Path.home() / ".secrets" / "azure-openai-key.txt"

PANEL = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Onyx &mdash; respuestas de Claude</title>
<style>
body{font-family:system-ui;background:#111;color:#eee;max-width:760px;margin:2rem auto;padding:0 1rem}
h1{font-size:1.15rem;opacity:.85}
.card{background:#1c1c1e;border-radius:12px;padding:1rem;margin:.8rem 0}
.card p{margin:0 0 .6rem;line-height:1.45;white-space:pre-line}
.card small{opacity:.4}
.repo{display:inline-block;background:#2c2c2e;border-radius:6px;padding:.1rem .5rem;font-size:.75rem;margin-bottom:.5rem;opacity:.7}
audio{width:100%}
#empty{opacity:.4}
details summary{cursor:pointer;opacity:.7;font-size:.85rem;margin-bottom:.4rem}
</style></head><body>
<h1>&#128266; Onyx &mdash; respuestas de Claude</h1>
<p id="empty">esperando respuestas&hellip;</p>
<div id="list"></div>
<script>
const seen = new Set();
const list = document.getElementById('list');
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
async function poll(){
  try{
    const r = await fetch('/list'); const items = await r.json();
    if(items.length) document.getElementById('empty').style.display='none';
    for(const it of items.reverse()){
      if(seen.has(it.id)) continue; seen.add(it.id);
      const div = document.createElement('div'); div.className='card';
      div.innerHTML = (it.repo ? '<span class="repo">'+esc(it.repo)+'</span>' : '')
        + '<details><summary>'+esc(it.preview)+'</summary><p>'+esc(it.full)+'</p></details>'
        + '<audio controls preload="none" src="/mp3/'+it.id+'"></audio>'
        + '<small>'+esc(it.time)+'</small>';
      list.prepend(div);
    }
  }catch(e){}
}
setInterval(poll, 3000); poll();
</script></body></html>"""


def _secret(path, name):
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


def _synth_susurro(text):
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


def _synth_azure(text):
    key = _secret(AZURE_SECRETS, "AZURE_OPENAI_TTS_KEY") or _secret(AZURE_SECRETS, "AZURE_OPENAI_KEY")
    if not key:
        return None
    endpoint = _secret(AZURE_SECRETS, "Endpoint") or "https://northcentralus.api.cognitive.microsoft.com"
    deployment = _secret(AZURE_SECRETS, "Deployment") or "tts"
    api_version = _secret(AZURE_SECRETS, "API-Version") or "2024-02-15-preview"
    url = (f"{endpoint.rstrip('/')}/openai/deployments/{deployment}"
           f"/audio/speech?api-version={api_version}")
    body = json.dumps({
        "model": deployment, "input": text, "voice": VOICE, "response_format": "mp3",
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "api-key": key, "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read() or None


def mp3_for(entry_id):
    """Cached mp3 for a feed entry; synthesize lazily on first play."""
    mp3 = FEED / (entry_id + ".mp3")
    if mp3.is_file() and mp3.stat().st_size > 0:
        return mp3.read_bytes()
    txt = FEED / (entry_id + ".txt")
    if not txt.is_file():
        return None
    text = txt.read_text(encoding="utf-8")
    audio = None
    for synth in (_synth_susurro, _synth_azure):
        try:
            audio = synth(text)
        except Exception:
            audio = None
        if audio:
            break
    if audio:
        mp3.write_bytes(audio)
    return audio


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parts = urllib.parse.urlparse(self.path).path.strip("/").split("/", 1)
        route = parts[0] or "panel"

        if route in ("panel", "index.html"):
            body = PANEL.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        if route == "list":
            items = []
            if FEED.is_dir():
                files = sorted(FEED.glob("*.txt"),
                               key=lambda f: f.stat().st_mtime, reverse=True)[:50]
                for f in files:
                    text = f.read_text(encoding="utf-8")
                    meta = {}
                    meta_file = FEED / (f.stem + ".json")
                    if meta_file.is_file():
                        try:
                            meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        except Exception:
                            meta = {}
                    items.append({
                        "id": f.stem,
                        "preview": text[:160] + ("…" if len(text) > 160 else ""),
                        "full": text,
                        "repo": meta.get("repo", ""),
                        "time": meta.get("time") or time.strftime(
                            "%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                    })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(items).encode())
            return

        if route == "mp3" and len(parts) == 2 and re.fullmatch(r"[0-9a-f]{12}", parts[1]):
            audio = mp3_for(parts[1])
            if audio is None:
                self.send_response(502)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
            return

        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    FEED.mkdir(parents=True, exist_ok=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
