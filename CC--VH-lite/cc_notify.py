#!/usr/bin/env python3
"""
cc-notify — Notificaciones nativas de macOS para Claude Code.

Reemplaza la voz invasiva. Cada sesión de Claude Code que termina dispara
un banner de macOS (vía terminal-notifier), AGRUPADO por sesión (no se
apilan 8 banners: cada terminal reemplaza el suyo). Haz click en el banner
para silenciar esa sesión. Control fino con el comando `ccn`.

── Cómo lo llaman los hooks de Claude Code ──
El hook manda un JSON por stdin con: hook_event_name, cwd, transcript_path,
session_id. Sin subcomando = modo hook (lee stdin y notifica).

── Control (alias `ccn`) ──
    ccn list            ver sesiones recientes y cuáles están silenciadas
    ccn mute <sid>      silenciar una sesión (ya no notifica)
    ccn mute all        silenciar TODAS las sesiones de golpe
    ccn unmute <sid>    reactivar una sesión   (o `unmute all`)
    ccn quiet           silencio global on/off (toggle)
    ccn sound           sonido on/off (toggle) — banner sin ding
    ccn clear           borra todos los banners en pantalla + limpia estado
    ccn status          estado actual (quiet, sonido, sesiones muteadas)

── Prueba manual ──
    echo '{"hook_event_name":"Stop","cwd":"/tmp/symfarmia","session_id":"abc123"}' \
        | python3 cc_notify.py
"""

import re
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path

# ── Estado en disco (banderas como archivos: simple, sin daemon) ──
STATE = Path.home() / ".cc-notify"
MUTE_DIR = STATE / "mute"          # mute/<sid> = sesión silenciada
SESS_DIR = STATE / "sessions"      # sessions/<sid>.json = última info (para `list`)
QUIET = STATE / "quiet"            # existe = silencio global
NOSOUND = STATE / "nosound"        # existe = banner sin sonido

SOUND = "Glass"                    # sonido suave; cámbialo o apágalo con `ccn sound`
MAX_CHARS = 140                    # corta el mensaje aquí (banner no cabe más)
SELF = str(Path(__file__).resolve())
PY = sys.executable or "python3"
TN = shutil.which("terminal-notifier")


def ensure_dirs() -> None:
    for d in (STATE, MUTE_DIR, SESS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def short_sid(sid: str | None) -> str:
    """SID corto y legible para grupos y comandos."""
    if not sid:
        return "claude"
    return re.sub(r"[^A-Za-z0-9_-]", "", sid)[:8] or "claude"


# ─────────────────────────────────────────────────────────────────────────────
# MODO HOOK
# ─────────────────────────────────────────────────────────────────────────────
def read_stdin_json() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def clean(text: str) -> str:
    """Quita markdown/código/emojis pa' un banner limpio."""
    if "```" in text:
        text = text.split("```", 1)[0]
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # [txt](url) -> txt
    text = re.sub(r"[*_`#>~|]", "", text)                  # símbolos markdown
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text) # emojis
    text = " ".join(text.split())
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def last_assistant_text(transcript_path: str) -> str | None:
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
        content = entry.get("message", {}).get("content")
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


def notify(title: str, subtitle: str, message: str, group: str) -> None:
    """Banner de macOS con botón "Detener voz" vía Hammerspoon (hs.notify).

    Hammerspoon es lo único que muestra banners CON botón en macOS 26
    (terminal-notifier 2.0.0 está roto; osascript no soporta botones). El botón
    "Detener voz" corta el afplay de la voz Arbor. Si `hs` no responde, cae a
    osascript (banner sin botón); para silenciar usa `ccn mute`/`ccn quiet`.
    """
    hs = shutil.which("hs")
    if hs:
        def _lua(s: str) -> str:
            s = str(s).replace("\\", "\\\\").replace('"', '\\"')
            return '"' + s.replace("\n", "\\n").replace("\r", " ") + '"'

        snd = "" if NOSOUND.exists() else SOUND
        call = (
            f"ccNotify({_lua(title)},{_lua(subtitle)},"
            f"{_lua(message or ' ')},{_lua(snd)})"
        )
        try:
            r = subprocess.run([hs, "-c", call], capture_output=True, timeout=5)
            if r.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass

    # Fallback osascript (sin botón). ensure_ascii=False por el emoji/acentos.
    sound = "" if NOSOUND.exists() else f' sound name "{SOUND}"'
    script = (
        f"display notification {json.dumps(message or ' ', ensure_ascii=False)} "
        f"with title {json.dumps(title, ensure_ascii=False)} "
        f"subtitle {json.dumps(subtitle, ensure_ascii=False)}{sound}"
    )
    subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_hook() -> None:
    ensure_dirs()
    data = read_stdin_json()
    event = data.get("hook_event_name", "Stop")
    sid = short_sid(data.get("session_id"))

    # Silencios: global o por sesión → ni nos molestamos.
    if QUIET.exists() or (MUTE_DIR / sid).exists():
        sys.exit(0)

    cwd = data.get("cwd") or ""
    project = Path(cwd).name or "Claude Code"

    if event == "Notification":
        message = clean(data.get("message") or "Necesita tu atención")
        subtitle = "Necesita input"
    else:  # Stop / SubagentStop
        tp = data.get("transcript_path")
        message = clean(last_assistant_text(tp) or "Terminó") if tp else "Terminó"
        subtitle = "Listo" if event == "Stop" else "Subagente listo"

    # Guarda metadata pa' `ccn list`.
    try:
        (SESS_DIR / f"{sid}.json").write_text(json.dumps({
            "sid": sid, "project": project, "subtitle": subtitle,
            "message": message, "ts": time.time(),
        }), encoding="utf-8")
    except OSError:
        pass

    notify(f"✅ {project}", subtitle, message, group=sid)
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# MODO CONTROL (alias `ccn`)
# ─────────────────────────────────────────────────────────────────────────────
def remove_banner(group: str) -> None:
    if TN:
        subprocess.run([TN, "-remove", group],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_list() -> None:
    ensure_dirs()
    files = sorted(SESS_DIR.glob("*.json"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        print("Sin sesiones registradas todavía.")
        return
    gq = " (SILENCIO GLOBAL activo)" if QUIET.exists() else ""
    print(f"Sesiones recientes{gq}:")
    print(f"  {'SID':<10} {'PROYECTO':<22} {'EDO':<6} ÚLTIMO")
    now = time.time()
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = d.get("sid", f.stem)
        muted = "MUTE" if (MUTE_DIR / sid).exists() else "on"
        ago = int(now - d.get("ts", now))
        when = f"{ago}s" if ago < 60 else f"{ago // 60}m"
        msg = (d.get("message", "") or "")[:34]
        print(f"  {sid:<10} {d.get('project', '?'):<22} {muted:<6} {when:>4} · {msg}")
    print("\n  ccn mute <sid> · ccn unmute <sid> · ccn quiet · ccn clear")


def cmd_mute(target: str | None) -> None:
    ensure_dirs()
    if target in (None, ""):
        print("Uso: ccn mute <sid>   (o  ccn mute all)")
        return
    if target == "all":
        n = 0
        for f in SESS_DIR.glob("*.json"):
            (MUTE_DIR / f.stem).touch()
            remove_banner(f.stem)
            n += 1
        print(f"🔇 Silenciadas {n} sesiones. (ccn unmute all pa' revertir)")
        return
    sid = short_sid(target)
    (MUTE_DIR / sid).touch()
    remove_banner(sid)
    print(f"🔇 Sesión {sid} silenciada.")


def cmd_unmute(target: str | None) -> None:
    ensure_dirs()
    if target == "all":
        n = 0
        for f in MUTE_DIR.glob("*"):
            f.unlink(missing_ok=True)
            n += 1
        print(f"🔔 Reactivadas {n} sesiones.")
        return
    if not target:
        print("Uso: ccn unmute <sid>   (o  ccn unmute all)")
        return
    sid = short_sid(target)
    (MUTE_DIR / sid).unlink(missing_ok=True)
    print(f"🔔 Sesión {sid} reactivada.")


def cmd_quiet() -> None:
    ensure_dirs()
    if QUIET.exists():
        QUIET.unlink(missing_ok=True)
        print("🔔 Silencio global APAGADO — vuelven las notificaciones.")
    else:
        QUIET.touch()
        print("🔇 Silencio global ENCENDIDO — cero notificaciones (ccn quiet pa' revertir).")


def cmd_sound() -> None:
    ensure_dirs()
    if NOSOUND.exists():
        NOSOUND.unlink(missing_ok=True)
        print(f"🔔 Sonido ENCENDIDO ({SOUND}).")
    else:
        NOSOUND.touch()
        print("🤫 Sonido APAGADO — banners mudos (siguen visibles).")


def cmd_clear() -> None:
    ensure_dirs()
    if TN:
        subprocess.run([TN, "-remove", "ALL"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for f in SESS_DIR.glob("*.json"):
        f.unlink(missing_ok=True)
    print("🧹 Banners borrados y lista limpiada.")


def cmd_status() -> None:
    ensure_dirs()
    q = "ON 🔇" if QUIET.exists() else "off"
    s = "off 🤫" if NOSOUND.exists() else f"on ({SOUND})"
    muted = sorted(f.name for f in MUTE_DIR.glob("*"))
    print(f"Silencio global: {q}")
    print(f"Sonido:          {s}")
    print(f"Sesiones mute:   {', '.join(muted) if muted else '(ninguna)'}")
    print(f"terminal-notifier: {'sí' if TN else 'NO (usando osascript)'}")


def cmd_stop() -> None:
    """Corta la voz Arbor que esté sonando (afplay) y cualquier habla pendiente."""
    subprocess.run(["pkill", "-x", "afplay"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-f", "cc_voice_lite.py --speak-now"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("⏹️  Voz detenida.")


def main() -> None:
    if len(sys.argv) < 2:
        run_hook()
        return
    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    dispatch = {
        "list": lambda: cmd_list(),
        "ls": lambda: cmd_list(),
        "mute": lambda: cmd_mute(arg),
        "unmute": lambda: cmd_unmute(arg),
        "quiet": lambda: cmd_quiet(),
        "sound": lambda: cmd_sound(),
        "clear": lambda: cmd_clear(),
        "status": lambda: cmd_status(),
        "stop": lambda: cmd_stop(),
    }
    fn = dispatch.get(cmd)
    if fn:
        fn()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
