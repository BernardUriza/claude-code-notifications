#!/usr/bin/env python3
"""
install.py — One-command installer for CC--VH-lite (banners + voice).

Does all the manual work the README used to ask for:
  - Detects your platform (macOS / Windows / Linux).
  - Detects THIS clone's path and the right Python interpreter
    (`python3` on macOS/Linux, `python` on Windows — whichever is on PATH).
  - MERGES the Stop + Notification hooks into your ~/.claude/settings.json
    WITHOUT clobbering what you already have (backs up before writing).
    Idempotent: you can run it a thousand times, it won't duplicate.
  - Checks the optional dependencies and tells you what's missing.

Usage:
    python install.py            # install / update the hooks
    python install.py --dry-run  # show what it would do, without writing anything
    python install.py --uninstall  # remove ONLY this repo's hooks

Claude Code runs the hooks via a POSIX shell (Git Bash on Windows), which is why
the paths are written with forward slashes (`D:/...`), which work on both.
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
from pathlib import Path

# The Windows console defaults to cp1252 and blows up on emojis/box-drawing.
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
# Markers to recognize OUR hooks (from any path) so we can re-install or
# uninstall without touching other people's hooks.
MARKERS = ("cc_notify.py", "cc_voice_lite.py")


def pick_interpreter() -> str:
    """Interpreter name to put in the hook: whichever is on PATH."""
    order = ("python", "python3") if IS_WINDOWS else ("python3", "python")
    for cand in order:
        if shutil.which(cand):
            return cand
    return order[0]


def hook_command(interp: str, script: Path) -> str:
    """Hook command with a POSIX path (works in Git Bash and in sh)."""
    p = script.as_posix()
    if " " in p:
        p = f'"{p}"'
    return f"{interp} {p}"


def build_block(interp: str) -> dict:
    """The {event: [ {hooks:[...]} ]} block with our two scripts."""
    entry = {
        "hooks": [
            {"type": "command", "command": hook_command(interp, NOTIFY)},
            {"type": "command", "command": hook_command(interp, VOICE)},
        ]
    }
    return {ev: [entry] for ev in EVENTS}


def is_ours(entry: dict) -> bool:
    """True if this hook entry points at our scripts (any path)."""
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
        print(f"⚠️  Couldn't read {SETTINGS}: {e}")
        print("    Aborting to avoid corrupting your config. Fix it and retry.")
        sys.exit(1)


def merge(settings: dict, block: dict) -> dict:
    """Insert our hooks, first removing ANY previous version of ours
    (idempotent + handles re-clones to a different path). Respects others' hooks."""
    hooks = settings.setdefault("hooks", {})
    for ev, entries in block.items():
        existing = hooks.get(ev, [])
        # remove our old entries, keep everyone else's
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
    print(f"✅ Wrote: {SETTINGS}")


def _has_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def check_deps() -> None:
    print("\n── Dependencies ──")
    ok, opt = "✅", "·"

    azure = (Path.home() / ".secrets" / "azure-openai-key.txt").exists()
    print(f"  {ok if azure else opt} Azure TTS key (~/.secrets/azure-openai-key.txt) "
          f"{'yes' if azure else 'no — paid voice, optional'}")

    edge = shutil.which("edge-tts") is not None
    print(f"  {ok if edge else opt} edge-tts (FREE neural voice) "
          f"{'yes' if edge else 'no — install with: pip install edge-tts'}")

    # Tray icon
    has_pystray = _has_module("pystray")
    has_pillow  = _has_module("PIL")
    tray_ok = has_pystray and has_pillow
    if tray_ok:
        print(f"  {ok} pystray + pillow — tray icon available (python cc_tray.py)")
    else:
        missing = []
        if not has_pystray:
            missing.append("pystray")
        if not has_pillow:
            missing.append("pillow")
        print(f"  {opt} tray icon (optional) — missing: pip install {' '.join(missing)}")

    # Config window (cross-platform). Tkinter is bundled → always runs.
    has_tk  = _has_module("tkinter")
    has_ctk = _has_module("customtkinter")
    if has_ctk:
        print(f"  {ok} config window (CustomTkinter, modern look) — python cc_config_gui.py")
    elif has_tk:
        print(f"  {ok} config window (Tkinter bundled) — python cc_config_gui.py · nicer look: pip install customtkinter")
    else:
        print(f"  {opt} config window — tkinter missing (rare); CustomTkinter: pip install customtkinter")

    if IS_WINDOWS:
        ps = shutil.which("powershell") is not None
        print(f"  {ok if ps else '❌'} powershell {'yes' if ps else 'NO — required for banners and voice'}")
        bt = False
        if ps:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "if (Get-Module -ListAvailable -Name BurntToast) { 'yes' } else { 'no' }"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=0x08000000)  # CREATE_NO_WINDOW
                bt = r.stdout.strip() == "yes"
            except Exception:
                pass
        print(f"  {ok if bt else opt} BurntToast {'yes' if bt else 'no — banners fall back to WinRT (zero deps); for the nicer path: Install-Module BurntToast'}")
    elif IS_MAC:
        tn = shutil.which("terminal-notifier") is not None
        hs = shutil.which("hs") is not None
        print(f"  {ok if tn else opt} terminal-notifier {'yes' if tn else 'no — optional (osascript fallback)'}")
        print(f"  {ok if hs else opt} Hammerspoon (hs) {'yes' if hs else 'no — optional (banner with a button)'}")
    else:
        print(f"  {opt} Linux: cc_notify uses macOS paths; the banners don't apply. The voice does (edge-tts/say-equiv).")


def _tray_autostart_path() -> Path:
    """Windows Startup folder (runs at login)."""
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def tray_autostart_install(interp: str) -> None:
    """Create a .vbs in Startup to launch cc_tray.py with no console window.

    Robust against spaces in the path: quotes the interpreter and script (in VBS
    quotes are escaped by doubling them → `""`). Uses the ABSOLUTE interpreter
    path (sys.executable), not the bare `python` name, because the login
    process's PATH can differ from the shell's PATH where it was installed.
    """
    if not IS_WINDOWS:
        print("ℹ️  Tray autostart only applies on Windows.")
        return
    startup = _tray_autostart_path()
    startup.mkdir(parents=True, exist_ok=True)
    vbs = startup / "cc_tray.vbs"

    # Absolute interpreter path (falls back to the name only if sys.executable is missing).
    py = sys.executable or interp
    script = str(TRAY)

    def _vbs_quote(s: str) -> str:
        # VBS quoting: inner " → "" ; wrapped in quotes for the shell.
        return '""' + s.replace('"', '""') + '""'

    cmd = f"{_vbs_quote(py)} {_vbs_quote(script)}"
    # wscript.exe runs the command with Run windowless (0 = hidden, False = don't wait)
    vbs_content = (
        'Set WShell = CreateObject("WScript.Shell")\r\n'
        f'WShell.Run "{cmd}", 0, False\r\n'
    )
    vbs.write_text(vbs_content, encoding="utf-8")
    print(f"✅ Autostart created: {vbs}")
    print("   The tray icon will launch automatically at Windows startup.")
    print("   To remove it: python install.py --tray-autostart-remove")


def tray_autostart_remove() -> None:
    if not IS_WINDOWS:
        return
    vbs = _tray_autostart_path() / "cc_tray.vbs"
    if vbs.exists():
        vbs.unlink()
        print(f"🗑️  Autostart removed: {vbs}")
    else:
        print("No tray autostart was installed.")


# Must match cc_notify.TOAST_AUMID.
TOAST_AUMID = "ClaudeCode.VoiceHandler"
TOAST_DISPLAY = "CC--VH-lite"


def register_toast_aumid() -> None:
    """Register a custom AppUserModelID + Start Menu shortcut so Windows 11
    pops the floating banner (not just an Action Center entry).

    Windows 11 only shows the banner for an AUMID registered as an app. Without
    this, cc_notify's toast still reaches the Action Center, just without the
    popup. Sets three things: the AUMID identity (DisplayName), ShowBanner=1,
    and a Start Menu shortcut with the AUMID embedded (the piece that makes
    Windows treat it as a real app, like Discord does).
    """
    if not IS_WINDOWS:
        print("ℹ️  Toast banner registration only applies on Windows.")
        return
    ps = r'''
$AumID = "''' + TOAST_AUMID + r'''"
$Disp = "''' + TOAST_DISPLAY + r'''"
$ShortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\CC--VH-lite.lnk"
$ck = "HKCU:\Software\Classes\AppUserModelId\$AumID"
New-Item $ck -Force | Out-Null
Set-ItemProperty $ck -Name "DisplayName" -Value $Disp -Type String
$nk = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\$AumID"
New-Item $nk -Force | Out-Null
Set-ItemProperty $nk -Name "Enabled" -Value 1 -Type DWord
Set-ItemProperty $nk -Name "ShowBanner" -Value 1 -Type DWord
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices; using System.Text;
namespace CCVHInstall {
  [ComImport, Guid("00021401-0000-0000-C000-000000000046")] internal class SL { }
  [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
  internal interface ISL {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder f,int c,IntPtr p,uint fl); void GetIDList(out IntPtr p); void SetIDList(IntPtr p);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder n,int c); void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string n);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder d,int c); void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string d);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder a,int c); void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string a);
    void GetHotkey(out short h); void SetHotkey(short h); void GetShowCmd(out uint s); void SetShowCmd(uint s);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder i,int c,out int x); void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string i,int x);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string r,uint d); void Resolve(IntPtr h,uint f); void SetPath([MarshalAs(UnmanagedType.LPWStr)] string f); }
  [ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  internal interface IPF { void GetClassID(out Guid c); [PreserveSig] int IsDirty(); void Load([MarshalAs(UnmanagedType.LPWStr)] string f,uint m);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string f,[MarshalAs(UnmanagedType.Bool)] bool r); void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f); void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string f); }
  [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  internal interface IPS { void GetCount(out uint c); void GetAt(uint i,out PK k); void GetValue(ref PK k,out PV v); void SetValue(ref PK k,ref PV v); void Commit(); }
  [StructLayout(LayoutKind.Sequential)] internal struct PK { public Guid fmtid; public uint pid; }
  [StructLayout(LayoutKind.Explicit)] internal struct PV { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
  public static class H {
    static PK K = new PK { fmtid=new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid=5 };
    public static void Create(string path,string target,string aumid){
      var l=(ISL)new SL(); l.SetPath(target);
      var v=new PV(); v.vt=31; v.p=Marshal.StringToCoTaskMemUni(aumid);
      ((IPS)l).SetValue(ref K, ref v); ((IPS)l).Commit(); Marshal.FreeCoTaskMem(v.p);
      ((IPF)l).Save(path,true); }
  }
}
'@
[CCVHInstall.H]::Create($ShortcutPath, "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe", $AumID)
'''
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1",
                                     delete=False, encoding="utf-8") as fh:
        fh.write(ps)
        tmp = fh.name
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", tmp],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000, check=False)
        print(f"✅ Toast banner registered (AUMID '{TOAST_DISPLAY}').")
        print("   Note: the FIRST notification may take a couple of minutes to pop")
        print("   while Windows indexes the new shortcut. After that it's instant.")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="CC--VH-lite installer")
    ap.add_argument("--dry-run", action="store_true", help="show without writing")
    ap.add_argument("--uninstall", action="store_true", help="remove only our hooks")
    ap.add_argument("--tray-autostart", action="store_true",
                    help="(Windows) install cc_tray.py into Startup")
    ap.add_argument("--tray-autostart-remove", action="store_true",
                    help="(Windows) remove the tray autostart")
    ap.add_argument("--register-toast", action="store_true",
                    help="(Windows) register the AUMID so banners pop (not just Action Center)")
    args = ap.parse_args()

    if args.register_toast:
        register_toast_aumid()
        return

    if not NOTIFY.exists() or not VOICE.exists():
        print(f"❌ Can't find the scripts in {HERE}. Are you running this from the clone?")
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
        print("🗑️  Removing CC--VH-lite hooks.")
        if args.dry_run:
            print(json.dumps(settings.get("hooks", {}), indent=2, ensure_ascii=False))
        else:
            write_settings(settings)
        return

    block = build_block(interp)
    print(f"Platform    : {'Windows' if IS_WINDOWS else 'macOS' if IS_MAC else 'Linux'}")
    print(f"Interpreter : {interp}")
    print(f"Scripts     : {HERE.as_posix()}")
    print(f"Events      : {', '.join(EVENTS)}")

    merged = merge(settings, block)
    if args.dry_run:
        print("\n── resulting settings.json (dry-run, NOT written) ──")
        print(json.dumps(merged, indent=2, ensure_ascii=False))
    else:
        write_settings(merged)
        # Windows 11 only pops the banner for a registered AUMID — set it up so
        # notifications actually show as banners, not just Action Center entries.
        if IS_WINDOWS:
            register_toast_aumid()

    check_deps()
    print("\n🔁 Restart Claude Code so it picks up the hooks.")


if __name__ == "__main__":
    main()
