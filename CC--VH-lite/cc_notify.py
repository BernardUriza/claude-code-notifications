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
    ccn dnd <min>       No Molestar por N minutos (se reactiva solo)
    ccn dnd             ver cuánto falta del DND activo
    ccn dnd off         cancelar DND antes de que expire
    ccn sound           sonido on/off (toggle) — banner sin ding
    ccn clear           borra todos los banners en pantalla + limpia estado
    ccn status          estado actual (quiet, dnd, sonido, sesiones muteadas)

── Prueba manual ──
    echo '{"hook_event_name":"Stop","cwd":"/tmp/symfarmia","session_id":"abc123"}' \
        | python3 cc_notify.py
"""

import os
import re
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path

# La consola de Windows usa cp1252 por defecto y truena con emojis (los prints de
# `ccn list/status` los usan). UTF-8 para no reventar.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

IS_WINDOWS = sys.platform.startswith("win")

# ── Estado en disco (banderas como archivos: simple, sin daemon) ──
STATE = Path.home() / ".cc-notify"
MUTE_DIR = STATE / "mute"          # mute/<sid> = sesión silenciada
SESS_DIR = STATE / "sessions"      # sessions/<sid>.json = última info (para `list`)
QUIET = STATE / "quiet"            # existe = silencio global
NOSOUND = STATE / "nosound"        # existe = banner sin sonido

SOUND = "Glass"                    # sonido suave (macOS); cámbialo o apágalo con `ccn sound`
MAX_CHARS = 140                    # corta el mensaje aquí (banner no cabe más)
SELF = str(Path(__file__).resolve())
PY = sys.executable or "python3"
TN = shutil.which("terminal-notifier")
DND = STATE / "dnd"               # existe con timestamp = No Molestar hasta esa hora


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


def _xml_escape(s: str) -> str:
    """Escapa los caracteres que romperían el XML del toast WinRT."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def _notify_windows(title: str, subtitle: str, message: str, group: str) -> None:
    """Toast nativo de Windows 10/11, NO bloqueante.

    Agrupado por sesión (cada terminal reemplaza su propio toast vía Tag/group):
      - BurntToast si el módulo está instalado (mejor UX, `Install-Module BurntToast`).
      - Si no, WinRT (Windows.UI.Notifications) inline con el AppId de PowerShell
        — cero dependencias, funciona en cualquier Windows 10/11.
    Texto/título van por env vars para no pelear con el escaping de PowerShell.
    """
    body = f"{subtitle}: {message}" if message else subtitle
    silent = NOSOUND.exists()
    audio_xml = '<audio silent="true"/>' if silent else ''
    # XML completo armado en Python (valores ya escapados): PowerShell solo hace
    # LoadXml($env:CC_TOAST_XML), así no hay que pelear con comillas en la línea.
    toast_xml = (
        '<toast><visual><binding template="ToastGeneric">'
        f'<text>{_xml_escape(title)}</text>'
        f'<text>{_xml_escape(body)}</text>'
        f'</binding></visual>{audio_xml}</toast>'
    )
    env = os.environ.copy()
    env["CC_TITLE"] = title
    env["CC_BODY"] = body
    env["CC_GROUP"] = group or "claude"
    env["CC_TOAST_XML"] = toast_xml

    silent_bt = "-Silent" if silent else ""
    ps = (
        "$ErrorActionPreference='Stop';"
        "if (Get-Module -ListAvailable -Name BurntToast) {"
        "  Import-Module BurntToast;"
        f"  New-BurntToastNotification -Text $env:CC_TITLE,$env:CC_BODY -UniqueIdentifier $env:CC_GROUP {silent_bt};"
        "} else {"
        "  [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "  [Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;"
        "  $doc=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "  $doc.LoadXml($env:CC_TOAST_XML);"
        "  $t=New-Object Windows.UI.Notifications.ToastNotification $doc;"
        "  $t.Tag=$env:CC_GROUP; $t.Group='claude-code';"
        "  $appId='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($t);"
        "}"
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        env=env,
    )


def notify(title: str, subtitle: str, message: str, group: str) -> None:
    """Banner de macOS con botón "Detener voz" vía Hammerspoon (hs.notify).

    Hammerspoon es lo único que muestra banners CON botón en macOS 26
    (terminal-notifier 2.0.0 está roto; osascript no soporta botones). El botón
    "Detener voz" corta el afplay de la voz Arbor. Si `hs` no responde, cae a
    osascript (banner sin botón); para silenciar usa `ccn mute`/`ccn quiet`.

    En Windows enruta a toasts nativos (BurntToast / WinRT).
    """
    if IS_WINDOWS:
        _notify_windows(title, subtitle, message, group)
        return

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

    # Silencios: global, DND con timer, o por sesión → ni nos molestamos.
    dnd_active = False
    if DND.exists():
        try:
            exp = float(DND.read_text(encoding="utf-8").strip())
            if exp > time.time():
                dnd_active = True
            else:
                DND.unlink(missing_ok=True)   # expiró solo
        except (OSError, ValueError):
            DND.unlink(missing_ok=True)

    if QUIET.exists() or dnd_active or (MUTE_DIR / sid).exists():
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

    dnd_str = "off"
    if DND.exists():
        try:
            exp = float(DND.read_text(encoding="utf-8").strip())
            rem = exp - time.time()
            if rem > 0:
                h, m = divmod(int(rem), 3600)
                m2 = m // 60
                dnd_str = f"⏳ {h}h{m2:02d}min restantes (hasta {_fmt_time(exp)})" if h else f"⏳ {m2}min restantes (hasta {_fmt_time(exp)})"
            else:
                DND.unlink(missing_ok=True)
        except (OSError, ValueError):
            DND.unlink(missing_ok=True)

    print(f"Silencio global: {q}")
    print(f"DND:             {dnd_str}")
    print(f"Sonido:          {s}")
    print(f"Sesiones mute:   {', '.join(muted) if muted else '(ninguna)'}")
    if IS_WINDOWS:
        bt = "sí" if shutil.which("powershell") else "NO (powershell ausente)"
        print(f"backend toast:   Windows (BurntToast/WinRT) · powershell: {bt}")
    else:
        print(f"terminal-notifier: {'sí' if TN else 'NO (usando osascript)'}")


def cmd_dnd(arg: str | None) -> None:
    """DND con timer: `ccn dnd 60` silencia 60 min y se reactiva solo."""
    ensure_dirs()
    if arg in (None, ""):
        # Mostrar estado actual
        if DND.exists():
            try:
                exp = float(DND.read_text(encoding="utf-8").strip())
                rem = exp - time.time()
                if rem > 0:
                    h, m = divmod(int(rem), 3600)
                    m2 = m // 60
                    s2 = m % 60
                    if h:
                        print(f"⏳ DND activo — {h}h{m2:02d}min restantes (hasta las {_fmt_time(exp)}).")
                    else:
                        print(f"⏳ DND activo — {m2}min {s2:02d}s restantes (hasta las {_fmt_time(exp)}).")
                    return
            except (OSError, ValueError):
                pass
        print("DND inactivo. Uso: ccn dnd <minutos>  |  ccn dnd off")
        return
    if arg in ("off", "cancel", "0"):
        DND.unlink(missing_ok=True)
        print("🔔 DND cancelado — vuelven las notificaciones.")
        return
    try:
        minutes = int(arg)
    except ValueError:
        print(f"Error: '{arg}' no es un número de minutos. Ej: ccn dnd 60")
        return
    if minutes <= 0:
        DND.unlink(missing_ok=True)
        print("🔔 DND cancelado.")
        return
    exp = time.time() + minutes * 60
    DND.write_text(str(exp), encoding="utf-8")
    h, m = divmod(minutes, 60)
    dur = f"{h}h{m:02d}min" if h else f"{minutes}min"
    print(f"🤫 DND activado por {dur} (hasta las {_fmt_time(exp)}). Se reactiva solo.")


def _fmt_time(ts: float) -> str:
    """Hora local HH:MM para mostrar cuándo expira el DND."""
    import time as _t
    lt = _t.localtime(ts)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"


def cmd_stop() -> None:
    """Corta la voz que esté sonando y cualquier habla pendiente."""
    if IS_WINDOWS:
        # Mata el reproductor (PowerShell MediaPlayer) y el proceso detached que habla.
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" "
             "| Where-Object { $_.CommandLine -match 'CC_AUDIO_PATH|speak-now' } "
             "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
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
        "dnd": lambda: cmd_dnd(arg),
    }
    fn = dispatch.get(cmd)
    if fn:
        fn()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
