#!/usr/bin/env python3
"""
cc_config_gui.py — Control center for CC--VH-lite (macOS + Windows).

ONE window, two tabs:
  · Onyx    — every Claude response captured by cc_onyx_capture.py, newest
              first, each with a ▶ that speaks it in the onyx voice (lazy
              synthesis, cached). Provided by cc_onyx_panel.OnyxFeedFrame.
  · Settings — DND, silence switches, voice & reading preferences.

Dev-tool DNA look & feel: technical dark mode, monospace typography, charcoal
+ electric-blue + mint-green palette (cc_theme). Premium widgets via
CustomTkinter when installed; falls back to plain Tkinter (bundled with
Python) so it ALWAYS runs. The plain branch uses cc_theme.flat_button /
flat_check because macOS aqua Tk ignores colors on native buttons/checkboxes
(white widgets that break the dark UI), and the ttk `clam` theme so the tabs,
combobox and scrollbars obey the palette too.

    pip install customtkinter   # for the premium look from the mockup

Usage:
    python cc_config_gui.py
"""

import sys
import time
from pathlib import Path

import cc_config
import cc_theme

# ── GUI backend: CustomTkinter if present, otherwise plain Tkinter ──
USING_CTK = False
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    USING_CTK = True
except ImportError:
    import tkinter as ctk          # type: ignore

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

from cc_onyx_panel import OnyxFeedFrame

# ── State on disk (same as cc_notify.py) ──
STATE   = Path.home() / ".cc-notify"
MUTE_DIR = STATE / "mute"
QUIET   = STATE / "quiet"
NOSOUND = STATE / "nosound"
DND     = STATE / "dnd"
SESS_DIR = STATE / "sessions"

VOICES = ["onyx", "alloy", "ash", "ballad", "coral", "echo",
          "fable", "nova", "sage", "shimmer"]

# Palette shortcut
C = cc_theme


# ─────────────────────────────────────────────────────────────────────────────
# Disk-state helpers
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


# ─────────────────────────────────────────────────────────────────────────────
# The window
# ─────────────────────────────────────────────────────────────────────────────

class ConfigWindow:
    def __init__(self) -> None:
        self.cfg = cc_config.load()
        self._imgs = []   # refs to CTkImage/PhotoImage so the GC doesn't drop them

        if USING_CTK:
            self.root = ctk.CTk()
            self.root.configure(fg_color=C.BG)
        else:
            self.root = tk.Tk()
            self.root.configure(bg=C.BG)
        self.root.title("CC--VH-lite")
        self.root.geometry("580x780")
        self.root.minsize(500, 620)

        self.mono = self._pick_mono()
        self._vars()
        self._build()
        self._refresh_dnd_label()

    # ── Monospace typeface available on the OS ──
    def _pick_mono(self) -> str:
        fams = set(tkfont.families())
        for f in C.MONO_STACK:
            if f in fams:
                return f
        return "Consolas" if sys.platform.startswith("win") else "Courier"

    def _f(self, size: int, bold: bool = False):
        """Monospace font (CTkFont under CTk, tuple under Tkinter)."""
        weight = "bold" if bold else "normal"
        if USING_CTK:
            return ctk.CTkFont(family=self.mono, size=size, weight=weight)
        return (self.mono, size, weight)

    # ── Tk variables ──
    def _vars(self) -> None:
        self.var_voice    = tk.StringVar(value=self.cfg.get("voice", "onyx"))
        self.var_minwords = tk.IntVar(value=int(self.cfg.get("min_words", 30)))
        self.var_dnddef   = tk.IntVar(value=int(self.cfg.get("dnd_default_min", 60)))
        self.var_repo     = tk.BooleanVar(value=bool(self.cfg.get("speak_repo_name", True)))
        self.var_quiet    = tk.BooleanVar(value=QUIET.exists())
        self.var_nosound  = tk.BooleanVar(value=NOSOUND.exists())

    # ── Build ──
    def _build(self) -> None:
        self._build_header()
        if USING_CTK:
            self._build_ctk()
        else:
            self._build_tk()

    def _build_header(self) -> None:
        """Logo + wordmark, shared by both branches, above the tabs."""
        if USING_CTK:
            head = ctk.CTkFrame(self.root, fg_color="transparent")
            head.pack(fill="x", padx=18, pady=(14, 8))
            if _HAS_PIL:
                pil = cc_theme.draw_logo(46, variant="active", gradient=True)
                if pil is not None:
                    img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(46, 46))
                    self._imgs.append(img)
                    ctk.CTkLabel(head, image=img, text="").pack(side="left", padx=(0, 12))
            wm = ctk.CTkFrame(head, fg_color="transparent")
            wm.pack(side="left")
            ctk.CTkLabel(wm, text="CC--VH-lite", font=self._f(21, bold=True),
                         text_color=C.ACCENT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(wm, text="voz onyx · notificaciones · control", font=self._f(11),
                         text_color=C.TEXT_DIM, anchor="w").pack(anchor="w")
        else:
            head = tk.Frame(self.root, bg=C.BG)
            head.pack(fill="x", padx=18, pady=(14, 8))
            if _HAS_PIL:
                pil = cc_theme.draw_logo(46, variant="active", gradient=True)
                if pil is not None:
                    img = ImageTk.PhotoImage(pil)
                    self._imgs.append(img)
                    tk.Label(head, image=img, bg=C.BG).pack(side="left", padx=(0, 12))
            wm = tk.Frame(head, bg=C.BG)
            wm.pack(side="left")
            tk.Label(wm, text="CC--VH-lite", bg=C.BG, fg=C.ACCENT,
                     font=(self.mono, 19, "bold"), anchor="w").pack(anchor="w")
            tk.Label(wm, text="voz onyx · notificaciones · control", bg=C.BG,
                     fg=C.TEXT_DIM, font=(self.mono, 10), anchor="w").pack(anchor="w")

    # ─────────────────────────────────────────────────────────────────────────
    # PREMIUM branch (CustomTkinter)
    # ─────────────────────────────────────────────────────────────────────────
    def _card(self, parent):
        return ctk.CTkFrame(parent, fg_color=C.BG_CARD, corner_radius=12)

    def _section_title(self, parent, text):
        return ctk.CTkLabel(parent, text=text, font=self._f(15, bold=True),
                            text_color=C.TEXT, anchor="w")

    def _build_ctk(self) -> None:
        tabs = ctk.CTkTabview(
            self.root, fg_color=C.BG,
            segmented_button_fg_color=C.BG_CARD,
            segmented_button_selected_color=C.ACCENT_LO,
            segmented_button_selected_hover_color=C.ACCENT,
            segmented_button_unselected_color=C.BG_CARD,
            segmented_button_unselected_hover_color=C.BG_INPUT,
            text_color=C.TEXT)
        tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        tab_onyx = tabs.add("  Onyx  ")
        tab_cfg = tabs.add("  Settings  ")

        OnyxFeedFrame(tab_onyx, self.mono).pack(fill="both", expand=True)

        root = ctk.CTkScrollableFrame(tab_cfg, fg_color=C.BG,
                                      scrollbar_button_color=C.BG_CARD)
        root.pack(fill="both", expand=True)

        # ── Do Not Disturb (pills) ──
        c1 = self._card(root); c1.pack(fill="x", pady=8)
        self._section_title(c1, "  Do Not Disturb").pack(fill="x", padx=14, pady=(12, 2))
        self.dnd_status = ctk.CTkLabel(c1, text="", font=self._f(12),
                                       text_color=C.STATUS, anchor="w")
        self.dnd_status.pack(fill="x", padx=16, pady=(0, 6))
        pills = ctk.CTkFrame(c1, fg_color="transparent")
        pills.pack(fill="x", padx=12, pady=(0, 14))
        for mins, lbl in [(30, "30 min"), (60, "1 h"), (120, "2 h"), (240, "4 h")]:
            ctk.CTkButton(
                pills, text=lbl, width=64, height=30, corner_radius=15,
                font=self._f(12, bold=True),
                fg_color="transparent", hover_color=C.BG_INPUT,
                border_width=1, border_color=C.ACCENT, text_color=C.ACCENT,
                command=lambda m=mins: self._dnd_set(m),
            ).pack(side="left", padx=4)
        self.btn_cancel = ctk.CTkButton(
            pills, text="Cancel", width=78, height=30, corner_radius=15,
            font=self._f(12), fg_color=C.BG_INPUT, hover_color=C.DANGER,
            text_color=C.TEXT_DIM, command=lambda: self._dnd_set(0),
        )
        self.btn_cancel.pack(side="left", padx=4)

        # ── Silence (switches) ──
        c2 = self._card(root); c2.pack(fill="x", pady=8)
        self._section_title(c2, "  Silence").pack(fill="x", padx=14, pady=(12, 4))
        self._ctk_switch(c2, "Global silence (quiet)", self.var_quiet, self._apply_quiet)
        self._ctk_switch(c2, "No sound (silent banner)", self.var_nosound, self._apply_nosound)
        ctk.CTkFrame(c2, fg_color="transparent", height=8).pack()

        # ── Voice & reading ──
        c3 = self._card(root); c3.pack(fill="x", pady=8)
        self._section_title(c3, "  Voice & reading").pack(fill="x", padx=14, pady=(12, 8))

        rv = ctk.CTkFrame(c3, fg_color="transparent"); rv.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(rv, text="TTS voice", font=self._f(12), text_color=C.TEXT,
                     width=120, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(rv, values=VOICES, variable=self.var_voice,
                          font=self._f(12), width=130,
                          fg_color=C.BG_INPUT, button_color=C.ACCENT_LO,
                          button_hover_color=C.ACCENT, text_color=C.TEXT,
                          dropdown_fg_color=C.BG_CARD, dropdown_text_color=C.TEXT,
                          dropdown_hover_color=C.BG_INPUT).pack(side="left", padx=8)

        self.lbl_minwords = self._ctk_slider(
            c3, "Min. words", self.var_minwords, 5, 120)
        self.lbl_dnddef = self._ctk_slider(
            c3, "DND default (min)", self.var_dnddef, 15, 240)

        self._ctk_switch(c3, "Speak the repo name before the text",
                         self.var_repo, lambda: None)
        ctk.CTkFrame(c3, fg_color="transparent", height=10).pack()

        # ── Save ──
        self.btn_save = ctk.CTkButton(
            root, text="Save settings", height=40, corner_radius=20,
            font=self._f(14, bold=True), fg_color=C.ACCENT,
            hover_color=C.ACCENT_HI, text_color="#08222e",
            command=self._save)
        self.btn_save.pack(pady=(14, 6))
        self.feedback = ctk.CTkLabel(root, text="", font=self._f(11),
                                     text_color=C.STATUS)
        self.feedback.pack(pady=(0, 10))

    def _ctk_switch(self, parent, text, var, cmd):
        sw = ctk.CTkSwitch(parent, text=text, variable=var, command=cmd,
                           font=self._f(12), text_color=C.TEXT,
                           progress_color=C.ACCENT, button_color=C.TEXT,
                           fg_color=C.BG_INPUT)
        sw.pack(anchor="w", padx=16, pady=6)
        return sw

    def _ctk_slider(self, parent, label, var, lo, hi):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(8, 4))
        ctk.CTkLabel(row, text=label, font=self._f(12), text_color=C.TEXT,
                     width=120, anchor="w").pack(side="left")
        val = ctk.CTkLabel(row, text=str(var.get()), font=self._f(12, bold=True),
                           text_color=C.ACCENT, width=34)
        val.pack(side="right")
        ctk.CTkSlider(row, from_=lo, to=hi, variable=var,
                      progress_color=C.ACCENT, button_color=C.ACCENT,
                      button_hover_color=C.ACCENT_HI, fg_color=C.BG_INPUT,
                      command=lambda v, l=val: l.configure(text=str(int(float(v))))
                      ).pack(side="left", fill="x", expand=True, padx=10)
        return val

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK branch (plain Tkinter) — flat widgets + clam ttk, dark everywhere
    # ─────────────────────────────────────────────────────────────────────────
    def _style_ttk(self) -> None:
        """clam is the only built-in ttk theme that fully obeys custom colors
        (aqua/default ignore background on tabs, comboboxes and scrollbars)."""
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("TNotebook", background=C.BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=C.BG_CARD, foreground=C.TEXT_DIM,
                     font=(self.mono, 12, "bold"), padding=(18, 8), borderwidth=0)
        st.map("TNotebook.Tab",
               background=[("selected", C.BG_INPUT)],
               foreground=[("selected", C.ACCENT)])
        st.configure("TCombobox", fieldbackground=C.BG_INPUT, background=C.BG_INPUT,
                     foreground=C.TEXT, arrowcolor=C.ACCENT, borderwidth=0,
                     selectbackground=C.BG_INPUT, selectforeground=C.TEXT)
        st.map("TCombobox", fieldbackground=[("readonly", C.BG_INPUT)])
        st.configure("Vertical.TScrollbar", background=C.BG_CARD,
                     troughcolor=C.BG, borderwidth=0, arrowcolor=C.TEXT_DIM)

    def _tk_card(self, parent, title):
        card = tk.Frame(parent, bg=C.BG_CARD,
                        highlightbackground=C.BORDER, highlightthickness=1)
        card.pack(fill="x", padx=14, pady=7)
        tk.Label(card, text=title, bg=C.BG_CARD, fg=C.TEXT,
                 font=(self.mono, 13, "bold"), anchor="w"
                 ).pack(fill="x", padx=14, pady=(10, 2))
        return card

    def _build_tk(self) -> None:
        self._style_ttk()

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        tab_onyx = tk.Frame(nb, bg=C.BG)
        tab_cfg = tk.Frame(nb, bg=C.BG)
        nb.add(tab_onyx, text="  Onyx  ")
        nb.add(tab_cfg, text="  Settings  ")

        OnyxFeedFrame(tab_onyx, self.mono).pack(fill="both", expand=True,
                                                padx=4, pady=4)

        # ── Do Not Disturb ──
        f1 = self._tk_card(tab_cfg, "Do Not Disturb")
        self.dnd_status = tk.Label(f1, text="", bg=C.BG_CARD, fg=C.STATUS,
                                   font=(self.mono, 11), anchor="w")
        self.dnd_status.pack(fill="x", padx=14)
        bf = tk.Frame(f1, bg=C.BG_CARD)
        bf.pack(fill="x", padx=12, pady=(6, 12))
        for mins, lbl in [(30, "30 min"), (60, "1 h"), (120, "2 h"), (240, "4 h")]:
            C.flat_button(bf, lbl, lambda m=mins: self._dnd_set(m), kind="ghost",
                          font=(self.mono, 11, "bold")).pack(side="left", padx=3)
        C.flat_button(bf, "Cancel", lambda: self._dnd_set(0), kind="dim",
                      font=(self.mono, 11)).pack(side="left", padx=3)

        # ── Silence ──
        f2 = self._tk_card(tab_cfg, "Silence")
        C.flat_check(f2, "Global silence (quiet)", self.var_quiet,
                     self._apply_quiet, font=(self.mono, 12)
                     ).pack(anchor="w", padx=14, pady=3)
        C.flat_check(f2, "No sound (silent banner)", self.var_nosound,
                     self._apply_nosound, font=(self.mono, 12)
                     ).pack(anchor="w", padx=14, pady=(3, 12))

        # ── Voice & reading ──
        f3 = self._tk_card(tab_cfg, "Voice & reading")
        rv = tk.Frame(f3, bg=C.BG_CARD)
        rv.pack(fill="x", padx=14, pady=5)
        tk.Label(rv, text="TTS voice", bg=C.BG_CARD, fg=C.TEXT,
                 font=(self.mono, 12), width=16, anchor="w").pack(side="left")
        ttk.Combobox(rv, values=VOICES, textvariable=self.var_voice,
                     state="readonly", width=12,
                     font=(self.mono, 11)).pack(side="left", padx=4)
        self.lbl_minwords = self._tk_slider(f3, "Min. words", self.var_minwords, 5, 120)
        self.lbl_dnddef = self._tk_slider(f3, "DND default", self.var_dnddef, 15, 240)
        C.flat_check(f3, "Speak the repo name before the text", self.var_repo,
                     font=(self.mono, 12)).pack(anchor="w", padx=14, pady=(6, 12))

        # ── Save ──
        C.flat_button(tab_cfg, "Save settings", self._save, kind="primary",
                      font=(self.mono, 13, "bold"), padx=22, pady=8).pack(pady=(12, 4))
        self.feedback = tk.Label(tab_cfg, text="", bg=C.BG, fg=C.STATUS,
                                 font=(self.mono, 10))
        self.feedback.pack(pady=(0, 8))

    def _tk_slider(self, parent, label, var, lo, hi):
        row = tk.Frame(parent, bg=C.BG_CARD)
        row.pack(fill="x", padx=14, pady=4)
        tk.Label(row, text=label, bg=C.BG_CARD, fg=C.TEXT,
                 font=(self.mono, 12), width=16, anchor="w").pack(side="left")
        lbl = tk.Label(row, text=str(var.get()), bg=C.BG_CARD, fg=C.ACCENT,
                       font=(self.mono, 12, "bold"), width=4)
        lbl.pack(side="right")
        tk.Scale(row, from_=lo, to=hi, orient="horizontal", variable=var,
                 showvalue=False, bg=C.BG_CARD, fg=C.TEXT, troughcolor=C.BG_INPUT,
                 highlightthickness=0, relief="flat", bd=0, sliderrelief="flat",
                 activebackground=C.ACCENT,
                 command=lambda v, l=lbl: l.configure(text=str(int(float(v))))
                 ).pack(side="left", fill="x", expand=True, padx=8)
        return lbl

    # ── Actions (shared by both branches) ──
    def _refresh_dnd_label(self) -> None:
        rem = _dnd_remaining()
        if rem > 0:
            h, r = divmod(int(rem), 3600)
            m = r // 60
            txt = f"⏳ Active — {h}h {m:02d}min left" if h else f"⏳ Active — {m}min left"
        else:
            txt = "Inactive"
        try:
            self.dnd_status.configure(text=txt)
            if hasattr(self, "btn_cancel"):
                self.btn_cancel.configure(state="normal" if rem > 0 else "disabled")
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
            self.feedback.configure(text="✅ Saved. Applies on the next hook (no Claude restart).")
        except OSError as e:
            messagebox.showerror("Error", f"Couldn't save config:\n{e}")

    def run(self) -> None:
        def _tick():
            self._refresh_dnd_label()
            try:
                self.root.after(10000, _tick)
            except tk.TclError:
                pass
        self.root.after(10000, _tick)
        self.root.mainloop()


def main() -> None:
    ConfigWindow().run()


if __name__ == "__main__":
    main()
