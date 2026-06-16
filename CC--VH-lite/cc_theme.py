#!/usr/bin/env python3
"""
cc_theme.py — Branding compartido de CC--VH-lite (paleta + isotipo).

Una sola fuente de verdad para la identidad visual: la paleta "dev-tool DNA"
y el isotipo (hexágono + C + bocina) dibujado en código con Pillow, así que
escala nítido a CUALQUIER tamaño (16/32 px de bandeja, 64 del header) sin
arrastrar archivos .ico/.png.

Usado por cc_tray.py (íconos de bandeja por estado) y cc_config_gui.py
(logo del header). Si Pillow no está, todo lo de imagen degrada a None y los
consumidores caen a su fallback.

── Paleta (del mockup de branding) ──
    Background  #1e1e24   深い炭黒   charcoal profundo
    Accent      #00bfff   電気青     azul eléctrico
    Status      #3ebf9b   ミント     verde menta
    Text        #d1d1d1   柔灰       gris suave
"""

import math

# ── Paleta ───────────────────────────────────────────────────────────────────
BG        = "#1e1e24"   # fondo principal (deep charcoal)
BG_CARD   = "#26262e"   # superficies elevadas (cards, secciones)
BG_INPUT  = "#2d2d37"   # inputs, tracks de slider apagados
ACCENT    = "#00bfff"   # azul eléctrico — acción / activo
ACCENT_HI = "#33ccff"   # hover del accent
ACCENT_LO = "#0a7fb0"   # accent presionado / borde
STATUS    = "#3ebf9b"   # verde menta — DND / estado ok
STATUS_LO = "#2c8a70"
TEXT      = "#d1d1d1"   # texto principal
TEXT_DIM  = "#8b8b94"   # texto secundario / deshabilitado
BORDER    = "#3a3a45"   # bordes sutiles
DANGER    = "#ef5b6b"   # mute / cancelar / tachado

# Tipografía monospace (cae a la genérica del SO si no existe la preferida).
MONO_STACK = ("JetBrains Mono", "Cascadia Code", "Consolas", "Menlo",
              "DejaVu Sans Mono", "monospace")

# Colores del isotipo por estado del tray.
STATE_COLORS = {
    "active": ACCENT,      # notificando
    "dnd":    STATUS,      # No Molestar (con zZ)
    "muted":  TEXT_DIM,    # silencio global / sin sonido (con ×)
    "idle":   "#4a4a55",   # sin sesiones (apagado)
}

# ── Pillow opcional ───────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _hexagon(cx: float, cy: float, r: float, flat_top: bool = True):
    """6 vértices de un hexágono centrado en (cx, cy) con radio r."""
    off = 0 if flat_top else 30
    return [
        (cx + r * math.cos(math.radians(60 * i + off)),
         cy + r * math.sin(math.radians(60 * i + off)))
        for i in range(6)
    ]


def draw_logo(size: int, *, variant: str = "active",
              gradient: bool = False):
    """Dibuja el isotipo CC--VH-lite a `size` px. Devuelve PIL.Image o None.

    `variant`: 'active' | 'dnd' | 'muted' | 'idle' — define color y overlay
    (× para muted, zZ para dnd). `gradient`: si True, tiñe el trazo con un
    degradado cyan→mint (para el header grande); si False usa color sólido del
    estado (mejor para los íconos chicos de bandeja, donde importa el color
    semántico).

    Se dibuja con supersampling ×8 y se reduce con LANCZOS → bordes suaves
    incluso a 16 px.
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

    # ── Hexágono contenedor (flat-top) ──
    hex_r = S * 0.44
    hex_w = max(2, int(S * 0.085))
    d.polygon(_hexagon(cx, cy, hex_r), outline=rgb + (255,), width=hex_w)

    # ── "C" abierta a la derecha (Claude Code) ──
    c_r = S * 0.24
    c_w = max(2, int(S * 0.105))
    c_box = [cx - c_r, cy - c_r, cx + c_r, cy + c_r]
    # PIL arc: 0°=3-en-punto, sentido horario. 50→310 deja abierto el sector
    # derecho → forma de C que mira a la bocina.
    d.arc(c_box, start=50, end=310, fill=rgb + (255,), width=c_w)

    # ── Bocina (speaker) en la abertura de la C ──
    sp_x = cx + S * 0.085
    sp_y = cy
    bw = S * 0.075   # ancho cuerpo
    bh = S * 0.085   # medio-alto cono
    # cuerpo rectangular + cono triangular
    d.rectangle([sp_x - bw, sp_y - bh * 0.45, sp_x - bw * 0.3, sp_y + bh * 0.45],
                fill=rgb + (255,))
    d.polygon([(sp_x - bw * 0.3, sp_y - bh * 0.45),
               (sp_x + bw * 0.7, sp_y - bh),
               (sp_x + bw * 0.7, sp_y + bh),
               (sp_x - bw * 0.3, sp_y + bh * 0.45)],
              fill=rgb + (255,))

    # ── Overlays de estado ──
    if variant == "muted":
        # × roja sobre la bocina (sonido cortado)
        xr = S * 0.10
        xc, yc = sp_x + S * 0.04, sp_y
        xw = max(2, int(S * 0.05))
        dr = _hex(DANGER)
        d.line([(xc - xr, yc - xr), (xc + xr, yc + xr)], fill=dr + (255,), width=xw)
        d.line([(xc - xr, yc + xr), (xc + xr, yc - xr)], fill=dr + (255,), width=xw)
    elif variant == "dnd":
        # "zZ" arriba-derecha (durmiendo)
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
    """Tiñe los píxeles opacos de mask_img con un degradado vertical."""
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
    # usa el alpha del logo como máscara
    alpha = mask_img.split()[3]
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), alpha)
    return out
