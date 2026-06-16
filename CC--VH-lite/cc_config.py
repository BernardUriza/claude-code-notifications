#!/usr/bin/env python3
"""
cc_config.py — Shared config for CC--VH-lite (banners + voice + tray + GUI).

A SINGLE config file at ~/.cc-notify/config.json, read by cc_notify.py,
cc_voice_lite.py, cc_tray.py, and cc_config_gui.py. Without this, the settings
(voice, minimum words, sound, default DND duration) were hardcoded and there was
no way to change them without editing the code.

Environment variables still take priority over config.json (for one-off
overrides), and config.json takes priority over the defaults.
"""

import os
import json
from pathlib import Path

STATE = Path.home() / ".cc-notify"
CONFIG_FILE = STATE / "config.json"

# Defaults — what used to be hardcoded lives here as the fallback.
DEFAULTS = {
    "sound": "Glass",          # banner sound name (macOS); "" = silent
    "voice": "onyx",           # Azure/edge TTS voice
    "min_words": 30,           # minimum words the voice reads
    "max_chars_speech": 600,   # voice character cap
    "max_chars_banner": 140,   # banner character cap
    "dnd_default_min": 60,     # default DND duration (minutes)
    "speak_repo_name": True,   # prepend the repo name before the spoken text
}


def load_raw() -> dict:
    """Read ONLY what's in config.json, without injecting defaults.

    Key to resolving priority: 'the key is NOT in the file' must be
    distinguishable from 'the key is present and equals the default value'. If
    `load()` (which merges defaults) were used for that decision, a default like
    voice='onyx' would always clobber the secrets file. Never raises.
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
    """Read config.json merged with the defaults. Never raises."""
    cfg = dict(DEFAULTS)
    cfg.update(load_raw())
    return cfg


def save(cfg: dict) -> None:
    """Write only the known keys to config.json, ATOMICALLY.

    Writes to a .tmp and does os.replace() (atomic rename on the same FS), so a
    mid-write crash CANNOT leave config.json truncated/corrupt: either the old
    one stays intact, or the new one is complete.
    """
    STATE.mkdir(parents=True, exist_ok=True)
    clean = {k: cfg[k] for k in DEFAULTS if k in cfg}
    payload = json.dumps(clean, indent=2, ensure_ascii=False) + "\n"
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)   # atomic


_INT_KEYS = ("min_words", "max_chars_speech", "max_chars_banner", "dnd_default_min")


def _coerce(key: str, val):
    """Cast a raw value to the key's expected type. None if it can't."""
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
    """Value of a key: env var (if given) > config.json (file) > default.

    Casts to the expected type and, if the raw value is garbage (e.g. config.json
    hand-edited with `"min_words": "forty"`), falls back to the default instead
    of crashing. Resolves priority with `load_raw()` (only what's present in the
    file) so a default doesn't clobber a lower-priority source.
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
