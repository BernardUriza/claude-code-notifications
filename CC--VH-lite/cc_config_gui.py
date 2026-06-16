#!/usr/bin/env python3
"""
cc_config_gui.py — Ventana de configuración de CC--VH-lite (macOS + Windows).

Una sola ventana para todo lo que antes estaba hardcodeado o enterrado en
comandos `ccn`: voz, mínimo de palabras, sonido, duración default del DND,
toggles de silencio, y la lista de sesiones con su estado de mute.

── Cross-platform ──
Usa CustomTkinter si está instalado (se ve IDÉNTICO en Mac y Windows, dark
mode). Si no, cae a Tkinter puro (viene incluido en Python en ambos SO), así
que SIEMPRE corre — sin instalar nada. Para el look bonito:
    pip install customtkinter

── Uso ──
    python cc_config_gui.py
"""

import sys
import time
from pathlib import Path

import cc_config

# ── Backend GUI: CustomTkinter si existe, si no Tkinter puro ──
USING_CTK = False
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    USING_CTK = True
except ImportError:
    import tkinter as ctk          # type: ignore  # mismo alias, API parcial compatible
    from tkinter import ttk

import tkinter as tk
from tkinter import messagebox

# ── Estado en disco (mismo que cc_notify.py) ──
STATE   = Path.home() / ".cc-notify"
MUTE_DIR = STATE / "mute"
QUIET   = STATE / "quiet"
NOSOUND = STATE / "nosound"
DND     = STATE / "dnd"
SESS_DIR = STATE / "sessions"

VOICES = ["onyx", "alloy", "ash", "ballad", "coral", "echo",
          "fable", "nova", "sage", "shimmer"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de estado en disco
# ─────────────────────────────────────────────────────────────────────────────

def _toggle_file(p: Path, on: bool) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if on:
        p.touch()
    else:
        p.unlink(missing_ok=True)


def _dnd_remaining() -> float:
    if not DND.exists():
        return 0.0
    try:
        exp = float(DND.read_text(encoding="utf-8").strip())
        return max(0.0, exp - time.time())
    except (OSError, ValueError):
        return 0.0


def _set_dnd(minutes: int) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if minutes <= 0:
        DND.unlink(missing_ok=True)
    else:
        DND.write_text(str(time.time() + minutes * 60), encoding="utf-8")


def _sessions() -> list[dict]:
    import json
    out = []
    for f in sorted(SESS_DIR.glob("*.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d["_muted"] = (MUTE_DIR / d.get("sid", f.stem)).exists()
        out.append(d)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# La ventana
# ─────────────────────────────────────────────────────────────────────────────

class ConfigWindow:
    def __init__(self) -> None:
        self.cfg = cc_config.load()

        if USING_CTK:
            self.root = ctk.CTk()
        else:
            self.root = tk.Tk()
        self.root.title("CC--VH-lite · Configuración")
        self.root.geometry("520x680")
        self.root.minsize(480, 600)

        self._vars()
        self._build()
        self._refresh_dnd_label()

    # ── Variables Tk ──
    def _vars(self) -> None:
        self.var_voice    = tk.StringVar(value=self.cfg.get("voice", "onyx"))
        self.var_minwords = tk.IntVar(value=int(self.cfg.get("min_words", 30)))
        self.var_sound    = tk.StringVar(value=self.cfg.get("sound", "Glass"))
        self.var_dnddef   = tk.IntVar(value=int(self.cfg.get("dnd_default_min", 60)))
        self.var_repo     = tk.BooleanVar(value=bool(self.cfg.get("speak_repo_name", True)))
        self.var_quiet    = tk.BooleanVar(value=QUIET.exists())
        self.var_nosound  = tk.BooleanVar(value=NOSOUND.exists())

    # ── Construcción de widgets (compatible CTk / Tk) ──
    def _frame(self, parent, **kw):
        return ctk.CTkFrame(parent, **kw) if USING_CTK else tk.Frame(parent, **kw)

    def _label(self, parent, text, **kw):
        if USING_CTK:
            return ctk.CTkLabel(parent, text=text, **kw)
        return tk.Label(parent, text=text, **kw)

    def _button(self, parent, text, cmd, **kw):
        if USING_CTK:
            return ctk.CTkButton(parent, text=text, command=cmd, **kw)
        # OJO: en tk.Button `width` es en CARACTERES, no píxeles como en CTk.
        # Descartamos el width estilo-CTk para que el botón tome su tamaño
        # natural y los botones en fila (DND) no se empujen fuera de la ventana.
        kw.pop("width", None)
        return tk.Button(parent, text=text, command=cmd, **kw)

    def _switch(self, parent, text, var, cmd):
        if USING_CTK:
            return ctk.CTkSwitch(parent, text=text, variable=var, command=cmd)
        return tk.Checkbutton(parent, text=text, variable=var, command=cmd)

    def _build(self) -> None:
        pad = {"padx": 16, "pady": 6}

        title = self._label(self.root, "⚙️  CC--VH-lite", font=("", 20, "bold"))
        title.pack(pady=(16, 4))
        sub = self._label(
            self.root,
            f"Backend GUI: {'CustomTkinter' if USING_CTK else 'Tkinter'}  ·  {sys.platform}",
        )
        sub.pack(pady=(0, 10))

        # ── DND rápido ──
        dnd_frame = self._frame(self.root)
        dnd_frame.pack(fill="x", **pad)
        self._label(dnd_frame, "No Molestar", font=("", 14, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        self.dnd_status = self._label(dnd_frame, "")
        self.dnd_status.pack(anchor="w", padx=10)
        btns = self._frame(dnd_frame)
        btns.pack(fill="x", padx=10, pady=8)
        for mins, lbl in [(30, "30 min"), (60, "1 h"), (120, "2 h"), (240, "4 h")]:
            self._button(btns, lbl, lambda m=mins: self._dnd_set(m), width=70).pack(side="left", padx=4)
        self._button(btns, "Cancelar", lambda: self._dnd_set(0), width=80).pack(side="left", padx=4)

        # ── Toggles ──
        tog = self._frame(self.root)
        tog.pack(fill="x", **pad)
        self._label(tog, "Silencios", font=("", 14, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        self._switch(tog, "Silencio global (quiet)", self.var_quiet, self._apply_quiet).pack(anchor="w", padx=10, pady=4)
        self._switch(tog, "Sin sonido (banner mudo)", self.var_nosound, self._apply_nosound).pack(anchor="w", padx=10, pady=(4, 8))

        # ── Voz / config persistente ──
        vf = self._frame(self.root)
        vf.pack(fill="x", **pad)
        self._label(vf, "Voz y lectura", font=("", 14, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        row = self._frame(vf)
        row.pack(fill="x", padx=10, pady=4)
        self._label(row, "Voz TTS:").pack(side="left")
        if USING_CTK:
            ctk.CTkOptionMenu(row, values=VOICES, variable=self.var_voice).pack(side="left", padx=8)
        else:
            ttk.Combobox(row, values=VOICES, textvariable=self.var_voice, state="readonly", width=12).pack(side="left", padx=8)

        rowm = self._frame(vf)
        rowm.pack(fill="x", padx=10, pady=4)
        self._label(rowm, "Mín. palabras:").pack(side="left")
        self.lbl_minwords = self._label(rowm, str(self.var_minwords.get()))
        if USING_CTK:
            ctk.CTkSlider(rowm, from_=5, to=120, number_of_steps=23,
                          variable=self.var_minwords,
                          command=lambda v: self.lbl_minwords.configure(text=str(int(float(v))))
                          ).pack(side="left", padx=8, fill="x", expand=True)
        else:
            tk.Scale(rowm, from_=5, to=120, orient="horizontal",
                     variable=self.var_minwords, showvalue=True).pack(side="left", padx=8, fill="x", expand=True)
        self.lbl_minwords.pack(side="left")

        rowd = self._frame(vf)
        rowd.pack(fill="x", padx=10, pady=4)
        self._label(rowd, "DND default (min):").pack(side="left")
        self.lbl_dnddef = self._label(rowd, str(self.var_dnddef.get()))
        if USING_CTK:
            ctk.CTkSlider(rowd, from_=15, to=240, number_of_steps=15,
                          variable=self.var_dnddef,
                          command=lambda v: self.lbl_dnddef.configure(text=str(int(float(v))))
                          ).pack(side="left", padx=8, fill="x", expand=True)
        else:
            tk.Scale(rowd, from_=15, to=240, orient="horizontal",
                     variable=self.var_dnddef, showvalue=True).pack(side="left", padx=8, fill="x", expand=True)
        self.lbl_dnddef.pack(side="left")

        self._switch(vf, "Decir nombre del repo antes del texto", self.var_repo, lambda: None).pack(anchor="w", padx=10, pady=(4, 8))

        # ── Guardar ──
        save_row = self._frame(self.root)
        save_row.pack(fill="x", **pad)
        self._button(save_row, "💾  Guardar configuración", self._save, width=200).pack(pady=8)

        self.feedback = self._label(self.root, "")
        self.feedback.pack(pady=(0, 8))

    # ── Acciones ──
    def _refresh_dnd_label(self) -> None:
        rem = _dnd_remaining()
        if rem > 0:
            h, r = divmod(int(rem), 3600)
            m = r // 60
            txt = f"⏳ Activo — {h}h {m:02d}min restantes" if h else f"⏳ Activo — {m}min restantes"
        else:
            txt = "Inactivo"
        try:
            self.dnd_status.configure(text=txt)
        except tk.TclError:
            pass

    def _dnd_set(self, minutes: int) -> None:
        if minutes > 0 and QUIET.exists():
            _toggle_file(QUIET, False)
            self.var_quiet.set(False)
        _set_dnd(minutes)
        self._refresh_dnd_label()

    def _apply_quiet(self) -> None:
        on = self.var_quiet.get()
        _toggle_file(QUIET, on)
        if on:
            _set_dnd(0)
            self._refresh_dnd_label()

    def _apply_nosound(self) -> None:
        _toggle_file(NOSOUND, self.var_nosound.get())

    def _save(self) -> None:
        self.cfg["voice"]           = self.var_voice.get()
        self.cfg["min_words"]       = int(self.var_minwords.get())
        self.cfg["dnd_default_min"] = int(self.var_dnddef.get())
        self.cfg["speak_repo_name"] = bool(self.var_repo.get())
        try:
            cc_config.save(self.cfg)
            self.feedback.configure(text="✅ Guardado. Aplica en el próximo hook (sin reiniciar Claude).")
        except OSError as e:
            messagebox.showerror("Error", f"No pude guardar config:\n{e}")

    def run(self) -> None:
        # Refresca el label de DND cada 10s mientras la ventana esté abierta.
        def _tick():
            self._refresh_dnd_label()
            self.root.after(10000, _tick)
        self.root.after(10000, _tick)
        self.root.mainloop()


def main() -> None:
    ConfigWindow().run()


if __name__ == "__main__":
    main()
