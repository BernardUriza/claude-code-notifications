#!/usr/bin/env python3
"""
CC--VH-lite — Notificaciones por voz para Claude Code, versión lite.

Una sola dependencia: el comando `say` de macOS (ya viene instalado).
Sin daemon, sin cola SQLite, sin OpenAI, sin Qwen, sin web UI. Nomás habla.

Qué dice: el nombre del repo/folder + un fragmento de lo que dijo Claude Code
(mínimo 30 palabras, o hasta el 50% del total si la respuesta es larga).
    - Stop / SubagentStop → última respuesta de Claude (la lee del transcript)
    - Notification        → el texto de la notificación (campo "message")

Cómo lo llaman los hooks de Claude Code:
    El hook manda un JSON por stdin con "hook_event_name" y "transcript_path".

Uso manual (pruebas):
    echo '{"hook_event_name":"Stop","transcript_path":"/ruta/al.jsonl"}' | python3 cc_voice_lite.py
    python3 cc_voice_lite.py --say "Probando, uno dos tres"
"""

import re
import sys
import json
import argparse
import subprocess
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — edita aquí mismo, no hay config.json que valga
# ─────────────────────────────────────────────────────────────────────────────
VOICE = "Paulina"   # voz mexicana de macOS. Lista: `say -v '?'`
RATE = 190          # palabras por minuto (más alto = más rápido)
MIN_WORDS = 30      # nunca menos de esto (salvo que la respuesta sea más corta)
MAX_RATIO = 0.5     # respuestas largas: hasta este % de las palabras totales
# ─────────────────────────────────────────────────────────────────────────────


def speak(text: str) -> None:
    """Habla en segundo plano y NO bloquea a Claude Code.

    start_new_session=True desacopla el `say` del proceso del hook,
    así Claude no espera a que termine el audio ni lo corta al salir.
    """
    subprocess.Popen(
        ["say", "-v", VOICE, "-r", str(RATE), text],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def read_payload(cli_hook: str | None) -> tuple[str | None, dict]:
    """Lee el JSON de stdin. Devuelve (evento, data)."""
    data: dict = {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    data = parsed
        except (json.JSONDecodeError, ValueError):
            pass
    event = cli_hook or data.get("hook_event_name")
    return event, data


def last_assistant_text(transcript_path: str) -> str | None:
    """Saca el texto de la última respuesta de Claude del transcript .jsonl.

    El transcript es JSONL: cada línea un objeto. Las respuestas de Claude son
    entries con type='assistant' y message.content = lista de bloques; los
    bloques de texto tienen type='text'. Recorremos de atrás hacia adelante.
    """
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
        msg = entry.get("message", {})
        content = msg.get("content")
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


def clean_for_speech(text: str) -> str:
    """Quita markdown y código pa' que `say` no lea símbolos raros."""
    if "```" in text:                                  # corta en bloque de código
        text = text.split("```", 1)[0]
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [txt](url) -> txt
    text = re.sub(r"[*_`#>~|]", "", text)               # símbolos markdown
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)  # emojis
    return " ".join(text.split())                       # colapsa espacios


SENTENCE_END = ".!?…"  # signos que cierran una oración


def pick_words(text: str) -> str:
    """Lee al menos MIN_WORDS palabras y termina la oración en curso (corta en el
    primer fin de oración después del mínimo). La oración manda: puede rebasar el
    tope. Si la respuesta es más corta que el mínimo, la dice toda. MAX_RATIO solo
    actúa de red de seguridad cuando el texto no tiene puntuación."""
    words = text.split()
    total = len(words)
    if total <= MIN_WORDS:
        return " ".join(words)

    # Desde el mínimo, busca la primera palabra que cierre oración (sin tope).
    for i in range(MIN_WORDS - 1, total):
        w = words[i].rstrip('")’\'»')   # ignora comillas/paréntesis de cierre
        if w and w[-1] in SENTENCE_END:
            return " ".join(words[: i + 1])

    # Texto sin puntuación: red de seguridad al MAX_RATIO del total.
    safety = min(total, max(MIN_WORDS, int(total * MAX_RATIO)))
    return " ".join(words[:safety])


def project_name(data: dict) -> str | None:
    """Nombre del repo/folder de la sesión: basename del `cwd` que manda el hook."""
    cwd = data.get("cwd")
    if cwd:
        name = Path(cwd).name
        if name:
            return name
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="CC--VH-lite: voz pa' Claude Code")
    parser.add_argument("--hook", help="Forzar evento (Stop, Notification, ...)")
    parser.add_argument("--say", help="Habla este texto y sale (modo prueba)")
    args = parser.parse_args()

    # Modo prueba: di lo que te pasen y listo
    if args.say:
        speak(args.say)
        return

    event, data = read_payload(args.hook)

    # ¿De dónde sacamos "lo que dice Claude"?
    text: str | None = None
    if event == "Notification":
        text = data.get("message")
    elif event in ("Stop", "SubagentStop"):
        tp = data.get("transcript_path")
        if tp:
            text = last_assistant_text(tp)

    if text:
        snippet = pick_words(clean_for_speech(text))
        if snippet:
            # Primero el repo/folder, luego el texto del hook.
            repo = project_name(data)
            speak(f"{repo}. {snippet}" if repo else snippet)

    # Siempre exit 0: un hook nunca debe trabar a Claude.
    sys.exit(0)


if __name__ == "__main__":
    main()
