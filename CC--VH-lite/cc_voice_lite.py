#!/usr/bin/env python3
"""
CC--VH-lite — voz ONYX para Claude Code (reconstruida 2026-06-03).

SOLO Azure OpenAI TTS, voz onyx. Sin Arbor (la voz culera que se colgaba
30s y no se podía detener) y sin servicio local. Habla un fragmento de lo
que dijo Claude Code, en segundo plano (detached), sin bloquear a Claude.

Respeta el silencio de cc-notify: si existe ~/.cc-notify/quiet o
~/.cc-voice-off, NO habla (el .py se lee fresco en cada hook, así que
`ccn quiet` la calla sin reiniciar Claude Code).

Eventos:
    - Stop / SubagentStop → última respuesta de Claude (del transcript .jsonl)
    - Notification        → el texto de la notificación (campo "message")

Config: ~/.secrets/azure-openai-key.txt (archivo de notas con la línea
    AZURE_OPENAI_TTS_KEY: <key>, más Endpoint/Deployment/API-Version).

Prueba manual:
    python3 cc_voice_lite.py --say "Probando la voz onyx"
"""

import os
import re
import sys
import json
import argparse
import subprocess
import tempfile
import urllib.request
from pathlib import Path

# ── Config Azure OpenAI TTS (onyx) ──
SECRET_FILE = Path.home() / ".secrets" / "azure-openai-key.txt"


def _cfg(name: str, default: str = "") -> str:
    """Lee 'name: valor' (o 'name=valor') del archivo de notas de secretos.

    Toma el primer token del valor (corta comentarios tipo '(verified ...)').
    Las variables de entorno tienen prioridad.
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
AZURE_VOICE = os.environ.get("AZURE_TTS_VOICE", "onyx")

# Fallback último recurso: voz local `say` de macOS (instantánea, gratis).
SAY_VOICE = os.environ.get("SAY_VOICE", "Paulina")

# ── Selección de fragmento ──
MIN_WORDS = 30
MAX_RATIO = 0.5
MAX_CHARS = 600
SENTENCE_END = ".!?…"


def speak_blocking(text: str, voice: str) -> None:
    """Genera el audio con Azure onyx y lo reproduce con afplay.

    Si Azure falla por lo que sea, cae al `say` de macOS para no quedar mudo.
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
                    subprocess.run(["afplay", mp3], check=False)
                finally:
                    try:
                        os.unlink(mp3)
                    except OSError:
                        pass
                return
        except Exception:
            pass  # cae al fallback
    # Fallback: voz local de macOS.
    try:
        subprocess.run(["say", "-v", SAY_VOICE, text], check=False)
    except Exception:
        pass


def speak_detached(text: str, voice: str) -> None:
    """Habla en segundo plano y NO bloquea a Claude Code.

    start_new_session=True desacopla el habla del proceso del hook, así
    Claude no espera a que termine el audio ni lo corta al salir.
    """
    subprocess.Popen(
        [sys.executable or "python3", str(Path(__file__).resolve()),
         "--speak-now", text, "--voice", voice],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def read_payload(cli_hook):
    """Lee el JSON de stdin. Devuelve (evento, data)."""
    data = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
    except Exception:
        data = {}
    return (cli_hook or data.get("hook_event_name")), data


def last_assistant_text(transcript_path: str):
    """Saca el texto de la última respuesta de Claude del transcript .jsonl.

    Recorre de atrás hacia adelante buscando type='assistant' y los bloques
    de message.content con type='text'.
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
    """Quita markdown y código pa' que la voz no lea símbolos raros."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links markdown
    text = re.sub(r"[*_`#>~|]", " ", text)                # símbolos markdown
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", " ", text)  # emojis
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def pick_words(text: str) -> str:
    """Al menos MIN_WORDS palabras, terminando la oración en curso.

    La oración manda: puede rebasar el mínimo. Si el texto es más corto que
    el mínimo, lo dice todo. MAX_RATIO es red de seguridad cuando no hay
    puntuación.
    """
    if not text:
        return ""
    words = text.split()
    total = len(words)
    if total <= MIN_WORDS:
        return text.strip(" \")’'»")[:MAX_CHARS]
    # Busca el primer fin de oración después del mínimo.
    cut = None
    for i in range(MIN_WORDS, total):
        if words[i] and words[i][-1] in SENTENCE_END:
            cut = i + 1
            break
    if cut is None:
        cut = min(total, max(MIN_WORDS, int(total * MAX_RATIO)))
    return " ".join(words[:cut]).strip(" \")’'»")[:MAX_CHARS]


def project_name(data: dict):
    """Nombre del repo/folder: basename del cwd que manda el hook."""
    cwd = data.get("cwd")
    return Path(cwd).name if cwd else None


def main() -> None:
    parser = argparse.ArgumentParser(description="CC--VH-lite: voz onyx pa' Claude Code")
    parser.add_argument("--hook", help="Forzar evento (Stop, Notification, ...)")
    parser.add_argument("--say", help="Habla este texto y sale (modo prueba)")
    parser.add_argument("--speak-now", help="(interno) proceso detached que habla")
    parser.add_argument("--voice", default=AZURE_VOICE)
    args = parser.parse_args()

    # Modo interno: el proceso detached que realmente habla.
    if args.speak_now is not None:
        try:
            speak_blocking(args.speak_now, args.voice)
        except Exception:
            pass
        return

    # Modo prueba manual.
    if args.say:
        speak_detached(args.say, args.voice)
        return

    # Interruptor de silencio: respeta el `quiet` de cc-notify (ccn quiet)
    # o el propio ~/.cc-voice-off. Calla sin reiniciar Claude Code.
    if (Path.home() / ".cc-notify" / "quiet").exists() or \
       (Path.home() / ".cc-voice-off").exists():
        sys.exit(0)

    event, data = read_payload(args.hook)
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
            speak_detached(f"{repo}. {snippet}" if repo else snippet, AZURE_VOICE)

    sys.exit(0)


if __name__ == "__main__":
    main()
