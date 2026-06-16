#!/usr/bin/env python3
"""
cc_toast_state — "will the toast actually be seen?" predictor.

Toast-first design: the banner is the primary channel; the voice is a COMPLEMENT
that only speaks when the toast is going to be shown. If Windows notifications
are off for our app (or globally), the banner can't pop — and we stay fully
silent, voice included. This keeps the Core Law's "silence must silence both
banner and voice" true even when the silence comes from Windows itself.

How it predicts WITHOUT blocking or a daemon:
  We don't (can't, cheaply) observe the on-screen render. We read the same
  per-app + global notification toggles Windows uses to decide whether to pop a
  banner — straight from the registry via `winreg` (microseconds, zero
  subprocess, so the hook still returns immediately). This is a PREDICTION, not
  an observation: Focus Assist / Do Not Disturb can still route an "Enabled"
  toast silently to the Action Center. Detecting Focus Assist reliably needs
  WNF/P-Invoke and is intentionally left out (frail, and not free) — documented
  limitation, not an oversight.

Independence: this module imports nothing from cc_notify / cc_voice_lite. The
AUMID is duplicated on purpose (CLAUDE.md: "Duplicate small path/config logic
when it prevents runtime coupling"). It MUST match cc_notify.TOAST_AUMID.

Degrade-gracefully contract: anything we can't determine → return True (assume
the toast shows). Non-Windows, missing winreg, registry errors → True. That way
a missing/odd environment never silences the voice by accident; the gate only
fires on an EXPLICIT "notifications off" signal.

Manual test:
    python cc_toast_state.py        # prints whether the toast would be seen
"""

import sys

IS_WINDOWS = sys.platform.startswith("win")

# Must match cc_notify.TOAST_AUMID.
TOAST_AUMID = "ClaudeCode.VoiceHandler"

# Registry locations Windows consults before popping a banner.
_GLOBAL_KEY = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"
_GLOBAL_VAL = "ToastEnabled"          # master toggle (0 = all toasts off)
_APP_KEY = (r"Software\Microsoft\Windows\CurrentVersion"
            r"\Notifications\Settings\\" + TOAST_AUMID)
_APP_VAL = "Enabled"                  # per-app toggle (0 = our app's toasts off)


def _reg_dword(subkey: str, name: str):
    """Read an HKCU DWORD. Returns the int, or None if key/value is absent.

    Absent is NOT zero: an unset toggle means "default on" in Windows, so we
    return None and let the caller treat it as enabled.
    """
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return int(val)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None


def toast_will_show() -> bool:
    """Predict whether a banner would actually pop for our AUMID.

    True unless an EXPLICIT "off" toggle says otherwise — so the voice gate only
    fires on a real "notifications disabled" signal, never on uncertainty.

    NOTE: does NOT account for Focus Assist / Do Not Disturb (see module docs).
    """
    if not IS_WINDOWS:
        return True   # macOS/Linux: no cheap predictor → assume it shows.
    if _reg_dword(_GLOBAL_KEY, _GLOBAL_VAL) == 0:
        return False  # master toast switch off → nothing pops.
    if _reg_dword(_APP_KEY, _APP_VAL) == 0:
        return False  # our app's toasts switched off in Settings.
    return True


if __name__ == "__main__":
    shown = toast_will_show()
    print(f"toast_will_show() = {shown}")
    if IS_WINDOWS:
        g = _reg_dword(_GLOBAL_KEY, _GLOBAL_VAL)
        a = _reg_dword(_APP_KEY, _APP_VAL)
        print(f"  global ToastEnabled = {g if g is not None else '(unset → on)'}")
        print(f"  app Enabled ({TOAST_AUMID}) = {a if a is not None else '(unset → on)'}")
        print("  note: Focus Assist / Do Not Disturb is NOT detected here.")
