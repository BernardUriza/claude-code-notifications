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
import shutil
import argparse
import subprocess
import tempfile
import urllib.request
from pathlib import Path

# ── Config Azure OpenAI TTS (onyx) ──
SECRET_FILE = Path.home() / ".secrets" / "azure-openai-key.txt"

IS_WINDOWS = sys.platform.startswith("win")

# Fallback local de Windows (SAPI vía System.Speech). Voz vacía = la default del
# sistema. Lista las instaladas con:
#   powershell "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices().VoiceInfo.Name"
WIN_SAY_VOICE = os.environ.get("WIN_SAY_VOICE", "")


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

# Config compartida (~/.cc-notify/config.json): voz, min_words, etc.
# Usamos load_raw() (SOLO lo presente en el archivo) para no dejar que un
# default del config pise una fuente de menor prioridad (p. ej. `Voice:` del
# secrets file). Si cc_config no importa, _RAW vacío → todo cae a defaults.
try:
    import cc_config
    _RAW = cc_config.load_raw()
except Exception:
    cc_config = None
    _RAW = {}


def _conf(key, default):
    """Valor del config.json (archivo), casteado; default si ausente/basura."""
    if cc_config is not None and key in _RAW:
        c = cc_config._coerce(key, _RAW[key])
        if c is not None:
            return c
    return default


# Voz OpenAI (alloy, ash, ballad, coral, echo, fable, onyx, nova, sage, shimmer).
# Prioridad REAL: env AZURE_TTS_VOICE > config.json `voice` > secrets `Voice:` > onyx.
# `_conf("voice", None)` devuelve None si la clave NO está en el archivo, así
# `or` cae al secrets file en vez de pisarlo con un default.
AZURE_VOICE = (_cfg("AZURE_TTS_VOICE") or _conf("voice", None)
               or _cfg("Voice") or "onyx")

# ── edge-tts: voz neural GRATIS, sin API key (Microsoft Edge TTS) ──
# Camino intermedio entre Azure (de pago, requiere key) y la voz local robótica.
# Instálalo con `pip install edge-tts`. Lista voces con `edge-tts --list-voices`.
EDGE_VOICE = os.environ.get("EDGE_TTS_VOICE", "es-MX-DaliaNeural")

# Fallback último recurso: voz local `say` de macOS (instantánea, gratis).
SAY_VOICE = os.environ.get("SAY_VOICE", "Paulina")

# ── Selección de fragmento (configurable vía config.json) ──
# _conf ya castea y cae a default si el valor es basura → un config.json
# editado a mano con tipos inválidos NO truena el script en import-time.
MIN_WORDS = _conf("min_words", 30)
MAX_RATIO = 0.5
MAX_CHARS = _conf("max_chars_speech", 600)
SPEAK_REPO = _conf("speak_repo_name", True)
SENTENCE_END = ".!?…"


def _play_audio_file(path: str) -> None:
    """Reproduce un archivo de audio de forma bloqueante, según la plataforma."""
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
            env=env, check=False,
        )
    else:
        subprocess.run(["afplay", path], check=False)


def _say_local(text: str) -> None:
    """Voz local (último recurso): SAPI en Windows, `say` en macOS."""
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
                env=env, check=False,
            )
        else:
            subprocess.run(["say", "-v", SAY_VOICE, text], check=False)
    except Exception:
        pass


def _edge_tts_to_file(text: str, out_path: str) -> bool:
    """Genera mp3 con edge-tts (neural, gratis, sin key). True si lo logró.

    Requiere `pip install edge-tts` (no viene en stdlib). El texto va por arg de
    subprocess (lista, sin shell) así que no hay problemas de escaping.
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
    """Genera el audio y lo reproduce (afplay en macOS, MediaPlayer en Windows).

    Cadena de fallback, de mejor a más simple:
      1. Azure OpenAI TTS onyx (si hay key en ~/.secrets).
      2. edge-tts (neural, gratis, sin key — si está instalado).
      3. Voz local offline (SAPI en Windows, `say` en macOS).
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
            pass  # cae al siguiente nivel
    # 2º: edge-tts (neural, gratis, sin key) si está instalado.
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
        pass  # cae al fallback local
    # 3º: voz local offline (SAPI en Windows, `say` en macOS).
    _say_local(text)


def speak_detached(text: str, voice: str) -> None:
    """Habla en segundo plano y NO bloquea a Claude Code.

    Re-invoca este script con --speak-now en un proceso desacoplado, así Claude
    no espera a que termine el audio ni lo corta al salir:
      - macOS:   start_new_session=True (POSIX).
      - Windows: DETACHED_PROCESS, sin ventana de consola.
    El proceso hijo corre speak_blocking (Azure onyx + reproducción/fallback
    según plataforma).
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
            prefix = repo and SPEAK_REPO
            speak_detached(f"{repo}. {snippet}" if prefix else snippet, AZURE_VOICE)

    sys.exit(0)


if __name__ == "__main__":
    main()
