#!/usr/bin/env python3
"""
install.py — Instalador de un comando para CC--VH-lite (banners + voz).

Hace todo el trabajo manual que antes pedía el README:
  - Detecta tu plataforma (macOS / Windows / Linux).
  - Detecta la ruta de ESTE clon y el intérprete de Python correcto
    (`python3` en macOS/Linux, `python` en Windows — el que exista en PATH).
  - FUSIONA los hooks Stop + Notification en tu ~/.claude/settings.json SIN
    pisar lo que ya tengas (hace backup antes de escribir). Idempotente: puedes
    correrlo mil veces, no duplica.
  - Chequea las dependencias opcionales y te dice qué falta.

Uso:
    python install.py            # instala / actualiza los hooks
    python install.py --dry-run  # muestra lo que haría, sin escribir nada
    python install.py --uninstall  # quita SOLO los hooks de este repo

Claude Code corre los hooks vía shell POSIX (Git Bash en Windows), por eso las
rutas se escriben con forward slashes (`D:/...`), que funcionan en ambos.
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
from pathlib import Path

# La consola de Windows usa cp1252 por defecto y truena con emojis/box-drawing.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

HERE = Path(__file__).resolve().parent          # .../CC--VH-lite
NOTIFY = HERE / "cc_notify.py"
VOICE  = HERE / "cc_voice_lite.py"
TRAY   = HERE / "cc_tray.py"
SETTINGS = Path.home() / ".claude" / "settings.json"
EVENTS = ("Stop", "Notification")
# Marcas para reconocer NUESTROS hooks (de cualquier ruta) y poder re-instalar
# o desinstalar sin tocar los hooks de otros.
MARKERS = ("cc_notify.py", "cc_voice_lite.py")


def pick_interpreter() -> str:
    """Nombre del intérprete a poner en el hook: el que exista en PATH."""
    order = ("python", "python3") if IS_WINDOWS else ("python3", "python")
    for cand in order:
        if shutil.which(cand):
            return cand
    return order[0]


def hook_command(interp: str, script: Path) -> str:
    """Comando del hook con ruta POSIX (sirve en Git Bash y en sh)."""
    p = script.as_posix()
    if " " in p:
        p = f'"{p}"'
    return f"{interp} {p}"


def build_block(interp: str) -> dict:
    """El bloque {event: [ {hooks:[...]} ]} con nuestros dos scripts."""
    entry = {
        "hooks": [
            {"type": "command", "command": hook_command(interp, NOTIFY)},
            {"type": "command", "command": hook_command(interp, VOICE)},
        ]
    }
    return {ev: [entry] for ev in EVENTS}


def is_ours(entry: dict) -> bool:
    """True si esta entrada de hook apunta a nuestros scripts (cualquier ruta)."""
    for h in entry.get("hooks", []):
        cmd = h.get("command", "")
        if any(m in cmd for m in MARKERS):
            return True
    return False


def load_settings() -> dict:
    if not SETTINGS.exists():
        return {}
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"⚠️  No pude leer {SETTINGS}: {e}")
        print("    Aborto para no corromper tu config. Arréglalo y reintenta.")
        sys.exit(1)


def merge(settings: dict, block: dict) -> dict:
    """Inserta nuestros hooks quitando primero CUALQUIER versión previa nuestra
    (idempotente + maneja re-clones a otra ruta). Respeta hooks ajenos."""
    hooks = settings.setdefault("hooks", {})
    for ev, entries in block.items():
        existing = hooks.get(ev, [])
        # quita nuestras entradas viejas, conserva las de otros
        kept = [e for e in existing if not is_ours(e)]
        hooks[ev] = kept + entries
    return settings


def remove_ours(settings: dict) -> dict:
    hooks = settings.get("hooks", {})
    for ev in list(hooks.keys()):
        hooks[ev] = [e for e in hooks[ev] if not is_ours(e)]
        if not hooks[ev]:
            del hooks[ev]
    if "hooks" in settings and not settings["hooks"]:
        del settings["hooks"]
    return settings


def write_settings(settings: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        backup = SETTINGS.with_suffix(".json.ccvh-bak")
        shutil.copy2(SETTINGS, backup)
        print(f"📦 Backup: {backup}")
    SETTINGS.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"✅ Escrito: {SETTINGS}")


def _has_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def check_deps() -> None:
    print("\n── Dependencias ──")
    ok, opt = "✅", "·"

    azure = (Path.home() / ".secrets" / "azure-openai-key.txt").exists()
    print(f"  {ok if azure else opt} Azure TTS key (~/.secrets/azure-openai-key.txt) "
          f"{'sí' if azure else 'no — voz de pago, opcional'}")

    edge = shutil.which("edge-tts") is not None
    print(f"  {ok if edge else opt} edge-tts (voz neural GRATIS) "
          f"{'sí' if edge else 'no — instala con: pip install edge-tts'}")

    # Tray icon
    has_pystray = _has_module("pystray")
    has_pillow  = _has_module("PIL")
    tray_ok = has_pystray and has_pillow
    if tray_ok:
        print(f"  {ok} pystray + pillow — tray icon disponible (python cc_tray.py)")
    else:
        missing = []
        if not has_pystray:
            missing.append("pystray")
        if not has_pillow:
            missing.append("pillow")
        print(f"  {opt} tray icon (opcional) — falta: pip install {' '.join(missing)}")

    # Ventana de config (cross-platform). Tkinter viene incluido → siempre corre.
    has_tk  = _has_module("tkinter")
    has_ctk = _has_module("customtkinter")
    if has_ctk:
        print(f"  {ok} ventana de config (CustomTkinter, look moderno) — python cc_config_gui.py")
    elif has_tk:
        print(f"  {ok} ventana de config (Tkinter incluido) — python cc_config_gui.py · look bonito: pip install customtkinter")
    else:
        print(f"  {opt} ventana de config — tkinter ausente (raro); CustomTkinter: pip install customtkinter")

    if IS_WINDOWS:
        ps = shutil.which("powershell") is not None
        print(f"  {ok if ps else '❌'} powershell {'sí' if ps else 'NO — requerido para banners y voz'}")
        bt = False
        if ps:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "if (Get-Module -ListAvailable -Name BurntToast) { 'yes' } else { 'no' }"],
                    capture_output=True, text=True, timeout=15)
                bt = r.stdout.strip() == "yes"
            except Exception:
                pass
        print(f"  {ok if bt else opt} BurntToast {'sí' if bt else 'no — banners caen a WinRT (cero deps); para la rama bonita: Install-Module BurntToast'}")
    elif IS_MAC:
        tn = shutil.which("terminal-notifier") is not None
        hs = shutil.which("hs") is not None
        print(f"  {ok if tn else opt} terminal-notifier {'sí' if tn else 'no — opcional (osascript de fallback)'}")
        print(f"  {ok if hs else opt} Hammerspoon (hs) {'sí' if hs else 'no — opcional (banner con botón)'}")
    else:
        print(f"  {opt} Linux: cc_notify usa rutas macOS; los banners no aplican. La voz sí (edge-tts/say-equiv).")


def _tray_autostart_path() -> Path:
    """Carpeta Startup de Windows (corre al iniciar sesión)."""
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def tray_autostart_install(interp: str) -> None:
    """Crea un .vbs en Startup para lanzar cc_tray.py sin ventana de consola."""
    if not IS_WINDOWS:
        print("ℹ️  Autostart de tray solo aplica en Windows.")
        return
    startup = _tray_autostart_path()
    vbs = startup / "cc_tray.vbs"
    script = TRAY.as_posix()
    # wscript.exe corre el .py con CreateObject("WScript.Shell").Run sin ventana
    vbs_content = (
        'Set WShell = CreateObject("WScript.Shell")\r\n'
        f'WShell.Run "{interp} {script}", 0, False\r\n'
    )
    vbs.write_text(vbs_content, encoding="utf-8")
    print(f"✅ Autostart creado: {vbs}")
    print("   El tray icon arrancará automáticamente al iniciar Windows.")
    print("   Para quitarlo: python install.py --tray-autostart-remove")


def tray_autostart_remove() -> None:
    if not IS_WINDOWS:
        return
    vbs = _tray_autostart_path() / "cc_tray.vbs"
    if vbs.exists():
        vbs.unlink()
        print(f"🗑️  Autostart eliminado: {vbs}")
    else:
        print("No había autostart de tray instalado.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Instalador de CC--VH-lite")
    ap.add_argument("--dry-run", action="store_true", help="muestra sin escribir")
    ap.add_argument("--uninstall", action="store_true", help="quita solo nuestros hooks")
    ap.add_argument("--tray-autostart", action="store_true",
                    help="(Windows) instala cc_tray.py en el Startup")
    ap.add_argument("--tray-autostart-remove", action="store_true",
                    help="(Windows) quita el autostart del tray")
    args = ap.parse_args()

    if not NOTIFY.exists() or not VOICE.exists():
        print(f"❌ No encuentro los scripts en {HERE}. ¿Corres esto desde el clon?")
        sys.exit(1)

    interp = pick_interpreter()

    if args.tray_autostart:
        tray_autostart_install(interp)
        return

    if args.tray_autostart_remove:
        tray_autostart_remove()
        return

    settings = load_settings()

    if args.uninstall:
        settings = remove_ours(settings)
        print("🗑️  Quitando hooks de CC--VH-lite.")
        if args.dry_run:
            print(json.dumps(settings.get("hooks", {}), indent=2, ensure_ascii=False))
        else:
            write_settings(settings)
        return

    block = build_block(interp)
    print(f"Plataforma : {'Windows' if IS_WINDOWS else 'macOS' if IS_MAC else 'Linux'}")
    print(f"Intérprete : {interp}")
    print(f"Scripts    : {HERE.as_posix()}")
    print(f"Eventos    : {', '.join(EVENTS)}")

    merged = merge(settings, block)
    if args.dry_run:
        print("\n── settings.json resultante (dry-run, NO escrito) ──")
        print(json.dumps(merged, indent=2, ensure_ascii=False))
    else:
        write_settings(merged)

    check_deps()
    print("\n🔁 Reinicia Claude Code para que tome los hooks.")


if __name__ == "__main__":
    main()
