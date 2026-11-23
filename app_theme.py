"""The app window's look: warm dark glass with orange light, drawn with plain Tk + Pillow.

  * Surface - a page background: near-black brown with soft orange glows (like light behind glass).
  * Card    - a frosted-glass panel on a Surface. It copies the bit of the Surface behind it, tints and
              lightens it, adds a little grain, a sheen along the top and a soft border, and rounds the
              corners. Content is drawn straight onto the glass (text, numbers, charts, pill buttons) by
              the card's paint function, so the glass shows through everywhere. Things you type into or
              scroll (tables, inputs, the map) sit in darker rounded "wells" that are flat on purpose.
  * Pill buttons, badges, avatars, a pill tab bar, a tooltip, a canvas table, sliders and switches.

Tk can't make widgets see-through, so small widgets put on a card take the glass colour right behind
them (Card.put); the glass changes slowly across a card, so they blend in.
"""
from __future__ import annotations

import functools
import logging
import tkinter as tk
import tkinter.font as tkfont

PAINT_ERRORS: list[str] = []  # errors while drawing cards (tests fail on any)

# ---- colour themes ----------------------------------------------------------------------------------
# Each theme: page base, glass tint, the accent, a soft accent for text, a highlight tint for translucent
# whites, and three text colours. Everything else is worked out from these. use_theme() switches.
THEMES = {
    "ember": {"name": "Ember", "base": (12, 8, 6), "tint": (62, 39, 27), "accent": (244, 114, 43),
              "soft": (255, 178, 122), "hl": (255, 236, 220), "text": "#f7efe8", "muted": "#e2d3c6", "dim": "#c0aa9b"},
    "ocean": {"name": "Ocean", "base": (5, 8, 14), "tint": (26, 41, 64), "accent": (52, 156, 255),
              "soft": (140, 200, 255), "hl": (220, 236, 255), "text": "#eef5ff", "muted": "#d2deee", "dim": "#a3b6cc"},
    "amethyst": {"name": "Amethyst", "base": (9, 6, 14), "tint": (46, 31, 68), "accent": (166, 104, 255),
                 "soft": (206, 172, 255), "hl": (238, 228, 255), "text": "#f5f0ff", "muted": "#ded4f0", "dim": "#b3a5c9"},
    "emerald": {"name": "Emerald", "base": (5, 10, 7), "tint": (24, 54, 40), "accent": (46, 204, 128),
                "soft": (140, 236, 186), "hl": (222, 255, 238), "text": "#effaf3", "muted": "#d0e7d9", "dim": "#9fbfab"},
    "rose": {"name": "Rose", "base": (13, 6, 8), "tint": (66, 28, 40), "accent": (255, 82, 128),
             "soft": (255, 162, 192), "hl": (255, 228, 236), "text": "#fff0f4", "muted": "#f1d4dc", "dim": "#cba4b0"},
    "arctic": {"name": "Arctic", "base": (7, 9, 11), "tint": (38, 48, 56), "accent": (64, 206, 226),
               "soft": (150, 232, 244), "hl": (230, 246, 252), "text": "#f2f8fa", "muted": "#d4e1e6", "dim": "#a5b6bd"},
}
THEME_ORDER = ["arctic", "ember", "ocean", "amethyst", "emerald", "rose"]
_THEME_KEY = globals().get("_THEME_KEY", "arctic")  # kept across use_theme()'s reload
THEME = THEMES.get(_THEME_KEY, THEMES["arctic"])


def _mix(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def _hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c[:3])


_B, _T, _A, _HL = THEME["base"], THEME["tint"], THEME["accent"], THEME["hl"]
BASE = _hex(_B)                        # window background
WELL = _hex(_mix(_B, _T, 0.12))        # flat wells for tables, inputs and the map
WELL_LINE = _hex(_mix(_B, _T, 0.55))
ROW_HOVER = _hex(_mix(_B, _T, 0.36))
ROW_SELECT = _hex(_mix(_B, _A, 0.17))
PANEL = _hex(_mix(_B, _T, 0.45))       # ttk panels, menus, footers
PANEL2 = _hex(_mix(_B, _T, 0.62))
TEXT, MUTED, DIM = THEME["text"], THEME["muted"], THEME["dim"]
FAINT = _hex(_mix(_B, _T, 0.95))
ACCENT = _hex(_A)
ACCENT_HI = _hex(_mix(_A, (255, 255, 255), 0.22))
ACCENT_LO = _hex(_mix(_A, (0, 0, 0), 0.2))
ACCENT_SOFT = _hex(THEME["soft"])
ON_ACCENT = _hex(_mix(_A, (0, 0, 0), 0.9))   # text on accent buttons
GLASS_TINT = _T
GLASS_WARM = tuple(int(v * 0.36) for v in _T)  # extra colour at the top of the glass
ACCENT_RGB = _A
GREEN = "#6fdc95"
GREEN_BG = "#183823"
RED = "#ff8b7c"
RED_BG = "#40181a"
AMBER = "#ffc163"
AMBER_BG = "#3d2a10"
GOLD = "#ffc94d"


def hl(a: int) -> tuple:
    """The theme's translucent highlight (warm white for Ember, cool for Ocean...) at alpha a."""
    return _HL + (a,)


def acc(t: float = 0.0, a: int | None = None) -> tuple:
    """The accent, lightened (t > 0) or darkened (t < 0)."""
    c = _mix(_A, (255, 255, 255), t) if t >= 0 else _mix(_A, (0, 0, 0), -t)
    return c if a is None else c + (a,)


def use_theme(key: str) -> bool:
    """Switches the colour theme (rebuilds this module's colours). Returns True if it changed.
    Widgets already made keep their colours: the app window rebuilds itself after this."""
    import importlib
    import sys

    key = key if key in THEMES else "arctic"
    mod = sys.modules[__name__]
    if key == getattr(mod, "_THEME_KEY", None):
        return False
    mod._THEME_KEY = key
    importlib.reload(mod)
    return True


AVATAR_COLORS = [("#ff9a4d", "#c2451a"), ("#ffd06b", "#c47a12"), ("#ff7a7a", "#a8323a"), ("#c99cff", "#6d47b8"),
                 ("#7fd6ff", "#2f74b0"), ("#8ce8a4", "#2f8a52")]


def rgb(hexcol: str) -> tuple[int, int, int]:
    h = hexcol.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def hexc(c) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c[:3])


# ---- fonts -------------------------------------------------------------------------------------------
class Fonts:
    def __init__(self, root):
        fams = set(tkfont.families(root))

        def pick(*names):
            return next((n for n in names if n in fams), "TkDefaultFont")

        disp = pick("Segoe UI Variable Display Semib", "Segoe UI Semibold", "Segoe UI")
        disp_l = pick("Segoe UI Variable Display", "Segoe UI")
        text = pick("Segoe UI Variable Text", "Segoe UI")
        semi = pick("Segoe UI Variable Text Semibold", "Segoe UI Semibold", "Segoe UI")
        small = pick("Segoe UI Variable Small", "Segoe UI")
        small_semi = pick("Segoe UI Variable Small Semibol", "Segoe UI Semibold", "Segoe UI")
        self.icons = next((n for n in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if n in fams), None)
        F = lambda fam, size: tkfont.Font(root, family=fam, size=size)  # noqa: E731
        self.hero = F(disp, 40)
        self.huge = F(disp, 30)
        self.big = F(disp, 22)
        self.title = F(disp, 24)
        self.mid = F(disp, 16)
        self.h2 = F(disp, 12)
        self.light = F(disp_l, 12)
        self.body = F(text, 10)
        self.bold = F(semi, 10)
        self.small = F(text, 10)
        self.small_bold = F(semi, 10)
        self.cap = F(small_semi, 9)
        self.icon = F(self.icons or text, 11)
        self.icon_big = F(self.icons or text, 14)
        self.logo = F(disp, 15)
        self.nav = F(semi, 11)          # the top navigation buttons
        self.nav_big = F(disp, 13)      # ... and the bigger Map button


# ---- painting helpers (Pillow) ----------------------------------------------------------------------
_PIL = None


def _pil():
    global _PIL
    if _PIL is None:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFilter, ImageTk

        _PIL = (Image, ImageDraw, ImageFilter, ImageTk, np)
    return _PIL


@functools.lru_cache(maxsize=128)
def _corner(r: int, ring: bool = False):
    """The top-left corner of a rounded rectangle (filled, or just its 1 px edge), drawn 4x and scaled down."""
    Image, ImageDraw, *_ = _pil()
    k = 4
    big = Image.new("L", (2 * r * k, 2 * r * k), 0)
    if ring:
        ImageDraw.Draw(big).ellipse([0, 0, 2 * r * k - 1, 2 * r * k - 1], outline=255, width=k)
    else:
        ImageDraw.Draw(big).ellipse([0, 0, 2 * r * k - 1, 2 * r * k - 1], fill=255)
    return big.crop((0, 0, r * k, r * k)).resize((r, r), Image.LANCZOS)


def _with_corners(base, r, piece):
    Image = _pil()[0]
    w, h = base.size
    base.paste(piece, (0, 0))
    base.paste(piece.transpose(Image.FLIP_LEFT_RIGHT), (w - r, 0))
    base.paste(piece.transpose(Image.FLIP_TOP_BOTTOM), (0, h - r))
    base.paste(piece.transpose(Image.ROTATE_180), (w - r, h - r))
    return base


@functools.lru_cache(maxsize=512)
def rounded_mask(w, h, r, scale=3):
    """An anti-aliased rounded-rectangle mask (cached: never change the image you get back). Built from four
    smooth corners on a plain rectangle, so big panels cost no more than small ones."""
    Image = _pil()[0]
    w, h = int(w), int(h)
    r = max(0, min(int(r), w // 2, h // 2))
    m = Image.new("L", (w, h), 255)
    return _with_corners(m, r, _corner(r)) if r > 0 else m


DEFAULT_GLOWS = [  # (x, y as fractions of the width / first screen height, radius as a fraction of the width, colour, strength)
    (0.02, 1.00, 0.62, acc(0.04), 0.62),
    (1.00, 0.00, 0.42, acc(-0.06), 0.34),
    (0.58, 0.58, 0.38, acc(-0.3), 0.24),
    (0.30, 0.10, 0.30, acc(-0.5), 0.16),
]


def backdrop(w: int, h: int, glows=DEFAULT_GLOWS, view_h: int | None = None):
    """The page background: dark warm base, soft glows, a vignette."""
    Image, _d, _f, _t, np = _pil()
    s = 4  # draw small, scale up: glows are smooth anyway
    sw, sh = max(2, w // s), max(2, h // s)
    vh = (view_h or h) / s
    ys, xs = np.mgrid[0:sh, 0:sw].astype(np.float32)
    img = np.zeros((sh, sw, 3), np.float32) + np.array(rgb(BASE), np.float32)
    for fx, fy, fr, col, a in glows:
        cx, cy, r = fx * sw, fy * vh, fr * sw
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max(1.0, r)
        k = np.clip(1 - d, 0, 1) ** 2.2 * a
        img += k[..., None] * (np.array(col, np.float32) - img) * 0.9
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    return out.resize((w, h), Image.BICUBIC)


def glass(bg, radius: int, tint=None, amount=0.52, sheen=0.11, border=0.17, glows=(), seed=0):
    """Frosted glass over bg (PIL RGB, the card's size): returns an RGB image with the corners left as bg.
    glows: [(x, y as fractions of the card, radius as a fraction of its larger side, colour, strength)] -
    orange light inside the glass.
    Fast: everything soft (tint, sheen, glows) is worked out at a quarter of the size and scaled up; the
    grain and the 1 px border are cached layers at full size."""
    Image, ImageDraw, ImageFilter, _t, np = _pil()
    from PIL import ImageChops

    w, h = bg.size
    q = 4 if w * h > 40_000 else 1
    sw, sh = max(2, w // q), max(2, h // q)
    a = np.asarray(bg.resize((sw, sh), Image.BILINEAR) if q > 1 else bg, np.float32)
    t = np.array(tint or GLASS_TINT, np.float32)
    g = a * (1 - amount) + t * amount + 6.0  # tinted, a touch lighter
    ys = np.linspace(0, 1, sh, dtype=np.float32)[:, None, None]
    xs = np.linspace(0, 1, sw, dtype=np.float32)[None, :, None]
    g += 255 * sheen * np.clip(1 - ys / 0.55, 0, 1) ** 2  # light along the top
    g += (np.array(GLASS_WARM, np.float32) * (1 - ys)) - 8 * ys  # warm at the top, a little darker down
    g += 10 * (1 - xs) * (1 - ys)  # and a touch lighter towards the top left
    for fx, fy, fr, col, st in glows:
        d = np.sqrt(((xs - fx) * w) ** 2 + ((ys - fy) * h) ** 2) / max(1.0, fr * max(w, h))
        k = np.clip(1 - d, 0, 1) ** 2 * st
        g = g * (1 - k) + np.array(col, np.float32) * k
    gl = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8), "RGB")
    if q > 1:
        gl = gl.resize((w, h), Image.BICUBIC)
    gl = ImageChops.add(gl, _grain_img(w, h), scale=1.0, offset=-128)  # frost grain
    gl.paste((255, 255, 255), (0, 0), _border_mask(w, h, int(radius), round(border, 3)))  # the soft border
    out = bg.copy()
    out.paste(gl, (0, 0), rounded_mask(w, h, radius))
    return out


@functools.lru_cache(maxsize=128)
def _grain_img(w, h):
    """Frost grain around mid grey (128 +- a few), as RGB, for ImageChops.add (cached per size)."""
    Image, _D, _F, _t, np = _pil()
    n = np.clip(128 + _grain(w, h)[..., 0], 0, 255).astype(np.uint8)
    g = Image.fromarray(n, "L")
    return Image.merge("RGB", (g, g, g))


@functools.lru_cache(maxsize=128)
def _border_mask(w, h, radius, border):
    """The 1 px border, brighter at the top and fading down, as a paste mask (cached per size)."""
    Image, _D, _F, _t, np = _pil()
    ys = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    m = np.asarray(_edge(w, h, radius), np.float32) * (border * (0.45 + 0.55 * (1 - ys ** 0.7)))
    return Image.fromarray(np.clip(m, 0, 255).astype(np.uint8), "L")


@functools.lru_cache(maxsize=1)
def _grain_tile():
    _I, _D, _F, _t, np = _pil()
    return np.random.default_rng(7).normal(0, 2.2, (256, 256, 1)).astype(np.float32)


def _grain(w, h):
    _I, _D, _F, _t, np = _pil()
    tile = _grain_tile()
    return np.tile(tile, ((h + 255) // 256, (w + 255) // 256, 1))[:h, :w]


@functools.lru_cache(maxsize=256)
def _edge(w, h, radius):
    """The card's 1 px rounded border as a mask (cached): straight edges plus four smooth corner arcs."""
    Image, ImageDraw, *_ = _pil()
    r = max(1, min(int(radius), w // 2, h // 2))
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    d.line([(r, 0), (w - r - 1, 0)], fill=255)
    d.line([(r, h - 1), (w - r - 1, h - 1)], fill=255)
    d.line([(0, r), (0, h - r - 1)], fill=255)
    d.line([(w - 1, r), (w - 1, h - r - 1)], fill=255)
    return _with_corners(m, r, _corner(r, ring=True))


def pill_image(w, h, top, bottom, glow=None, outline=None, radius=None):
    """A rounded (pill) RGBA image with a vertical gradient, an optional outer glow and outline. Returns
    (image, margin). Cached by its arguments: the result is shared, so never draw onto it."""
    try:
        return _pill_cached(int(w), int(h), tuple(top), tuple(bottom), tuple(glow) if glow else None,
                            tuple(outline) if outline else None, None if radius is None else int(radius))
    except TypeError:  # something unhashable: just draw it
        return _pill_image(int(w), int(h), top, bottom, glow, outline, radius)


@functools.lru_cache(maxsize=4096)
def _pill_cached(w, h, top, bottom, glow, outline, radius):
    return _pill_image(w, h, top, bottom, glow, outline, radius)


def _pill_image(w, h, top, bottom, glow=None, outline=None, radius=None):
    Image, ImageDraw, ImageFilter, _t, np = _pil()
    w, h = max(1, int(w)), max(1, int(h))
    m = 10 if glow else 0
    W, H = w + 2 * m, h + 2 * m
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if glow:
        gl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(gl).rounded_rectangle([m, m + 3, m + w, m + h + 2], radius=(radius or h // 2), fill=glow)
        out = Image.alpha_composite(out, gl.filter(ImageFilter.GaussianBlur(6)))
    grad = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    body = np.array(top, np.float32) * (1 - grad) + np.array(bottom, np.float32) * grad
    body = np.repeat(body, w, axis=1)
    alpha = np.full((h, w, 1), 255, np.float32) if len(top) == 3 else body[..., 3:4]
    face = Image.fromarray(np.concatenate([body[..., :3], alpha], axis=2).astype(np.uint8), "RGBA")
    mask = rounded_mask(w, h, radius if radius is not None else h // 2)
    if len(top) == 4:
        mask = Image.fromarray((np.asarray(mask, np.float32) * np.asarray(face.split()[3], np.float32) / 255).astype(np.uint8))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layer.paste(face, (m, m), mask)
    out = Image.alpha_composite(out, layer)
    if outline:
        ol = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
        r = (radius if radius is not None else h // 2) * 2
        ImageDraw.Draw(ol).rounded_rectangle([1, 1, w * 2 - 2, h * 2 - 2], radius=r, outline=outline, width=2)
        out.alpha_composite(ol.resize((w, h), Image.LANCZOS), (m, m))
    return out, m


def circle_image(d, top, bottom, outline=None, glow=None):
    return pill_image(d, d, top, bottom, glow=glow, outline=outline, radius=d // 2)


@functools.lru_cache(maxsize=512)
def glow_image(w, h, color, alpha=120, blur=14):
    """A soft glow (cached: never draw onto the result)."""
    Image, ImageDraw, ImageFilter, *_ = _pil()
    w, h, color = int(w), int(h), tuple(color)
    m = blur * 2
    img = Image.new("RGBA", (w + 2 * m, h + 2 * m), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([m, m, m + w, m + h], radius=min(w, h) // 2, fill=color + (alpha,))
    return img.filter(ImageFilter.GaussianBlur(blur)), m


# ---- surfaces and cards -----------------------------------------------------------------------------
class Surface(tk.Canvas):
    """A page background with glows. Cards are gridded straight into it (no frames in between)."""

    def __init__(self, parent, glows=DEFAULT_GLOWS, view_h=None):
        super().__init__(parent, bg=BASE, highlightthickness=0, bd=0)
        self.glows, self.view_h = glows, view_h
        self.cards: list[Card] = []
        self.img = None
        self._photo = None
        self._item = self.create_image(0, 0, anchor="nw")
        self._after = None
        self._size = (0, 0)
        self.bind("<Configure>", self._configured)

    def _configured(self, e):
        if (e.width, e.height) == self._size:
            return
        self._size = (e.width, e.height)
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(25, self.render)

    def render(self):
        try:
            self._render()
        except tk.TclError:  # the window closed meanwhile
            pass

    def _render(self):
        from PIL import ImageTk

        self._after = None
        w, h = max(2, self.winfo_width()), max(2, self.winfo_height())
        vh = self.view_h() if callable(self.view_h) else self.view_h
        self.img = backdrop(w, h, self.glows, vh)
        self._photo = ImageTk.PhotoImage(self.img)
        self.itemconfigure(self._item, image=self._photo)
        self.tag_lower(self._item)
        for c in list(self.cards):
            try:
                if c.winfo_exists():
                    c.render()
            except tk.TclError:
                pass

    def crop(self, x, y, w, h):
        Image = _pil()[0]
        if self.img is None:
            return Image.new("RGB", (w, h), rgb(BASE))
        out = Image.new("RGB", (w, h), rgb(BASE))
        out.paste(self.img.crop((x, y, x + w, y + h)), (0, 0))
        return out


class Card(tk.Canvas):
    """A frosted-glass panel. paint(card) draws its content (tagged "c", redrawn on every change);
    card.put(widget, x, y) places a small widget on the glass in the glass's colour."""

    def __init__(self, surface: Surface, paint=None, radius=22, height=120, width=200, fonts=None, amount=0.56,
                 plain=False, glows=()):
        super().__init__(surface, bg=BASE, highlightthickness=0, bd=0, height=height, width=width)
        self.surface, self.paint_fn, self.radius, self.f, self.amount = surface, paint, radius, fonts, amount
        self.glows = glows
        self.plain = plain  # no glass: just the page behind (for titles)
        self.g = None
        self._photo = None
        self._bg = self.create_image(0, 0, anchor="nw")
        self._after = None
        self._size = (0, 0)
        self._imgs: list = []
        self._puts: dict = {}
        self._hover_cb = {}
        surface.cards.append(self)
        self.bind("<Configure>", self._configured)

    @property
    def W(self):
        return max(1, self.winfo_width())

    @property
    def H(self):
        return max(1, self.winfo_height())

    def _configured(self, e):
        if (e.width, e.height) == self._size:
            return
        self._size = (e.width, e.height)
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(20, self.render)

    def render(self):
        try:
            self._render()
        except tk.TclError:  # the window closed meanwhile
            pass

    def _render(self):
        from PIL import ImageTk

        self._after = None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 8:
            return
        x, y = self.winfo_x(), self.winfo_y()
        key = (w, h, x, y, id(self.surface.img), self.glows if isinstance(self.glows, tuple) else repr(self.glows))
        if key == getattr(self, "_glass_key", None) and self.g is not None:
            self.repaint()  # same size, place and page behind it: the glass is still right
            return
        self._glass_key = key
        bg = self.surface.crop(x, y, w, h)
        self.g = bg if self.plain else glass(bg, self.radius, amount=self.amount, glows=self.glows)
        self._photo = ImageTk.PhotoImage(self.g)
        self.itemconfigure(self._bg, image=self._photo)
        self.tag_lower(self._bg)
        self.repaint()

    def repaint(self):
        self.delete("c")
        self._imgs = []
        if self.paint_fn and self.g is not None:
            try:
                self.paint_fn(self)
            except tk.TclError:
                raise
            except Exception as e:  # a bug in one card shouldn't blank the window; logged, and tests check the list
                PAINT_ERRORS.append(repr(e))
                logging.getLogger("chest.app").exception("drawing a card")
        for wdg, (item, *_r) in list(self._puts.items()):
            self.tag_raise(item)
            self._tint(wdg)

    # --- glass colour where a widget sits
    def sample(self, x, y) -> str:
        if self.g is None:
            return hexc(rgb(BASE))
        x = min(max(0, int(x)), self.g.size[0] - 1)
        y = min(max(0, int(y)), self.g.size[1] - 1)
        return hexc(self.g.getpixel((x, y)))

    def put(self, widget, x, y, anchor="nw", width=None, height=None, tint=True):
        kw = {}
        if width:
            kw["width"] = width
        if height:
            kw["height"] = height
        if widget in self._puts:
            item = self._puts[widget][0]
            self.coords(item, x, y)
            self.itemconfigure(item, anchor=anchor, **kw)
        else:
            item = self.create_window(x, y, anchor=anchor, window=widget, **kw)
        if not str(widget).startswith(str(self) + "."):
            try:
                widget.tk.call("raise", widget._w, self._w)  # a sibling made before the card sits underneath it
                # (not widget.lift(): on a Canvas that raises canvas items instead)
            except tk.TclError:
                pass
        self._puts[widget] = (item, tint)
        self.after_idle(lambda: self._tint(widget))
        return item

    def _tint(self, widget):
        item, tint = self._puts.get(widget, (None, False))
        if not tint or item is None:
            return
        try:
            widget.update_idletasks()
            x0, y0 = self.coords(item)
            ww, wh = widget.winfo_width(), widget.winfo_height()
            anchor = self.itemcget(item, "anchor")
            # the widget's centre, whatever the anchor
            dx = {"nw": ww / 2, "w": ww / 2, "sw": ww / 2, "n": 0, "center": 0, "s": 0, "ne": -ww / 2, "e": -ww / 2,
                  "se": -ww / 2}.get(anchor, 0)
            dy = {"nw": wh / 2, "n": wh / 2, "ne": wh / 2, "w": 0, "center": 0, "e": 0, "sw": -wh / 2, "s": -wh / 2,
                  "se": -wh / 2}.get(anchor, 0)
            if hasattr(widget, "set_backdrop") and self.g is not None:
                left, top = int(x0 + dx - ww / 2), int(y0 + dy - wh / 2)
                widget.set_backdrop(self.g.crop((left, top, left + ww, top + wh)))  # the exact glass behind it
            _set_bg(widget, self.sample(x0 + dx, y0 + dy))
        except tk.TclError:
            pass

    def fit(self, bottom, pad=20):
        """Grow (or shrink) the card so content ending at `bottom` fits, with some room under it."""
        want = int(bottom + pad)
        if abs(want - int(self.cget("height"))) > 2 and abs(want - self.H) > 2:
            self.after_idle(lambda: self.configure(height=want))

    def keep(self, photo):
        self._imgs.append(photo)
        return photo

    # --- drawing shortcuts (all tagged "c")
    def text(self, x, y, text, font=None, fill=TEXT, anchor="nw", tags=(), width=None, justify="left"):
        kw = {"width": width} if width else {}
        return self.create_text(x, y, text=text, font=font or self.f.body, fill=fill, anchor=anchor,
                                tags=("c",) + tuple(tags), justify=justify, **kw)

    def image(self, x, y, pil, anchor="nw", tags=()):
        p = self.keep(photo_of(self, pil))
        return self.create_image(x, y, image=p, anchor=anchor, tags=("c",) + tuple(tags))

    def line(self, *pts, fill=FAINT, width=1, dash=None, tags=()):
        kw = {"dash": dash} if dash else {}
        return self.create_line(*pts, fill=fill, width=width, tags=("c",) + tuple(tags), **kw)

    def on_click(self, tag, fn, hover=None):
        """Click handler for drawn items with this tag; hover(on: bool) is called on enter / leave."""
        self.tag_bind(tag, "<ButtonRelease-1>", lambda e: fn())
        self.tag_bind(tag, "<Enter>", lambda e: (self.configure(cursor="hand2"), hover and hover(True)))
        self.tag_bind(tag, "<Leave>", lambda e: (self.configure(cursor=""), hover and hover(False)))


def photo_of(widget, pil):
    """The Tk image for a PIL picture, made once per picture and Tk interpreter and reused after that."""
    cached = getattr(pil, "_ct_photo", None)
    if cached is not None and cached[0] is widget.tk:
        return cached[1]
    from PIL import ImageTk

    ph = ImageTk.PhotoImage(pil, master=widget)
    try:
        pil._ct_photo = (widget.tk, ph)
    except AttributeError:
        pass
    return ph


_FIT: dict = {}


def fit_text(text, font, width) -> str:
    """The text, cut with "…" to fit width pixels in font (cached)."""
    text = str(text)
    width = int(width)
    key = (text, str(font), width)
    hit = _FIT.get(key)
    if hit is not None:
        return hit
    if width <= 8 or font.measure(text) <= width:
        out = text
    else:
        lo, hi = 0, len(text)  # longest prefix that fits, by halving
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid] + "…") <= width:
                lo = mid
            else:
                hi = mid - 1
        out = text[:lo] + "…"
    if len(_FIT) > 20000:
        _FIT.clear()
    _FIT[key] = out
    return out


def _set_bg(widget, col):
    try:
        widget.configure(bg=col)
    except tk.TclError:
        try:
            widget.configure(background=col)
        except tk.TclError:
            pass
    if isinstance(widget, (tk.Frame,)):
        for ch in widget.winfo_children():
            if isinstance(ch, (tk.Label, tk.Frame, tk.Canvas, tk.Checkbutton)):
                _set_bg(ch, col)


# ---- drawn controls ---------------------------------------------------------------------------------
PRIMARY = (acc(0.3), acc(-0.1))
PRIMARY_HOVER = (acc(0.42), acc(0.0))
GHOST = (hl(30), hl(10))
GHOST_HOVER = (hl(52), hl(22))
RADIUS = 11  # buttons, chips and in-card tabs: softly rounded rectangles (the top tabs stay pill shaped)


def pill(card: Card, x, y, text, command=None, kind="ghost", font=None, anchor="nw", tag=None, pad=16, h=34,
         icon=None, active=False, width=None, radius=RADIUS):
    """A pill button drawn on a card. Returns (x0, y0, x1, y1)."""
    f = font or card.f.bold
    tw = f.measure(text) + (card.f.icon.measure(icon) + 8 if icon else 0)
    w = width or (tw + 2 * pad)
    x0 = x - (w if "e" in anchor else w / 2 if anchor in ("n", "s", "center") else 0)
    y0 = y - (h if "s" in anchor else h / 2 if anchor in ("w", "e", "center") else 0)
    tag = tag or f"pill{id(text)}{int(x)}{int(y)}"
    prim = kind == "primary" or active
    look = {False: (GHOST, GHOST_HOVER), True: (PRIMARY, PRIMARY_HOVER)}[prim]
    if kind == "soft" and not active:  # accent-tinted glass
        look = ((acc(0.2, 70), acc(-0.2, 40)), (acc(0.25, 110), acc(-0.1, 70)))
    imgs = []
    for top, bot in look:
        img, m = pill_image(int(w), h, top, bot, glow=acc(0, 150) if (kind == "primary" or (prim and kind == "soft")) else None,
                            outline=acc(0.6, 90) if prim else (acc(0.3, 150) if kind == "soft" else hl(44)), radius=radius)
        imgs.append((img, m))
    (img, m) = imgs[0]
    item = card.image(x0 - m, y0 - m, img, tags=(tag,))
    fg = ON_ACCENT if prim else TEXT
    tx = x0 + w / 2
    if icon:
        iw = card.f.icon.measure(icon)
        start = x0 + (w - tw) / 2
        card.text(start, y0 + h / 2 + 1, icon, font=card.f.icon, fill=fg, anchor="w", tags=(tag,))
        card.text(start + iw + 8, y0 + h / 2, text, font=f, fill=fg, anchor="w", tags=(tag,))
    else:
        card.text(tx, y0 + h / 2, text, font=f, fill=fg, anchor="center", tags=(tag,))
    if command:
        from PIL import ImageTk

        hover_photo = card.keep(photo_of(card, imgs[1][0]))
        base_photo = card.itemcget(item, "image")
        card.on_click(tag, command, hover=lambda on: card.itemconfigure(item, image=hover_photo if on else base_photo))
    return x0, y0, x0 + w, y0 + h


CHIPS = {  # status chips: (top, bottom, text colour)
    "green": ((96, 206, 128), (46, 150, 84), "#08180d"),
    "red": ((255, 122, 100), (210, 66, 52), "#240806"),
    "amber": ((255, 206, 102), (226, 150, 40), "#241503"),
    "orange": (acc(0.3), acc(-0.1), ON_ACCENT),  # (the accent, whatever the theme)
    "muted": (hl(40), hl(22), MUTED),
}


@functools.lru_cache(maxsize=512)
def chip_image(w, h, kind):
    top, bot, _fg = CHIPS[kind]
    return pill_image(w, h, top, bot, radius=7, outline=(255, 245, 235, 60) if kind != "muted" else hl(30))[0]


def badge(card: Card, x, y, text, kind="green", anchor="nw", font=None, tag=()):
    fg = CHIPS[kind][2]
    f = font or card.f.small_bold
    w, h = f.measure(text) + 18, 22
    x0 = x - (w if "e" in anchor else w / 2 if anchor in ("n", "s", "center") else 0)
    y0 = y - (h if "s" in anchor else h / 2 if anchor in ("w", "e", "center") else 0)
    card.image(x0, y0, chip_image(w, h, kind), tags=tag)
    card.text(x0 + w / 2, y0 + h / 2, text, font=f, fill=fg, anchor="center", tags=tag)
    return x0, y0, x0 + w, y0 + h


def avatar(card: Card, x, y, name, d=36, tags=(), online=None):
    name = str(name or "?")
    top, bot = AVATAR_COLORS[sum(map(ord, name)) % len(AVATAR_COLORS)]
    img, _ = circle_image(d, rgb(top), rgb(bot))
    card.image(x, y, img, tags=tags)
    card.text(x + d / 2, y + d / 2, name[:1].upper(), font=card.f.bold, fill=ON_ACCENT, anchor="center", tags=tags)
    if online is not None:
        dot, _ = circle_image(12, rgb(GREEN if online else "#6b5a50"), rgb(GREEN if online else "#6b5a50"),
                              outline=(20, 14, 10, 255))
        card.image(x + d - 11, y + d - 11, dot, tags=tags)


def icon_circle(card: Card, x, y, glyph, d=34, command=None, tag=None, active=False):
    tag = tag or f"ic{glyph}{int(x)}{int(y)}"
    top, bot = (hl(26), hl(12)) if not active else PRIMARY
    img, _ = circle_image(d, top, bot, outline=hl(40))
    item = card.image(x, y, img, tags=(tag,))
    card.text(x + d / 2, y + d / 2 + 1, glyph, font=card.f.icon if card.f.icons else card.f.bold,
              fill=ON_ACCENT if active else TEXT, anchor="center", tags=(tag,))
    if command:
        from PIL import ImageTk

        himg, _ = circle_image(d, hl(52), hl(28), outline=acc(0.3, 90))
        hp = card.keep(photo_of(card, himg))
        base = card.itemcget(item, "image")
        card.on_click(tag, command, hover=lambda on: card.itemconfigure(item, image=hp if on else base))
    return tag


def theme_swatch(key: str, w: int, h: int):
    """A small preview of a colour theme: its glass, its glow and an accent button."""
    Image, ImageDraw, ImageFilter, _t, np = _pil()
    t = THEMES[key]
    ys = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    xs = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    base, tint, a = (np.array(t[k], np.float32) for k in ("base", "tint", "accent"))
    img = base * 0.35 + tint * 0.65 * (1 - 0.45 * ys) + 10 * (1 - xs) * (1 - ys)
    d = np.sqrt(((xs - 0.1) * w) ** 2 + ((ys - 1.15) * h) ** 2) / (0.75 * w)
    k = np.clip(1 - d, 0, 1) ** 2 * 0.75
    img = img * (1 - k) + a * k
    face = Image.fromarray(np.clip(np.repeat(img, 1, axis=0), 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    bw, bh = int(w * 0.42), max(8, int(h * 0.16))
    btn, _ = pill_image(bw, bh, tuple(_mix(t["accent"], (255, 255, 255), 0.3)), tuple(_mix(t["accent"], (0, 0, 0), 0.1)),
                        radius=bh // 2)
    face.alpha_composite(btn, (w - bw - int(w * 0.1), int(h * 0.16)))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(face, (0, 0), rounded_mask(w, h, 14))
    ol = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    ImageDraw.Draw(ol).rounded_rectangle([1, 1, w * 2 - 2, h * 2 - 2], radius=28, outline=t["hl"] + (70,), width=2)
    out.alpha_composite(ol.resize((w, h), Image.LANCZOS))
    return out


def well(card: Card, x0, y0, x1, y1, radius=16):
    """A flat, darker rounded well for a widget (table, input, map)."""
    img, _ = pill_image(int(x1 - x0), int(y1 - y0), rgb(WELL) + (235,), rgb(WELL) + (235,), radius=radius,
                        outline=hl(18))
    card.image(x0, y0, img)


def help_dot(card: Card, x, y, tip: str, tooltip):
    """A small "?" that explains something on hover."""
    tag = f"help{int(x)}{int(y)}"
    img, _ = circle_image(16, hl(30), hl(16))
    card.image(x, y, img, tags=(tag,))
    card.text(x + 8, y + 8, "?", font=card.f.cap, fill=MUTED, anchor="center", tags=(tag,))
    card.tag_bind(tag, "<Enter>", lambda e: tooltip.show(tip, e.x_root, e.y_root))
    card.tag_bind(tag, "<Leave>", lambda e: tooltip.hide())


class Tooltip:
    def __init__(self, root, fonts):
        self.root, self.f, self.win = root, fonts, None

    def show(self, text, x, y):
        self.hide()
        w = self.win = tk.Toplevel(self.root)
        w.overrideredirect(True)
        try:
            w.attributes("-topmost", True)
        except tk.TclError:
            pass
        w.configure(bg=WELL_LINE)
        tk.Label(w, text=text, bg=PANEL2, fg=TEXT, font=self.f.small, justify="left", wraplength=280,
                 padx=12, pady=8).pack(padx=1, pady=1)
        w.geometry(f"+{x + 14}+{y + 12}")

    def hide(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None


class Backdropped:
    """A canvas widget that draws the bit of glass behind it first, so it blends into a gradient card."""

    _bd = None

    def set_backdrop(self, pil):
        from PIL import ImageTk

        if pil.size[0] < 2 or pil.size[1] < 2:
            return
        self._bd = ImageTk.PhotoImage(pil)
        self._paint_now()

    def _draw_backdrop(self):
        if self._bd is not None:
            self.create_image(0, 0, image=self._bd, anchor="nw")

    def _paint_now(self):
        pass


class Tabs(Backdropped, tk.Canvas):
    """A pill tab bar on frosted glass: [ Home | Games | ... ], the picked one orange."""

    def __init__(self, parent, choices, value, command, fonts, bg=BASE, h=40, pad=18, radius=None):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=h)
        self.choices, self.value, self.command, self.f, self.h, self.pad = choices, value, command, fonts, h, pad
        self.radius = radius  # None: pill shaped
        self._imgs = []
        self._hover = None
        widths = [fonts.bold.measure(label) + 2 * pad for label, _v in choices]
        self.configure(width=sum(widths) + 8)
        self._spans = []
        x = 4
        for (label, v), w in zip(choices, widths):
            self._spans.append((x, x + w, v, label))
            x += w
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Configure>", lambda e: self.paint())

    def set(self, v):
        self.value = v
        self.paint()

    def _paint_now(self):
        self.paint()

    def _at(self, x):
        return next((v for x0, x1, v, _l in self._spans if x0 <= x < x1), None)

    def _motion(self, e):
        self._set_hover(self._at(e.x))

    def _set_hover(self, v):
        if v != self._hover:
            self._hover = v
            self.configure(cursor="hand2" if v is not None else "")
            self.paint()

    def _click(self, e):
        v = self._at(e.x)
        if v is not None and v != self.value:
            self.value = v
            self.paint()
            self.command(v)

    def paint(self):
        from PIL import ImageTk

        self.delete("all")
        self._imgs = []
        self._draw_backdrop()
        W = int(self.cget("width"))
        h = self.h
        r = self.radius
        bgimg, _ = pill_image(W, h, hl(26), hl(8), outline=hl(40), radius=r)
        p = photo_of(self, bgimg)
        self._imgs.append(p)
        self.create_image(0, 0, image=p, anchor="nw")
        for x0, x1, v, label in self._spans:
            on = v == self.value
            if on or v == self._hover:
                top, bot = PRIMARY if on else (hl(26), hl(14))
                img, _ = pill_image(int(x1 - x0), h - 8, top, bot, radius=None if r is None else max(4, r - 3),
                                    outline=acc(0.6, 80) if on else None)
                pp = photo_of(self, img)
                self._imgs.append(pp)
                self.create_image(x0, 4, image=pp, anchor="nw")
            self.create_text((x0 + x1) / 2, h / 2, text=label, font=self.f.bold,
                             fill=ON_ACCENT if on else (TEXT if v == self._hover else MUTED))


# ---- scrolling ------------------------------------------------------------------------------------------
class SlimScroll(tk.Canvas):
    def __init__(self, parent, target, bg=BASE):
        super().__init__(parent, width=10, bg=bg, highlightthickness=0, bd=0)
        self.target, self.first, self.last, self._drag = target, 0.0, 1.0, None
        self.bind("<Configure>", lambda e: self._paint())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: (setattr(self, "_drag", None), self._paint()))

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._paint()

    def _paint(self):
        self.delete("all")
        if self.last - self.first >= 0.999:
            return
        h = self.winfo_height()
        y0, y1 = self.first * h, max(self.first * h + 24, self.last * h)
        self.create_line(5, y0 + 4, 5, y1 - 4, fill="#4a3428" if self._drag is None else ACCENT, width=6, capstyle="round")

    def _press(self, e):
        h = max(1, self.winfo_height())
        if not (self.first * h <= e.y <= self.last * h):
            self.target.yview_moveto(max(0.0, e.y / h - (self.last - self.first) / 2))
        self._drag = (e.y, self.first)
        self._paint()

    def _move(self, e):
        if self._drag:
            y0, f0 = self._drag
            self.target.yview_moveto(max(0.0, f0 + (e.y - y0) / max(1, self.winfo_height())))


class ScrollSurface(tk.Frame):
    """A Surface taller than the window, scrolled with the wheel or a slim bar. Grid cards into .inner."""

    def __init__(self, parent):
        super().__init__(parent, bg=BASE)
        self.view = tk.Canvas(self, bg=BASE, highlightthickness=0, bd=0)
        self.bar = SlimScroll(self, self.view)
        self.view.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.view.pack(side="left", fill="both", expand=True)
        self.inner = Surface(self.view, view_h=lambda: self.view.winfo_height())
        self._win = self.view.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", lambda e: self.view.configure(scrollregion=(0, 0, e.width, e.height)), add="+")
        self.view.bind("<Configure>", lambda e: self.view.itemconfigure(self._win, width=e.width))
        self.view._wheel_target = True


# ---- inputs on glass ------------------------------------------------------------------------------------
class Slider(Backdropped, tk.Canvas):
    """get() / set(v); command(value as text) on every change, like tk.Scale."""

    def __init__(self, parent, from_, to, command=None, width=260, bg=BASE):
        super().__init__(parent, width=width, height=30, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.lo, self.hi, self.command, self.v = float(from_), float(to), command, float(from_)
        self._imgs = []
        self.bind("<Configure>", lambda e: self._paint())
        self.bind("<ButtonPress-1>", self._at)
        self.bind("<B1-Motion>", self._at)

    def get(self) -> float:
        return self.v

    def set(self, v):
        self.v = max(self.lo, min(self.hi, float(v)))
        self._paint()
        if self.command:
            self.command(str(self.v))

    def _at(self, e):
        w = max(1, self.winfo_width() - 20)
        self.set(self.lo + (self.hi - self.lo) * min(1.0, max(0.0, (e.x - 10) / w)))

    def _paint_now(self):
        self._paint()

    def _paint(self):
        from PIL import ImageTk

        self.delete("all")
        self._draw_backdrop()
        w, h = self.winfo_width(), self.winfo_height()
        if w < 30:
            return
        x = 12 + (w - 24) * (self.v - self.lo) / (self.hi - self.lo or 1)
        th = 8
        track, _ = pill_image(w - 16, th, hl(34), hl(20), outline=hl(30))
        fill, _ = pill_image(max(th, int(x - 8)), th, acc(0.32), acc(-0.05))
        knob, m = circle_image(20, (255, 252, 248), (236, 228, 222), outline=acc(0.15, 255), glow=acc(0, 120))
        self._imgs = [photo_of(self, i) for i in (track, fill, knob)]
        self.create_image(8, h / 2 - th / 2, image=self._imgs[0], anchor="nw")
        self.create_image(8, h / 2 - th / 2, image=self._imgs[1], anchor="nw")
        self.create_image(x - 10 - m, h / 2 - 10 - m, image=self._imgs[2], anchor="nw")


class Switch(Backdropped, tk.Canvas):
    """An on / off switch bound to a BooleanVar."""

    def __init__(self, parent, variable, command, bg=BASE):
        super().__init__(parent, width=46, height=26, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.var, self.command = variable, command
        self._imgs = []
        self.bind("<ButtonRelease-1>", self.flip)
        self.bind("<Configure>", lambda e: self._paint())

    def flip(self, e=None):
        self.var.set(not self.var.get())
        self._paint()
        self.command()

    def _paint_now(self):
        self._paint()

    def _paint(self):
        on = bool(self.var.get())
        self.delete("all")
        self._draw_backdrop()
        from PIL import ImageTk

        if on:
            track, _ = pill_image(44, 24, acc(0.3), acc(-0.08), outline=acc(0.6, 110))
            knob, _ = circle_image(18, (255, 252, 248), (238, 226, 216))
        else:
            track, _ = pill_image(44, 24, hl(40), hl(22), outline=hl(60))
            knob, _ = circle_image(18, (214, 200, 190), (180, 166, 156))
        self._imgs = [photo_of(self, track), photo_of(self, knob)]
        self.create_image(1, 1, image=self._imgs[0], anchor="nw")
        self.create_image(24 if on else 4, 4, image=self._imgs[1], anchor="nw")


class Button(Backdropped, tk.Canvas):
    """A smooth rounded button as its own widget (for places that aren't cards)."""

    def __init__(self, parent, text, command, fonts, kind="ghost", bg=BASE, h=32, pad=14, font=None):
        self.f, self.text, self.command, self.kind, self.h = fonts, text, command, kind, h
        self.font = font or fonts.bold
        w = self.font.measure(text) + 2 * pad
        super().__init__(parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._hover = False
        self._imgs = []
        self.bind("<Enter>", lambda e: self._set(True))
        self.bind("<Leave>", lambda e: self._set(False))
        self.bind("<ButtonRelease-1>", lambda e: self.command() if 0 <= e.x < self.winfo_width() else None)
        self.bind("<Configure>", lambda e: self._paint_now())

    def configure_text(self, text):
        self.text = text
        self.configure(width=self.font.measure(text) + 28)
        self._paint_now()

    def _set(self, on):
        self._hover = on
        self._paint_now()

    def _paint_now(self):
        from PIL import ImageTk

        self.delete("all")
        self._draw_backdrop()
        w, h = int(self.cget("width")), self.h
        prim = self.kind == "primary"
        top, bot = (PRIMARY_HOVER if self._hover else PRIMARY) if prim else (GHOST_HOVER if self._hover else GHOST)
        img, _ = pill_image(w, h, top, bot, radius=RADIUS,
                            outline=acc(0.6, 90) if prim else hl(44))
        self._imgs = [photo_of(self, img)]
        self.create_image(0, 0, image=self._imgs[0], anchor="nw")
        self.create_text(w / 2, h / 2, text=self.text, font=self.font, fill=ON_ACCENT if prim else TEXT)


@functools.lru_cache(maxsize=16)
def check_image(d, on):
    if on:
        return pill_image(d, d, acc(0.3), acc(-0.08), radius=6, outline=acc(0.6, 120))[0]
    return pill_image(d, d, hl(22), hl(12), radius=6, outline=hl(90))[0]


class ListView(tk.Frame):
    """A scrolling list for side panels. rows: [{"title", "sub", "right", "right_color", "title_color",
    "checked": True/False/None (None = no box), "data", "accent", "header": bool}].
    on_click(data) for a row, on_check(data, checked) for its box."""

    def __init__(self, parent, fonts, on_click=None, on_check=None, compact=False, bg=None, empty="Nothing here",
                 tiles=False):
        bg = bg or WELL
        super().__init__(parent, bg=bg)
        self.f, self.on_click, self.on_check, self.compact, self.bg, self.empty = fonts, on_click, on_check, compact, bg, empty
        self.tiles = tiles  # each row a rounded, clearly clickable tile with a View button
        self.rows: list[dict] = []
        self.selected = None
        self._hover = None
        self._imgs = []
        self.row_h = 30 if compact else (68 if tiles else 56)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = SlimScroll(self, self.canvas, bg=bg)
        self.canvas.configure(yscrollcommand=self._scrolled)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas._wheel_target = True
        self._drawn: set = set()
        self._row_y: dict = {}
        self._cw = 0
        self.canvas.bind("<Configure>", self._configured)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        self.canvas.bind("<ButtonRelease-1>", self._click)

    def set_rows(self, rows, selected=None):
        self.rows = list(rows)
        self.selected = selected
        self.draw()

    def _configured(self, e):
        if abs(e.width - self._cw) >= 2:
            self._cw = e.width
            self.draw()
        else:
            self._fill_view()

    def _scrolled(self, first, last):
        self.bar.set(first, last)
        self._fill_view()

    def _fill_view(self):
        c = self.canvas
        if not self._row_y:
            return
        top, h = c.canvasy(0), max(c.winfo_height(), 300)
        for i, y in self._row_y.items():
            if i not in self._drawn and top - h <= y <= top + 2 * h:
                self._draw_row(i)

    def _y(self, i):
        return sum(24 if r.get("header") else self.row_h for r in self.rows[:i])

    def _index_at(self, y):
        y = self.canvas.canvasy(y)
        acc = 0
        for i, r in enumerate(self.rows):
            h = 24 if r.get("header") else self.row_h
            if acc <= y < acc + h:
                return None if r.get("header") else i
            acc += h
        return None

    def draw(self):
        from PIL import ImageTk

        c = self.canvas
        c.delete("all")
        self._imgs = []
        W = max(120, c.winfo_width())
        if not self.rows:
            c.create_text(W / 2, 40, text=self.empty, fill=DIM, font=self.f.small, width=W - 30, justify="center")
            c.configure(scrollregion=(0, 0, W, 80))
            return
        y = 0
        self._box = photo_of(c, check_image(18, False)), photo_of(c, check_image(18, True))
        self._row_y = {}
        self._drawn = set()
        self._W = W
        for i, r in enumerate(self.rows):
            if r.get("header"):
                c.create_text(10, y + 14, text=r["title"].upper(), font=self.f.cap, fill=DIM, anchor="w")
                if r.get("right"):
                    c.create_text(W - 14, y + 14, text=r["right"].upper(), font=self.f.cap, fill=DIM, anchor="e")
                y += 24
                continue
            self._row_y[i] = y
            y += self.row_h
        c.configure(scrollregion=(0, 0, W, y))
        self._fill_view()

    def _draw_row(self, i):
        """Draws (or redraws) one row; its canvas items are tagged "r<i>"."""
        c, r, y, W, box = self.canvas, self.rows[i], self._row_y[i], self._W, self._box
        tag = f"r{i}"
        c.delete(tag)
        self._drawn.add(i)
        h = self.row_h
        if self.tiles:
            self._tile(c, i, r, y, W, box, tag)
            return
        t = (tag,)
        fill = ROW_SELECT if i == self.selected else (ROW_HOVER if i == self._hover else self.bg)
        c.create_rectangle(4, y + 2, W - 4, y + h - 2, fill=fill, outline="", tags=(f"bg{i}", tag))
        if r.get("accent"):
            c.create_line(6, y + 10, 6, y + h - 10, fill=r["accent"], width=3, capstyle="round", tags=t)
        x = 14
        if r.get("checked") is not None:
            c.create_image(x, y + h / 2 - 9, image=box[bool(r["checked"])], anchor="nw", tags=(f"box{i}", tag))
            if r["checked"]:
                c.create_text(x + 9, y + h / 2, text="✓", font=self.f.cap, fill=ON_ACCENT, tags=t)
            x += 30
        right = r.get("right")
        rw = self.f.bold.measure(str(right)) + 16 if right not in (None, "") else 0
        if self.compact:
            c.create_text(x, y + h / 2, text=self._fit(r["title"], self.f.small, W - x - rw - 10), anchor="w",
                          font=self.f.small, fill=r.get("title_color", TEXT), tags=t)
        else:
            c.create_text(x, y + h / 2 - 10, text=self._fit(r["title"], self.f.bold, W - x - rw - 10), anchor="w",
                          font=self.f.bold, fill=r.get("title_color", TEXT), tags=t)
            c.create_text(x, y + h / 2 + 11, text=self._fit(r.get("sub", ""), self.f.small, W - x - rw - 10),
                          anchor="w", font=self.f.small, fill=MUTED, tags=t)
        if rw:
            c.create_text(W - 14, y + h / 2, text=str(right), anchor="e", font=self.f.bold,
                          fill=r.get("right_color", ACCENT_SOFT), tags=t)

    def _redraw(self, *indexes):
        for i in indexes:
            if i is not None and i in self._drawn:
                self._draw_row(i)

    def update_row(self, i, **changes):
        """Changes one row and redraws just it (e.g. ticked, colour)."""
        if 0 <= i < len(self.rows):
            self.rows[i].update(changes)
            self._redraw(i)

    def _fit(self, text, font, width):
        return fit_text(text, font, width)

    def _tile(self, c, i, r, y, W, box, tag=None):
        h = self.row_h
        t = (tag,) if tag else ()
        sel, hov = i == self.selected, i == self._hover
        tw, th = W - 12, h - 8
        if sel:
            img, _ = pill_image(tw, th, acc(-0.55, 120), acc(-0.7, 90), radius=12, outline=acc(0.1, 230))
        elif hov:
            img, _ = pill_image(tw, th, hl(30), hl(16), radius=12, outline=acc(0.2, 150))
        else:
            img, _ = pill_image(tw, th, hl(16), hl(8), radius=12, outline=hl(34))
        p = photo_of(c, img)
        c.create_image(6, y + 4, image=p, anchor="nw", tags=t)
        x = 18
        mid = y + h / 2
        if r.get("checked") is not None:
            col = r.get("check_color") if r["checked"] else None
            if col:  # ticked: the box takes the game's colour on the map
                im, _ = pill_image(20, 20, tuple(_mix(col, (255, 255, 255), 0.2)), tuple(col), radius=6,
                                   outline=(255, 255, 255, 140))
                pb = photo_of(c, im)
                c.create_image(x, mid - 10, image=pb, anchor="nw", tags=t)
                c.create_text(x + 10, mid, text="✓", font=self.f.cap, fill="#111111", tags=t)
            else:
                c.create_image(x + 1, mid - 9, image=box[bool(r["checked"])], anchor="nw", tags=t)
                if r["checked"]:
                    c.create_text(x + 10, mid, text="✓", font=self.f.cap, fill=ON_ACCENT, tags=t)
            x += 34
        # right side: chest count and a View button (or "Viewing")
        label = "Viewing" if sel else "View"
        bw = self.f.small_bold.measure(label) + 22
        bx = W - 18 - bw
        if sel:
            bimg, _ = pill_image(bw, 26, acc(0.3), acc(-0.1), radius=8, outline=acc(0.6, 110))
            fg = ON_ACCENT
        else:
            bimg, _ = pill_image(bw, 26, hl(44 if hov else 26), hl(22 if hov else 12), radius=8, outline=hl(70))
            fg = TEXT
        pv = photo_of(c, bimg)
        c.create_image(bx, mid - 13, image=pv, anchor="nw", tags=t)
        c.create_text(bx + bw / 2, mid, text=label, font=self.f.small_bold, fill=fg, tags=t)
        right = r.get("right")
        rx = bx - 12
        if right not in (None, ""):
            c.create_text(rx, mid - 8, text=str(right), anchor="e", font=self.f.bold, fill=ACCENT_SOFT, tags=t)
            c.create_text(rx, mid + 10, text=r.get("right_sub", "chests"), anchor="e", font=self.f.cap, fill=DIM,
                          tags=t)
            rx -= max(self.f.bold.measure(str(right)), self.f.cap.measure(r.get("right_sub", "chests"))) + 12
        c.create_text(x, mid - 10, text=self._fit(r["title"], self.f.bold, rx - x), anchor="w", font=self.f.bold,
                      fill=r.get("title_color", TEXT), tags=t)
        c.create_text(x, mid + 11, text=self._fit(r.get("sub", ""), self.f.small, rx - x), anchor="w",
                      font=self.f.small, fill=MUTED, tags=t)

    def _set_hover(self, i):
        if i != self._hover:
            old, self._hover = self._hover, i
            self.canvas.configure(cursor="hand2" if i is not None else "")
            self._redraw(old, i)  # just the two rows that changed

    def _motion(self, e):
        self._set_hover(self._index_at(e.y))

    def _click(self, e):
        i = self._index_at(e.y)
        if i is None:
            return
        r = self.rows[i]
        if r.get("checked") is not None and e.x < 46 and self.on_check:
            r["checked"] = not r["checked"]
            self._redraw(i)
            self.on_check(r.get("data"), r["checked"])
            return
        old, self.selected = self.selected, i
        self._redraw(old, i)
        if self.on_click:
            self.on_click(r.get("data"))


def entry(parent, var, width=30, fonts=None):
    """A text box for a rounded well (drawn by the card around it)."""
    return tk.Entry(parent, textvariable=var, width=width, bg=WELL, fg=TEXT, insertbackground=ACCENT, relief="flat",
                    font=fonts.body if fonts else None, highlightthickness=0, bd=0, selectbackground=ROW_SELECT)


# ---- the table ------------------------------------------------------------------------------------------
class Table(tk.Frame):
    """A sortable, selectable list drawn on a canvas, for wells.

    columns: [{"title", "width", "anchor" ("w"/"e"/"center"), "stretch": bool, "nosort": bool, "help": text}]
    rows: [{"values": [...], "sort": [...], "colors": {i: colour}, "bold": {i...}, "accent": colour, "data": any,
            "badge": {i: kind}}]
    """

    def __init__(self, parent, fonts, columns, on_select=None, row_h=38, empty="Nothing here yet", scroll=True,
                 bg=WELL, max_rows=None, multi=False, on_marks=None):
        super().__init__(parent, bg=bg)
        self.f, self.columns, self.on_select, self.empty = fonts, columns, on_select, empty
        self.multi, self.on_marks, self.marked = multi, on_marks, set()
        self.rows: list[dict] = []
        self.row_h, self.bg, self.max_rows = row_h, bg, max_rows
        self.sort_col, self.sort_rev, self.selected, self._hover = None, False, None, None
        self._imgs = []
        self.head = tk.Canvas(self, height=30, bg=bg, highlightthickness=0, bd=0)
        self.head.pack(side="top", fill="x")
        body = tk.Frame(self, bg=bg)
        body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=bg, highlightthickness=0, bd=0)
        if scroll:
            self.bar = SlimScroll(body, self.canvas, bg=bg)
            self.canvas.configure(yscrollcommand=self._scrolled)
            self.bar.pack(side="right", fill="y")
            self.canvas._wheel_target = True
        self.canvas.pack(side="left", fill="both", expand=True)
        self._drawn: set = set()   # rows drawn so far (the rest are drawn when scrolled into view)
        self._widths_now = []
        self._cw = 0
        self._redraw_after = None
        self.canvas.bind("<Configure>", self._resized)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        self.canvas.bind("<ButtonRelease-1>", self._click)
        self.head.bind("<ButtonRelease-1>", self._head_click)
        if max_rows:
            self.canvas.configure(height=row_h * max_rows)

    def set_columns(self, columns):
        self.columns = columns
        self.sort_col = None
        self._draw()

    def set_rows(self, rows: list[dict]):
        self.rows = list(rows)
        self._sort()
        self.selected = None
        if self.marked:
            self.marked = set()
            if self.on_marks:
                self.on_marks([])
        self._draw()

    def marked_data(self) -> list:
        return [self.rows[i].get("data") for i in sorted(self.marked) if i < len(self.rows)]

    def selected_data(self):
        return self.rows[self.selected].get("data") if self.selected is not None and self.selected < len(self.rows) else None

    def select(self, i):
        self.selected = i if 0 <= i < len(self.rows) else None
        self._draw()
        if self.on_select:
            self.on_select(self.selected_data())

    def _sort(self):
        if self.sort_col is None or self.sort_col >= len(self.columns):
            return
        c = self.sort_col

        def key(r):
            v = (r.get("sort") or r["values"])[c]
            return (v is None, v if v is not None else 0)

        self.rows.sort(key=key, reverse=self.sort_rev)
        if self.sort_rev:
            self.rows.sort(key=lambda r: (r.get("sort") or r["values"])[c] is None)

    def _widths(self, total):
        fixed = sum(c["width"] for c in self.columns if not c.get("stretch"))
        stretch = [c for c in self.columns if c.get("stretch")]
        extra = max(0, total - fixed - 24)
        return [(max(c["width"], extra / len(stretch)) if c.get("stretch") else c["width"]) for c in self.columns]

    def _resized(self, e):
        if abs(e.width - self._cw) < 2:
            return
        self._cw = e.width
        if self._redraw_after:
            self.after_cancel(self._redraw_after)
        self._redraw_after = self.after(40, self._draw)

    def _fit(self, text, font, width):
        return fit_text(text, font, width)

    def _fill_of(self, r):
        if r == self.selected or r in self.marked:
            return ROW_SELECT
        return ROW_HOVER if r == self._hover else self.bg

    def _draw(self):
        self._redraw_after = None
        c, h = self.canvas, self.head
        c.delete("all")
        h.delete("all")
        self._imgs = []
        W = max(self.canvas.winfo_width(), 200)
        widths = self._widths(W)
        x = 16
        for i, (col, w) in enumerate(zip(self.columns, widths)):
            title = col["title"].upper()
            if self.sort_col == i:
                title += "  ↓" if self.sort_rev else "  ↑"
            anchor = col.get("anchor", "w")
            tx = x + (w - 12 if anchor == "e" else (w - 12) / 2 if anchor == "center" else 0)
            h.create_text(tx, 16, text=title, anchor={"w": "w", "e": "e", "center": "center"}[anchor],
                          fill=ACCENT_SOFT if self.sort_col == i else DIM, font=self.f.cap)
            col["_x"] = x
            x += w
        h.create_line(12, 29, W - 12, 29, fill=WELL_LINE)
        if not self.rows:
            c.create_text(W / 2, 44, text=self.empty, fill=DIM, font=self.f.body, width=W - 60, justify="center")
            c.configure(scrollregion=(0, 0, W, 90))
            return
        self._drawn = set()
        self._widths_now = widths
        self._W = W
        c.configure(scrollregion=(0, 0, W, len(self.rows) * self.row_h))
        self._fill_view()

    def _scrolled(self, first, last):
        self.bar.set(first, last)
        if self._drawn is not None and self.rows:
            self._fill_view()

    def _fill_view(self):
        """Draws the rows in view (and a screenful either side) that aren't drawn yet."""
        c, rh = self.canvas, self.row_h
        if not self.rows or not self._widths_now:
            return
        top = c.canvasy(0)
        h = max(c.winfo_height(), rh * (self.max_rows or 10))
        first = max(0, int((top - h) // rh))
        last = min(len(self.rows), int((top + 2 * h) // rh) + 1)
        for r in range(first, last):
            if r not in self._drawn:
                self._draw_row(r)

    def _draw_row(self, r):
        c, rh, W, widths, row = self.canvas, self.row_h, self._W, self._widths_now, self.rows[r]
        self._drawn.add(r)
        y = r * rh
        t = (f"row{r}",)
        c.create_rectangle(6, y + 2, W - 6, y + rh - 2, fill=self._fill_of(r), outline="", tags=(f"bg{r}", "bg") + t)
        if row.get("accent"):
            c.create_line(8, y + 10, 8, y + rh - 10, fill=row["accent"], width=3, capstyle="round", tags=t)
        colors, bold, badges = row.get("colors") or {}, row.get("bold") or (), row.get("badge") or {}
        for i, (col, w) in enumerate(zip(self.columns, widths)):
            anchor = col.get("anchor", "w")
            tx = col["_x"] + (w - 12 if anchor == "e" else (w - 12) / 2 if anchor == "center" else 0)
            if i in badges and row["values"][i] not in ("", None):
                self._badge(c, tx, y + rh / 2, str(row["values"][i]), badges[i], anchor, t)
                continue
            font = self.f.bold if i in bold else self.f.body
            text = self._fit(row["values"][i], font, w - 14)
            c.create_text(tx, y + rh / 2, text=text, anchor={"w": "w", "e": "e", "center": "center"}[anchor],
                          fill=colors.get(i, TEXT), font=font, tags=t)

    def _badge(self, c, x, y, text, kind, anchor, tags=()):
        fg = CHIPS[kind][2]
        w, hh = self.f.small_bold.measure(text) + 18, 22
        x0 = x - (w if anchor == "e" else w / 2 if anchor == "center" else 0)
        p = photo_of(c, chip_image(w, hh, kind))
        c.create_image(x0, y - hh / 2, image=p, anchor="nw", tags=tags)
        c.create_text(x0 + w / 2, y, text=text, fill=fg, font=self.f.small_bold, tags=tags)

    def _row_at(self, y):
        r = int(self.canvas.canvasy(y) // self.row_h)
        return r if 0 <= r < len(self.rows) else None

    def _paint_row(self, r):
        if r is None or r not in self._drawn:
            return
        self.canvas.itemconfigure(f"bg{r}", fill=self._fill_of(r))

    def _set_hover(self, r):
        if r != self._hover:
            old, self._hover = self._hover, r
            self._paint_row(old)
            self._paint_row(r)
            self.canvas.configure(cursor="hand2" if r is not None else "")

    def _motion(self, e):
        self._set_hover(self._row_at(e.y) if self.on_select else None)

    def _click(self, e):
        r = self._row_at(e.y)
        if r is None or not self.on_select:
            return
        ctrl, shift = bool(e.state & 0x4), bool(e.state & 0x1)
        before = set(self.marked)
        if self.multi and (ctrl or shift) and self.selected is not None:
            if shift:
                lo, hi = sorted((self.selected, r))
                self.marked |= set(range(lo, hi + 1))
            else:
                self.marked.add(self.selected)
                self.marked ^= {r}
        else:
            self.marked = set()
        old, self.selected = self.selected, r
        for i in before | self.marked | {old, r}:
            self._paint_row(i)
        self.on_select(self.selected_data())
        if self.on_marks and (before or self.marked):
            self.on_marks(self.marked_data())

    def _head_click(self, e):
        for i, col in enumerate(self.columns):
            if col.get("_x", 0) <= e.x and (i + 1 == len(self.columns) or e.x < self.columns[i + 1].get("_x", 1e9)):
                if col.get("nosort"):
                    return
                if self.sort_col == i:
                    self.sort_rev = not self.sort_rev
                else:
                    self.sort_col, self.sort_rev = i, col.get("anchor") == "e"
                sel = self.selected_data()
                marked = [self.rows[k].get("data") for k in self.marked]
                self._sort()
                self.selected = next((k for k, r in enumerate(self.rows) if sel is not None and r.get("data") is sel), None)
                self.marked = {k for k, r in enumerate(self.rows) if any(r.get("data") is m for m in marked)}
                self._draw()
                return


# ---- ttk (the map's controls, the match browser, dialogs) -----------------------------------------------
def apply_theme(root):
    """Dark warm ttk styling (clam based), so ttk windows match the app. Done once per Tk root."""
    from tkinter import ttk

    if getattr(root, "_ct_themed", None) == _THEME_KEY:
        return
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        return
    root._ct_themed = _THEME_KEY
    HOV, SEL, LINE = _hex(_mix(_B, _T, 0.8)), _hex(_mix(_B, _A, 0.24)), _hex(_mix(_B, _T, 0.95))
    base = ("Segoe UI", 9)
    st.configure(".", background=PANEL, foreground=TEXT, fieldbackground=WELL, bordercolor=LINE, lightcolor=PANEL,
                 darkcolor=PANEL, troughcolor=WELL, focuscolor=PANEL, selectbackground=SEL, selectforeground=TEXT,
                 insertcolor=TEXT, arrowcolor=MUTED, font=base)
    st.map(".", foreground=[("disabled", DIM)])
    st.configure("TFrame", background=PANEL)
    st.configure("TLabel", background=PANEL, foreground=TEXT)
    st.configure("TButton", background=PANEL2, foreground=TEXT, padding=(10, 3), borderwidth=1, relief="flat")
    st.map("TButton", background=[("pressed", SEL), ("active", HOV)], bordercolor=[("active", ACCENT_LO)])
    for w in ("TCheckbutton", "TRadiobutton"):
        st.configure(w, background=PANEL, foreground=TEXT, indicatorbackground=WELL, indicatorforeground=ACCENT,
                     indicatorcolor=WELL, upperbordercolor=LINE, lowerbordercolor=LINE)
        st.map(w, background=[("active", PANEL)], indicatorcolor=[("selected", ACCENT)],
               indicatorbackground=[("selected", ACCENT)])
    st.configure("TCombobox", fieldbackground=WELL, background=PANEL2, foreground=TEXT, arrowcolor=MUTED, padding=3)
    st.map("TCombobox", fieldbackground=[("readonly", WELL)], foreground=[("readonly", TEXT)],
           selectbackground=[("readonly", WELL)], selectforeground=[("readonly", TEXT)], background=[("active", HOV)])
    root.option_add("*TCombobox*Listbox.background", WELL)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SEL)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", base)
    root.option_add("*Toplevel.background", BASE)
    root.option_add("*Menu.background", PANEL2)
    root.option_add("*Menu.foreground", TEXT)
    root.option_add("*Menu.activeBackground", SEL)
    root.option_add("*Menu.activeForeground", TEXT)
    root.option_add("*Menu.relief", "flat")
    st.configure("TEntry", fieldbackground=WELL, foreground=TEXT)
    st.configure("Horizontal.TScale", background=ACCENT, troughcolor=LINE, bordercolor=PANEL, lightcolor=ACCENT,
                 darkcolor=ACCENT)
    st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=24, bordercolor=LINE)
    st.map("Treeview", background=[("selected", SEL)], foreground=[("selected", TEXT)])
    st.configure("Treeview.Heading", background=PANEL2, foreground=MUTED, relief="flat", bordercolor=LINE)
    st.map("Treeview.Heading", background=[("active", HOV)])
    st.configure("TNotebook", background=BASE, bordercolor=LINE, tabmargins=(0, 4, 0, 0))
    st.configure("TNotebook.Tab", background=PANEL2, foreground=MUTED, padding=(14, 5), bordercolor=LINE)
    st.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", ACCENT_SOFT)])
    for o in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
        st.configure(o, background=PANEL2, troughcolor=PANEL, arrowcolor=MUTED, bordercolor=PANEL,
                     lightcolor=PANEL2, darkcolor=PANEL2)
        st.map(o, background=[("pressed", SEL), ("active", HOV)], arrowcolor=[("active", TEXT)])
