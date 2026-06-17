#!/usr/bin/env python3
"""
cc_tray.py — System-tray icon for CC--VH-lite (Windows/macOS).

Shows cc-notify's state in real time and lets you mute, enable DND, or restore
notifications — without opening a terminal.

── Requirements ──
    pip install pystray pillow

── Usage ──
    python cc_tray.py            # launch the tray and keep it in the background
    python cc_tray.py --help

── Icon states ──
    🟢 Green  = notifying (all active)
    🟡 Yellow = DND active with timer
    🔴 Red    = global silence (ccn quiet)
    ⚫ Gray   = cc-notify not installed / no sessions

── Claude Code integration ──
    No hooking required — it runs separately, persistent.
    On Windows: add it to Startup with install.py --tray-autostart.
"""

import sys
import time
import threading
import subprocess
from pathlib import Path

# The Windows console defaults to cp1252 and blows up on the emojis in the
# prints (--check-deps, error messages). UTF-8 so it doesn't crash.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import pystray
    from PIL import Image          # the isotype drawing lives in cc_theme
except ImportError:
    print(
        "ERROR: Tray dependencies missing.\n"
        "Install them with:  pip install pystray pillow\n"
        "Then re-run: python cc_tray.py"
    )
    sys.exit(1)

# ── State paths (same as cc_notify.py) ──
STATE   = Path.home() / ".cc-notify"
MUTE_DIR = STATE / "mute"
QUIET   = STATE / "quiet"
NOSOUND = STATE / "nosound"
DND     = STATE / "dnd"
SESS_DIR = STATE / "sessions"
VOICE_LOCK = STATE / "voice.lock"   # poison with a sentinel to cut the voice mid-flight

# Shared branding (palette + isotype drawn in code).
import cc_theme

SIZE          = 64          # icon px (pystray scales it)
REFRESH_SECS  = 15          # how often the state refreshes


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

def _dnd_remaining() -> float:
    """Seconds left of DND, or 0 if not active."""
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
    """Snapshot of cc-notify's current state."""
    quiet = QUIET.exists()
    dnd_rem = _dnd_remaining()
    nosound = NOSOUND.exists()
    sessions = list(SESS_DIR.glob("*.json"))

    if quiet or nosound:
        variant = "muted"
        label = "Silenced (global)" if quiet else "No sound"
        symbol = "🔇"
    elif dnd_rem > 0:
        variant = "dnd"
        label = f"DND {_fmt_rem(dnd_rem)} (until {_fmt_until(dnd_rem)})"
        symbol = "⏳"
    elif sessions:
        variant = "active"
        label = "Active — notifying"
        symbol = "🔔"
    else:
        variant = "idle"
        label = "No sessions"
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
# Icon: CC--VH-lite isotype per state (delegated to cc_theme)
# ─────────────────────────────────────────────────────────────────────────────

def make_icon(variant: str) -> Image.Image:
    """Tray isotype for the given state (active/dnd/muted/idle)."""
    return cc_theme.draw_logo(SIZE, variant=variant)


# ─────────────────────────────────────────────────────────────────────────────
# Menu actions
# ─────────────────────────────────────────────────────────────────────────────

def _write_dnd(minutes: int) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    exp = time.time() + minutes * 60
    DND.write_text(str(exp), encoding="utf-8")


def _cancel_dnd() -> None:
    DND.unlink(missing_ok=True)


def _shut_up() -> None:
    """Cut the voice playing right now — instantly, from the tray.

    Poisons the shared voice lock with a sentinel; every active player polls it
    every 200ms and self-stops the moment the owner PID stops matching (see
    cc_voice_lite._play_audio_file / _say_local). Cut in ≤200ms, no process
    kill. Best-effort; never raises.
    """
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        VOICE_LOCK.write_text("stop", encoding="utf-8")
    except OSError:
        pass


def _toggle_quiet() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if QUIET.exists():
        QUIET.unlink(missing_ok=True)
    else:
        QUIET.touch()
        _cancel_dnd()   # global quiet cancels DND
        _shut_up()      # silence silences both: cut whatever is playing now


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

    # ── Menu helpers ──

    def _on_dnd(self, minutes: int):
        def _do(icon, item):
            _cancel_dnd()
            if QUIET.exists():
                QUIET.unlink(missing_ok=True)
            _write_dnd(minutes)
            _shut_up()      # silence silences both: cut whatever is playing now
            self._refresh(icon)
        return _do

    def _on_shut_up(self, icon, item):
        """Cut the voice playing right now, without changing any silence state."""
        _shut_up()

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
        """Turn everything back on: cancel quiet and DND."""
        QUIET.unlink(missing_ok=True)
        _cancel_dnd()
        self._refresh(icon)

    def _on_config(self, icon, item):
        """Open the settings window as a SEPARATE process.

        The tray (pystray) and the window (Tkinter) each own a mainloop;
        running them in the same process fights over the loop. We launch
        cc_config_gui.py detached with pythonw (no console on Windows).
        """
        gui = Path(__file__).resolve().parent / "cc_config_gui.py"
        # On Windows use pythonw.exe (no console window) if it exists.
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

    # ── Menu construction ──

    def _build_menu(self, state: dict) -> pystray.Menu:
        quiet = state["quiet"]
        dnd_rem = state["dnd_rem"]
        nosound = state["nosound"]

        # Status item (not clickable, informational only)
        status_item = pystray.MenuItem(
            f"{state['symbol']}  {state['label']}",
            None,
            enabled=False,
        )

        separator = pystray.Menu.SEPARATOR

        # Silence actions
        if quiet or dnd_rem > 0:
            toggle_item = pystray.MenuItem("🔔  Resume notifications", self._on_activate)
        else:
            toggle_item = pystray.MenuItem("🔇  Global silence", self._on_quiet)

        # DND submenu
        dnd_submenu = pystray.Menu(
            pystray.MenuItem("30 minutes",  self._on_dnd(30)),
            pystray.MenuItem("1 hour",      self._on_dnd(60)),
            pystray.MenuItem("1.5 hours",   self._on_dnd(90)),
            pystray.MenuItem("2 hours",     self._on_dnd(120)),
            pystray.MenuItem("4 hours",     self._on_dnd(240)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Cancel DND" if dnd_rem > 0 else "DND inactive",
                self._on_dnd_off,
                enabled=dnd_rem > 0,
            ),
        )
        dnd_item = pystray.MenuItem("⏳  Do Not Disturb…", dnd_submenu)

        # Sound
        sound_label = "🔔  Enable sound" if nosound else "🤫  Mute sound"
        sound_item = pystray.MenuItem(sound_label, self._on_sound)

        # Shut up NOW — cut the voice playing this instant (no state change).
        shut_up_item = pystray.MenuItem("⏹️  Shut up now", self._on_shut_up)

        # Open settings window (default = double-click the icon)
        config_item = pystray.MenuItem("⚙️  Settings…", self._on_config, default=True)

        quit_item = pystray.MenuItem("✖  Quit tray", self._on_quit)

        return pystray.Menu(
            status_item,
            separator,
            shut_up_item,
            config_item,
            toggle_item,
            dnd_item,
            sound_item,
            separator,
            quit_item,
        )

    # ── Icon refresh ──

    def _refresh(self, icon: pystray.Icon | None = None) -> None:
        tgt = icon or self._icon
        if tgt is None:
            return
        state = get_state()
        tgt.icon = make_icon(state["variant"])
        tgt.title = f"cc-notify — {state['label']}"
        tgt.menu = self._build_menu(state)

    def _bg_refresh(self) -> None:
        """Refresh the icon every REFRESH_SECS seconds in the background."""
        while not self._stop_event.wait(REFRESH_SECS):
            self._refresh()

    # ── Entry point ──

    def run(self) -> None:
        state = get_state()
        icon = pystray.Icon(
            name="cc-notify",
            icon=make_icon(state["variant"]),
            title=f"cc-notify — {state['label']}",
            menu=self._build_menu(state),
        )
        self._icon = icon

        # Auto-refresh thread
        t = threading.Thread(target=self._bg_refresh, daemon=True)
        t.start()

        icon.run()


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="CC--VH-lite: cc-notify tray icon")
    ap.add_argument(
        "--check-deps",
        action="store_true",
        help="Just check that pystray and pillow are installed",
    )
    args = ap.parse_args()

    if args.check_deps:
        print("✅ pystray and pillow available.")
        return

    CCTray().run()


if __name__ == "__main__":
    main()
