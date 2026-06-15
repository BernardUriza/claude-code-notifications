#!/usr/bin/env python3
"""
cc_tray.py — Ícono en la bandeja del sistema para CC--VH-lite (Windows/macOS).

Muestra el estado de cc-notify en tiempo real y permite silenciar, activar DND,
o restablecer notificaciones — sin abrir una terminal.

── Requisitos ──
    pip install pystray pillow

── Uso ──
    python cc_tray.py            # lanza el tray y se queda en background
    python cc_tray.py --help

── Estados del ícono ──
    🟢 Verde   = notificando (todo activo)
    🟡 Amarillo = DND activo con timer
    🔴 Rojo    = silencio global (ccn quiet)
    ⚫ Gris    = cc-notify no instalado / sin sesiones

── Integración con claudecode ──
    Agrega en tu ~/.claude/settings.json (o deja que install.py lo haga):
    No requiere hookeo — corre aparte, persistente.
    En Windows: agrégalo al Startup con install.py --tray-autostart.
"""

import sys
import time
import threading
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print(
        "ERROR: Faltan dependencias del tray.\n"
        "Instálalas con:  pip install pystray pillow\n"
        "Luego vuelve a correr: python cc_tray.py"
    )
    sys.exit(1)

# ── Rutas de estado (mismas que cc_notify.py) ──
STATE   = Path.home() / ".cc-notify"
MUTE_DIR = STATE / "mute"
QUIET   = STATE / "quiet"
NOSOUND = STATE / "nosound"
DND     = STATE / "dnd"
SESS_DIR = STATE / "sessions"

# ── Colores del ícono ──
COLOR_ACTIVE  = "#22c55e"   # verde  — notificando
COLOR_DND     = "#eab308"   # amarillo — DND con timer
COLOR_QUIET   = "#ef4444"   # rojo   — silencio global
COLOR_IDLE    = "#6b7280"   # gris   — sin sesiones / apagado
BG            = "#1e1e1e"   # fondo del ícono (círculo oscuro)
SIZE          = 64          # px del ícono (pystray lo escala)

REFRESH_SECS  = 15          # con qué frecuencia se refresca el estado


# ─────────────────────────────────────────────────────────────────────────────
# Estado
# ─────────────────────────────────────────────────────────────────────────────

def _dnd_remaining() -> float:
    """Segundos restantes de DND, o 0 si no está activo."""
    if not DND.exists():
        return 0.0
    try:
        exp = float(DND.read_text(encoding="utf-8").strip())
        rem = exp - time.time()
        if rem > 0:
            return rem
        DND.unlink(missing_ok=True)
    except (OSError, ValueError):
        try:
            DND.unlink(missing_ok=True)
        except OSError:
            pass
    return 0.0


def _fmt_rem(secs: float) -> str:
    h, r = divmod(int(secs), 3600)
    m = r // 60
    if h:
        return f"{h}h {m:02d}min"
    return f"{m}min" if m >= 1 else "<1min"


def _fmt_until(secs: float) -> str:
    lt = time.localtime(time.time() + secs)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"


def get_state() -> dict:
    """Snapshot del estado actual de cc-notify."""
    quiet = QUIET.exists()
    dnd_rem = _dnd_remaining()
    nosound = NOSOUND.exists()
    sessions = list(SESS_DIR.glob("*.json"))

    if quiet:
        color = COLOR_QUIET
        label = "Silenciado (global)"
        symbol = "🔇"
    elif dnd_rem > 0:
        color = COLOR_DND
        label = f"DND {_fmt_rem(dnd_rem)} (hasta {_fmt_until(dnd_rem)})"
        symbol = "⏳"
    elif sessions:
        color = COLOR_ACTIVE
        label = "Activo — notificando"
        symbol = "🔔"
    else:
        color = COLOR_IDLE
        label = "Sin sesiones"
        symbol = "○"

    return {
        "color": color,
        "label": label,
        "symbol": symbol,
        "quiet": quiet,
        "dnd_rem": dnd_rem,
        "nosound": nosound,
        "sessions": len(sessions),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Ícono generado con Pillow
# ─────────────────────────────────────────────────────────────────────────────

def make_icon(color: str) -> Image.Image:
    """Genera un ícono cuadrado con un círculo del color dado."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = 4
    # Sombra sutil
    draw.ellipse([pad + 2, pad + 2, SIZE - pad + 2, SIZE - pad + 2],
                 fill=(0, 0, 0, 80))
    # Círculo principal
    draw.ellipse([pad, pad, SIZE - pad, SIZE - pad], fill=color)
    # Borde interior claro
    draw.ellipse([pad + 3, pad + 3, SIZE - pad - 3, SIZE - pad - 3],
                 outline=(255, 255, 255, 60), width=2)
    return img


# ─────────────────────────────────────────────────────────────────────────────
# Acciones del menú
# ─────────────────────────────────────────────────────────────────────────────

def _write_dnd(minutes: int) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    exp = time.time() + minutes * 60
    DND.write_text(str(exp), encoding="utf-8")


def _cancel_dnd() -> None:
    DND.unlink(missing_ok=True)


def _toggle_quiet() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if QUIET.exists():
        QUIET.unlink(missing_ok=True)
    else:
        QUIET.touch()
        _cancel_dnd()   # quiet global cancela DND


def _toggle_sound() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if NOSOUND.exists():
        NOSOUND.unlink(missing_ok=True)
    else:
        NOSOUND.touch()


# ─────────────────────────────────────────────────────────────────────────────
# Tray
# ─────────────────────────────────────────────────────────────────────────────

class CCTray:
    def __init__(self) -> None:
        self._icon: pystray.Icon | None = None
        self._stop_event = threading.Event()

    # ── Helpers de menú ──

    def _on_dnd(self, minutes: int):
        def _do(icon, item):
            _cancel_dnd()
            if QUIET.exists():
                QUIET.unlink(missing_ok=True)
            _write_dnd(minutes)
            self._refresh(icon)
        return _do

    def _on_dnd_off(self, icon, item):
        _cancel_dnd()
        self._refresh(icon)

    def _on_quiet(self, icon, item):
        _toggle_quiet()
        self._refresh(icon)

    def _on_sound(self, icon, item):
        _toggle_sound()
        self._refresh(icon)

    def _on_activate(self, icon, item):
        """Activa todo: cancela quiet y DND."""
        QUIET.unlink(missing_ok=True)
        _cancel_dnd()
        self._refresh(icon)

    def _on_quit(self, icon, item):
        self._stop_event.set()
        icon.stop()

    # ── Construcción del menú ──

    def _build_menu(self, state: dict) -> pystray.Menu:
        quiet = state["quiet"]
        dnd_rem = state["dnd_rem"]
        nosound = state["nosound"]

        # Ítem de estado (no clickeable, solo informativo)
        status_item = pystray.MenuItem(
            f"{state['symbol']}  {state['label']}",
            None,
            enabled=False,
        )

        separator = pystray.Menu.SEPARATOR

        # Acciones de silencio
        if quiet or dnd_rem > 0:
            toggle_item = pystray.MenuItem("🔔  Activar notificaciones", self._on_activate)
        else:
            toggle_item = pystray.MenuItem("🔇  Silencio global", self._on_quiet)

        # Submenú DND
        dnd_submenu = pystray.Menu(
            pystray.MenuItem("30 minutos",  self._on_dnd(30)),
            pystray.MenuItem("1 hora",      self._on_dnd(60)),
            pystray.MenuItem("1.5 horas",   self._on_dnd(90)),
            pystray.MenuItem("2 horas",     self._on_dnd(120)),
            pystray.MenuItem("4 horas",     self._on_dnd(240)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Cancelar DND" if dnd_rem > 0 else "DND inactivo",
                self._on_dnd_off,
                enabled=dnd_rem > 0,
            ),
        )
        dnd_item = pystray.MenuItem("⏳  No Molestar…", dnd_submenu)

        # Sonido
        sound_label = "🔔  Activar sonido" if nosound else "🤫  Silenciar sonido"
        sound_item = pystray.MenuItem(sound_label, self._on_sound)

        quit_item = pystray.MenuItem("✖  Cerrar tray", self._on_quit)

        return pystray.Menu(
            status_item,
            separator,
            toggle_item,
            dnd_item,
            sound_item,
            separator,
            quit_item,
        )

    # ── Actualización del ícono ──

    def _refresh(self, icon: pystray.Icon | None = None) -> None:
        tgt = icon or self._icon
        if tgt is None:
            return
        state = get_state()
        tgt.icon = make_icon(state["color"])
        tgt.title = f"cc-notify — {state['label']}"
        tgt.menu = self._build_menu(state)

    def _bg_refresh(self) -> None:
        """Refresca el ícono cada REFRESH_SECS segundos en background."""
        while not self._stop_event.wait(REFRESH_SECS):
            self._refresh()

    # ── Entrada principal ──

    def run(self) -> None:
        state = get_state()
        icon = pystray.Icon(
            name="cc-notify",
            icon=make_icon(state["color"]),
            title=f"cc-notify — {state['label']}",
            menu=self._build_menu(state),
        )
        self._icon = icon

        # Hilo de refresco automático
        t = threading.Thread(target=self._bg_refresh, daemon=True)
        t.start()

        icon.run()


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="CC--VH-lite: tray icon de cc-notify")
    ap.add_argument(
        "--check-deps",
        action="store_true",
        help="Solo verifica que pystray y pillow estén instalados",
    )
    args = ap.parse_args()

    if args.check_deps:
        print("✅ pystray y pillow disponibles.")
        return

    CCTray().run()


if __name__ == "__main__":
    main()
