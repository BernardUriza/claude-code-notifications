#!/usr/bin/env python3
"""
cc_config.py — Config compartida de CC--VH-lite (banners + voz + tray + GUI).

UN solo archivo de config en ~/.cc-notify/config.json, leído por cc_notify.py,
cc_voice_lite.py, cc_tray.py y cc_config_gui.py. Sin esto, los ajustes
(voz, mínimo de palabras, sonido, duración default del DND) estaban hardcodeados
y no había forma de cambiarlos sin editar el código.

Las variables de entorno siguen teniendo prioridad sobre el config.json (para
overrides puntuales), y el config.json tiene prioridad sobre los defaults.
"""

import os
import json
from pathlib import Path

STATE = Path.home() / ".cc-notify"
CONFIG_FILE = STATE / "config.json"

# Defaults — lo que estaba hardcodeado antes vive aquí como fallback.
DEFAULTS = {
    "sound": "Glass",          # nombre del sonido del banner (macOS); "" = mudo
    "voice": "onyx",           # voz Azure/edge TTS
    "min_words": 30,           # mínimo de palabras que lee la voz
    "max_chars_speech": 600,   # tope de caracteres de la voz
    "max_chars_banner": 140,   # tope de caracteres del banner
    "dnd_default_min": 60,     # duración default del DND (minutos)
    "speak_repo_name": True,   # anteponer nombre del repo antes del texto hablado
}


def load() -> dict:
    """Lee config.json mezclado con los defaults. Nunca truena."""
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save(cfg: dict) -> None:
    """Escribe solo las claves conocidas en config.json."""
    STATE.mkdir(parents=True, exist_ok=True)
    clean = {k: cfg[k] for k in DEFAULTS if k in cfg}
    CONFIG_FILE.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")


def get(key: str, env_var: str | None = None):
    """Valor de una clave: env var (si se da y existe) > config.json > default.

    `min_words`, `max_chars_*`, `dnd_default_min` se castean a int.
    """
    if env_var:
        ev = os.environ.get(env_var)
        if ev not in (None, ""):
            if key in ("min_words", "max_chars_speech", "max_chars_banner", "dnd_default_min"):
                try:
                    return int(ev)
                except ValueError:
                    pass
            elif key == "speak_repo_name":
                return ev.strip().lower() in ("1", "true", "yes", "on")
            else:
                return ev
    return load().get(key, DEFAULTS.get(key))
