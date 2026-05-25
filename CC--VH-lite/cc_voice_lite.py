#!/usr/bin/env python3
"""
CC--VH-lite — Notificaciones por voz para Claude Code, versión lite.

Una sola dependencia: el comando `say` de macOS (ya viene instalado).
Sin daemon, sin cola SQLite, sin OpenAI, sin Qwen, sin web UI. Nomás habla.

Cómo lo llaman los hooks de Claude Code:
    El hook manda un JSON por stdin con el campo "hook_event_name".
    También aceptamos --hook <Evento> por si lo quieres probar a mano.

Uso manual (pruebas):
    echo '{"hook_event_name":"Stop"}' | python3 cc_voice_lite.py
    python3 cc_voice_lite.py --hook Stop
    python3 cc_voice_lite.py --say "Probando, uno dos tres"
"""

import sys
import json
import random
import argparse
import subprocess

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — edita aquí mismo, no hay config.json que valga
# ─────────────────────────────────────────────────────────────────────────────
VOICE = "Paulina"   # voz mexicana de macOS. Lista: `say -v '?'`
RATE = 190          # palabras por minuto (más alto = más rápido)

# Frases por evento. Se elige una al azar pa' que no aburra.
PHRASES = {
    "SessionStart": [
        "Arrancamos, mi Bernard",
        "Listo pa' chambear",
        "A darle, jefe",
    ],
    "Stop": [
        "Listo",
        "Ya quedó",
        "Terminé, compa",
        "Ahí está, jefe",
    ],
    "Notification": [
        "Oye, necesito tu visto bueno",
        "Échame una mano, necesito permiso",
        "Hey jefe, requiero tu aprobación",
    ],
}
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


def get_hook_event(cli_hook: str | None) -> str | None:
    """Saca el nombre del evento: prioridad al --hook, luego al JSON de stdin."""
    if cli_hook:
        return cli_hook
    if sys.stdin.isatty():
        return None
    try:
        raw = sys.stdin.read()
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data.get("hook_event_name")
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="CC--VH-lite: voz pa' Claude Code")
    parser.add_argument("--hook", help="Nombre del evento (SessionStart, Stop, Notification)")
    parser.add_argument("--say", help="Habla este texto y sale (modo prueba)")
    args = parser.parse_args()

    # Modo prueba: di lo que te pasen y listo
    if args.say:
        speak(args.say)
        return

    event = get_hook_event(args.hook)

    # Si el evento no está en nuestro mapa, salimos calladitos (exit 0).
    phrases = PHRASES.get(event) if event else None
    if phrases:
        speak(random.choice(phrases))

    # Siempre exit 0: un hook nunca debe trabar a Claude.
    sys.exit(0)


if __name__ == "__main__":
    main()
