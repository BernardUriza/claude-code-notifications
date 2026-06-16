#!/usr/bin/env python3
"""
cc_theme.py — Shared branding for CC--VH-lite (palette + isotype).

A single source of truth for the visual identity: the "dev-tool DNA" palette
and the isotype (hexagon + C + speaker) drawn in code with Pillow, so it scales
crisp at ANY size (16/32 px tray, 64 px header) without shipping .ico/.png
files.

Used by cc_tray.py (per-state tray icons) and cc_config_gui.py (header logo).
If Pillow is missing, everything image-related degrades to None and the
consumers fall back.

── Palette (from the branding mockup) ──
    Background  #1e1e24   deep charcoal
    Accent      #00bfff   electric blue
    Status      #3ebf9b   mint green
    Text        #d1d1d1   soft gray
"""

import math

# ── Palette ──────────────────────────────────────────────────────────────────
BG        = "#1e1e24"   # main background (deep charcoal)
BG_CARD   = "#26262e"   # elevated surfaces (cards, sections)
BG_INPUT  = "#2d2d37"   # inputs, dimmed slider tracks
ACCENT    = "#00bfff"   # electric blue — action / active
ACCENT_HI = "#33ccff"   # accent hover
ACCENT_LO = "#0a7fb0"   # accent pressed / border
STATUS    = "#3ebf9b"   # mint green — DND / ok state
STATUS_LO = "#2c8a70"
TEXT      = "#d1d1d1"   # main text
TEXT_DIM  = "#8b8b94"   # secondary / disabled text
BORDER    = "#3a3a45"   # subtle borders
DANGER    = "#ef5b6b"   # mute / cancel / strikethrough

# Monospace typography (falls back to the OS generic if the preferred one is missing).
MONO_STACK = ("JetBrains Mono", "Cascadia Code", "Consolas", "Menlo",
              "DejaVu Sans Mono", "monospace")

# Isotype colors by tray state.
STATE_COLORS = {
    "active": ACCENT,      # notifying
    "dnd":    STATUS,      # Do Not Disturb (with zZ)
    "muted":  TEXT_DIM,    # global silence / no sound (with ×)
    "idle":   "#4a4a55",   # no sessions (off)
}

# ── Optional Pillow ──────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _hexagon(cx: float, cy: float, r: float, flat_top: bool = True):
    """6 vertices of a hexagon centered at (cx, cy) with radius r."""
    off = 0 if flat_top else 30
    return [
        (cx + r * math.cos(math.radians(60 * i + off)),
         cy + r * math.sin(math.radians(60 * i + off)))
        for i in range(6)
    ]


def draw_logo(size: int, *, variant: str = "active",
              gradient: bool = False):
    """Draw the CC--VH-lite isotype at `size` px. Returns PIL.Image or None.

    `variant`: 'active' | 'dnd' | 'muted' | 'idle' — sets color and overlay
    (× for muted, zZ for dnd). `gradient`: if True, tints the stroke with a
    cyan→mint gradient (for the large header); if False uses the state's solid
    color (better for the small tray icons, where the semantic color matters).

    Drawn with 8x supersampling and downscaled with LANCZOS → smooth edges even
    at 16 px.
    """
    if not _HAS_PIL:
        return None

    SS = 8
    S = max(size, 8) * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    color = STATE_COLORS.get(variant, ACCENT)
    rgb = _hex(color)
    cx = cy = S / 2

    # ── Container hexagon (flat-top) ──
    hex_r = S * 0.44
    hex_w = max(2, int(S * 0.085))
    d.polygon(_hexagon(cx, cy, hex_r), outline=rgb + (255,), width=hex_w)

    # ── "C" open to the right (Claude Code) ──
    c_r = S * 0.24
    c_w = max(2, int(S * 0.105))
    c_box = [cx - c_r, cy - c_r, cx + c_r, cy + c_r]
    # PIL arc: 0°=3-o'clock, clockwise. 50→310 leaves the right sector open
    # → a C shape facing the speaker.
    d.arc(c_box, start=50, end=310, fill=rgb + (255,), width=c_w)

    # ── Speaker in the C's opening ──
    sp_x = cx + S * 0.085
    sp_y = cy
    bw = S * 0.075   # body width
    bh = S * 0.085   # half-height of the cone
    # rectangular body + triangular cone
    d.rectangle([sp_x - bw, sp_y - bh * 0.45, sp_x - bw * 0.3, sp_y + bh * 0.45],
                fill=rgb + (255,))
    d.polygon([(sp_x - bw * 0.3, sp_y - bh * 0.45),
               (sp_x + bw * 0.7, sp_y - bh),
               (sp_x + bw * 0.7, sp_y + bh),
               (sp_x - bw * 0.3, sp_y + bh * 0.45)],
              fill=rgb + (255,))

    # ── State overlays ──
    if variant == "muted":
        # red × over the speaker (sound cut)
        xr = S * 0.10
        xc, yc = sp_x + S * 0.04, sp_y
        xw = max(2, int(S * 0.05))
        dr = _hex(DANGER)
        d.line([(xc - xr, yc - xr), (xc + xr, yc + xr)], fill=dr + (255,), width=xw)
        d.line([(xc - xr, yc + xr), (xc + xr, yc - xr)], fill=dr + (255,), width=xw)
    elif variant == "dnd":
        # "zZ" top-right (sleeping)
        zc = _hex(STATUS)
        zw = max(2, int(S * 0.028))
        def _z(ox, oy, s):
            d.line([(ox, oy), (ox + s, oy)], fill=zc + (255,), width=zw)
            d.line([(ox + s, oy), (ox, oy + s)], fill=zc + (255,), width=zw)
            d.line([(ox, oy + s), (ox + s, oy + s)], fill=zc + (255,), width=zw)
        _z(cx + S * 0.22, cy - S * 0.40, S * 0.11)
        _z(cx + S * 0.38, cy - S * 0.30, S * 0.075)

    if gradient:
        img = _apply_gradient(img, ACCENT, STATUS)

    return img.resize((max(size, 8), max(size, 8)), Image.LANCZOS)


def _apply_gradient(mask_img, top_color: str, bottom_color: str):
    """Tint the opaque pixels of mask_img with a vertical gradient."""
    S = mask_img.size[0]
    top, bot = _hex(top_color), _hex(bottom_color)
    grad = Image.new("RGBA", (S, S))
    gp = grad.load()
    for y in range(S):
        t = y / max(1, S - 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        for x in range(S):
            gp[x, y] = (r, g, b, 255)
    # use the logo's alpha as the mask
    alpha = mask_img.split()[3]
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), alpha)
    return out
