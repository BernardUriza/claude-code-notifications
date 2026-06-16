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
import subprocess
from pathlib import Path

# La consola de Windows usa cp1252 por defecto y truena con los emojis de los
# prints (--check-deps, mensajes de error). UTF-8 para no reventar.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import pystray
    from PIL import Image          # el dibujo del isotipo vive en cc_theme
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

# Branding compartido (paleta + isotipo dibujado en código).
import cc_theme

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

    if quiet or nosound:
        variant = "muted"
        label = "Silenciado (global)" if quiet else "Sin sonido"
        symbol = "🔇"
    elif dnd_rem > 0:
        variant = "dnd"
        label = f"DND {_fmt_rem(dnd_rem)} (hasta {_fmt_until(dnd_rem)})"
        symbol = "⏳"
    elif sessions:
        variant = "active"
        label = "Activo — notificando"
        symbol = "🔔"
    else:
        variant = "idle"
        label = "Sin sesiones"
        symbol = "○"

    return {
        "variant": variant,
        "color": cc_theme.STATE_COLORS[variant],
        "label": label,
        "symbol": symbol,
        "quiet": quiet,
        "dnd_rem": dnd_rem,
        "nosound": nosound,
        "sessions": len(sessions),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Ícono: isotipo CC--VH-lite por estado (delegado a cc_theme)
# ─────────────────────────────────────────────────────────────────────────────

def make_icon(variant: str) -> Image.Image:
    """Isotipo de la bandeja para el estado dado (active/dnd/muted/idle)."""
    return cc_theme.draw_logo(SIZE, variant=variant)


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

    def _on_config(self, icon, item):
        """Abre la ventana de configuración como proceso SEPARADO.

        El tray (pystray) y la ventana (Tkinter) tienen cada uno su propio
        mainloop; correrlos en el mismo proceso pelea por el loop. Lanzamos
        cc_config_gui.py detached con pythonw (sin consola en Windows).
        """
        gui = Path(__file__).resolve().parent / "cc_config_gui.py"
        # En Windows usamos pythonw.exe (sin ventana de consola) si existe.
        exe = sys.executable or "python"
        if sys.platform.startswith("win"):
            pw = exe.replace("python.exe", "pythonw.exe")
            if Path(pw).exists():
                exe = pw
        kwargs: dict = dict(stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen([exe, str(gui)], **kwargs)
        except OSError:
            pass

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

        # Abrir ventana de configuración (default = doble click en el ícono)
        config_item = pystray.MenuItem("⚙️  Configuración…", self._on_config, default=True)

        quit_item = pystray.MenuItem("✖  Cerrar tray", self._on_quit)

        return pystray.Menu(
            status_item,
            separator,
            config_item,
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
        tgt.icon = make_icon(state["variant"])
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
            icon=make_icon(state["variant"]),
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
