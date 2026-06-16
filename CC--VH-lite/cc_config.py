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


def load_raw() -> dict:
    """Lee SOLO lo que está en config.json, sin inyectar defaults.

    Clave para resolver prioridad: 'la clave NO está en el archivo' tiene que
    distinguirse de 'la clave está y vale el mismo valor que el default'. Si
    `load()` (que mezcla defaults) se usara para esa decisión, un default como
    voice='onyx' pisaría siempre al secrets file. Nunca truena.
    """
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if k in DEFAULTS}
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def load() -> dict:
    """Lee config.json mezclado con los defaults. Nunca truena."""
    cfg = dict(DEFAULTS)
    cfg.update(load_raw())
    return cfg


def save(cfg: dict) -> None:
    """Escribe solo las claves conocidas en config.json, de forma ATÓMICA.

    Escribe a un .tmp y hace os.replace() (rename atómico en el mismo FS), así
    un crash a media escritura NO puede dejar config.json truncado/corrupto:
    o queda el viejo intacto, o el nuevo completo.
    """
    STATE.mkdir(parents=True, exist_ok=True)
    clean = {k: cfg[k] for k in DEFAULTS if k in cfg}
    payload = json.dumps(clean, indent=2, ensure_ascii=False) + "\n"
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)   # atómico


_INT_KEYS = ("min_words", "max_chars_speech", "max_chars_banner", "dnd_default_min")


def _coerce(key: str, val):
    """Castea un valor crudo al tipo esperado de la clave. None si no se puede."""
    if key in _INT_KEYS:
        try:
            return int(val)
        except (TypeError, ValueError):
            return None
    if key == "speak_repo_name":
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("1", "true", "yes", "on")
    return val


def get(key: str, env_var: str | None = None):
    """Valor de una clave: env var (si se da) > config.json (archivo) > default.

    Castea al tipo esperado y, si el valor crudo es basura (p. ej. config.json
    editado a mano con `"min_words": "cuarenta"`), cae al default en vez de
    tronar. Resuelve la prioridad con `load_raw()` (solo lo presente en el
    archivo) para que un default no pise una fuente de menor prioridad.
    """
    if env_var:
        ev = os.environ.get(env_var)
        if ev not in (None, ""):
            coerced = _coerce(key, ev)
            if coerced is not None:
                return coerced
    raw = load_raw()
    if key in raw:
        coerced = _coerce(key, raw[key])
        if coerced is not None:
            return coerced
    return DEFAULTS.get(key)
