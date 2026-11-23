"""The in-game overlay in the app window's look (app_theme.py): tinted glass with the theme's glow, the app's
font, status chips and avatars. A drop-in for chest_tracker.Painter: same size rules and the same draw().

Like the classic painter it returns one RGBA picture; the overlay window shows it with per-pixel
transparency, so the "Background" slider (opacity) fades the glass while the numbers stay solid.
"""
from __future__ import annotations

from pathlib import Path

import app_theme as T

APP_DIR = Path(__file__).resolve().parent


TIMER_LATE = (255, 64, 56)  # the match timer at 14 minutes: leave and requeue (Nov 2026 squad analysis)

CLASSIC_FONTS = ["impact.ttf", "seguibl.ttf", "ariblk.ttf", "DejaVuSans-Bold.ttf"]  # the classic overlay's fonts


def _font(size: int):
    """The classic overlay's font: one dropped in the app's "fonts" folder (e.g. Burbank), else Impact."""
    import os

    from PIL import ImageFont

    try:
        custom = next(iter(sorted(p for p in (APP_DIR / "fonts").iterdir() if p.suffix.lower() in (".ttf", ".otf"))), None)
        if custom:
            return ImageFont.truetype(str(custom), size)
    except (OSError, ValueError):
        pass
    for n in CLASSIC_FONTS:
        for folder in (Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/usr/share/fonts/truetype/dejavu")):
            if (folder / n).exists():
                try:
                    return ImageFont.truetype(str(folder / n), size)
                except OSError:
                    pass
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


class GlassPainter:
    """Draws the overlay as a transparent (RGBA) image (drop-in for chest_tracker.Painter)."""

    PARTY_HEAD, PARTY_ROW = 30, 24  # expanded table: header and row height (unscaled)

    def __init__(self, scale: float = 1.0, theme: str | None = None):
        if theme:
            T.use_theme(theme)
        self.s = scale = max(0.5, min(3.0, float(scale)))
        self.W, self.H = int(272 * scale), int(92 * scale)  # (no chest picture or caption: smaller than the classic one)
        self.big = _font(int(60 * scale))
        self.num = _font(int(21 * scale))
        self.lab = _font(int(12 * scale))
        self.msg = _font(int(14 * scale))
        self.name = _font(int(14 * scale))
        # the theme's colours, taken now (the app rebuilds the painter when the theme changes)
        self.base, self.tint, self.accent = T.rgb(T.BASE), T.GLASS_TINT, T.ACCENT_RGB
        self.text, self.muted, self.dim = T.rgb(T.TEXT), T.rgb(T.MUTED), T.rgb(T.DIM)
        self.soft = T.rgb(T.ACCENT_SOFT)
        self.hl = T.hl(0)[:3]
        self.green = T.rgb(T.GREEN)
        self._panels: dict = {}

    def party_height(self, n: int) -> int:
        return int((self.PARTY_HEAD + self.PARTY_ROW * n + 12) * self.s) if n else 0

    # ---- the glass ----------------------------------------------------------------------------------
    def _panel(self, opacity: float, highlight: bool, H: int):
        """Rounded glass: a tinted gradient, a glow in the theme's colour, a sheen and a soft border.
        Never fully transparent, so it can still be clicked."""
        import numpy as np
        from PIL import Image, ImageDraw

        key = (round(opacity, 2), highlight, H)
        if key in self._panels:
            return self._panels[key]
        W, s, ss = self.W, self.s, 3
        r = int(20 * s)
        ys = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
        xs = np.linspace(0, 1, W, dtype=np.float32)[None, :, None]
        top = np.array(T._mix(self.base, self.tint, 0.95), np.float32)
        bot = np.array(T._mix(self.base, self.tint, 0.5), np.float32)
        rgb = top * (1 - ys) + bot * ys
        # the theme's glow, low on the left behind the chest, and a little light at the top
        main_h = min(H, self.H)
        d = np.sqrt(((xs - 0.12) * W) ** 2 + ((ys - (main_h * 0.95) / H) * H) ** 2) / (0.55 * W)
        k = np.clip(1 - d, 0, 1) ** 2 * 0.75
        rgb = rgb * (1 - k) + np.array(self.accent, np.float32) * k
        rgb += 255 * 0.10 * np.clip(1 - ys * H / (main_h * 0.55), 0, 1) ** 2
        alpha = np.full((H, W, 1), 255 * opacity, np.float32)
        alpha = np.maximum(alpha, k * 255 * min(1.0, opacity * 1.6) * 0.8)  # the glow stays visible a while longer
        face = Image.fromarray(np.clip(np.concatenate([rgb, alpha], axis=2), 0, 255).astype(np.uint8), "RGBA")
        mask = Image.new("L", (W * ss, H * ss), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, W * ss - 1, H * ss - 1], radius=r * ss, fill=255)
        mask = mask.resize((W, H), Image.LANCZOS)
        a = np.asarray(face.getchannel("A"), np.float32) * np.asarray(mask, np.float32) / 255
        face.putalpha(Image.fromarray(np.maximum(a, np.asarray(mask, np.float32) > 0).astype(np.uint8)))
        # border: brighter along the top; the theme's colour while being dragged
        edge = Image.new("L", (W * ss, H * ss), 0)
        bw = int(max(1, (2.2 if highlight else 1.2) * s) * ss)
        ImageDraw.Draw(edge).rounded_rectangle([0, 0, W * ss - 1, H * ss - 1], radius=r * ss, outline=255, width=bw)
        edge = np.asarray(edge.resize((W, H), Image.LANCZOS), np.float32)
        if highlight:
            col, strength = self.accent, np.full((H, W), 1.0, np.float32)
        else:
            col = self.hl
            strength = (0.30 + 0.45 * (1 - ys[..., 0])) * min(1.0, 0.35 + opacity)
        border = np.zeros((H, W, 4), np.float32)
        border[..., :3] = col
        border[..., 3] = edge * strength
        out = Image.alpha_composite(face, Image.fromarray(border.astype(np.uint8), "RGBA"))
        self._panels = {key: out}
        return out

    def _chip(self, d, x, y, text, kind, font, anchor="lm"):
        """A small status chip (Checked, FINAL...) like the app's; returns its width."""
        top, bot, fg = T.CHIPS[kind]
        w = int(d.textlength(text, font=font) + 14 * self.s)
        h = int(19 * self.s)
        x0 = x - w if anchor.startswith("r") else x
        img, _ = T.pill_image(w, h, top, bot, radius=int(6 * self.s))
        self._stickers.append((img, (int(x0), int(y - h / 2))))
        d.text((x0 + w / 2, y), text, font=font, fill=T.rgb(fg) if isinstance(fg, str) else fg, anchor="mm")
        return w

    def _text(self, d, xy, text, font, fill, anchor):
        d.text(xy, text, font=font, fill=fill, anchor=anchor)

    # ---- draw ---------------------------------------------------------------------------------------
    def draw(self, mine: int, rows, flash: float = 0.0, message: str = "", confirmed: bool = False,
             opacity: float = 0.85, highlight: bool = False, party=None, final: bool = False,
             timer: str = "", late: bool = False):
        """rows: [(label, value)] shown on the right. party: players for the expanded table (see
        Team.players), or None when collapsed. final: the match is over. timer: the match clock, tiny in the
        top-left corner (red once late: time to leave). Returns an RGBA image."""
        from PIL import Image, ImageDraw, ImageFilter

        s, W, H = self.s, self.W, self.H
        full_h = H + self.party_height(len(party or []))
        img = self._panel(opacity, highlight, full_h).copy()
        self._stickers = []
        see_through = opacity < 0.55
        label_col = self.muted if see_through else self.dim

        text = Image.new("RGBA", (W, full_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(text)
        x0 = int(18 * s)
        # the big number (this game's chests), FINAL over it once the match is over
        num = f"{mine:,}"
        ny = H // 2 + int(2 * s)

        self._text(d, (x0, ny), num, self.big, self.soft if flash > 0 else self.text, "lm")
        if final:  # the match is over: FINAL, just right of the number
            self._text(d, (x0 + d.textlength(num, font=self.big) + int(6 * s), ny - int(14 * s)), "FINAL", self.lab,
                       self.soft, "lm")
        if timer:
            self._text(d, (x0 + int(2 * s), int(11 * s)), timer, self.lab, TIMER_LATE if late else label_col, "lm")
        if confirmed:  # a green tick: this game's count was checked against its replay
            nx = x0 + d.textlength(num, font=self.big) + int(5 * s)
            ty = ny + int(12 * s)
            d.line([(nx, ty), (nx + 5 * s, ty + 5 * s), (nx + 14 * s, ty - 7 * s)], fill=self.green,
                   width=max(2, int(3 * s)))

        rx = W - int(16 * s)
        if message:
            # status text takes the place of the small stats for a few seconds
            box_l = x0 + max(int(60 * s), int(d.textlength(num, font=self.big) + 22 * s))
            width = rx - box_l - int(10 * s)
            words, lines, cur = message.split(), [], ""
            for w in words:
                trial = (cur + " " + w).strip()
                if d.textlength(trial, font=self.msg) > width and cur:
                    lines.append(cur)
                    cur = w
                else:
                    cur = trial
            lines.append(cur)
            lines = lines[:4]
            lh = int(16 * s)
            bh = lh * len(lines) + int(12 * s)
            by = (H - bh) // 2
            box, _ = T.pill_image(rx - box_l + int(6 * s), bh, T.acc(-0.25, 150), T.acc(-0.45, 150), radius=int(10 * s),
                                  outline=T.acc(0.3, 140))
            self._stickers.append((box, (box_l - int(6 * s), by)))
            y = by + int(6 * s)
            for ln in lines:
                self._text(d, (rx - int(4 * s), y), ln, self.msg, self.text, "ra")
                y += lh
        else:
            # the big number is this game already: don't repeat it on the right
            rows = [(lb, v) for lb, v in rows if not (str(lb).upper() == "THIS GAME" and v == mine)] or list(rows)
            step = int((25 if len(rows) >= 3 else 30) * s)
            top = (H - step * (len(rows) - 1)) // 2
            for i, (label, val) in enumerate(rows):
                y = top + i * step
                v = f"{val:,}" if isinstance(val, int) else str(val)
                self._text(d, (rx, y), v, self.num, self.text, "rm")
                vw = d.textlength(v, font=self.num)
                self._text(d, (rx - vw - int(7 * s), y + int(1 * s)), str(label).upper(), self.lab, label_col, "rm")

        if party:
            lx, gx = int(16 * s), W - int(70 * s)
            gx, tx = gx - int(52 * s), gx  # columns: player, game, today, total (smallest to biggest)
            y = H + int(2 * s)
            sep = Image.new("RGBA", (W - int(32 * s), max(1, int(s))), self.hl + (60,))
            self._stickers.append((sep, (int(16 * s), H)))
            self._text(d, (lx, y + int(14 * s)), "PLAYER", self.lab, label_col, "lm")
            self._text(d, (tx, y + int(14 * s)), "TODAY", self.lab, label_col, "rm")
            self._text(d, (gx, y + int(14 * s)), "GAME", self.lab, label_col, "rm")
            self._text(d, (rx, y + int(14 * s)), "TOTAL", self.lab, label_col, "rm")
            y += int(self.PARTY_HEAD * s)
            for p in party:
                cy = y + int(self.PARTY_ROW * s / 2) - int(2 * s)
                name = str(p.get("name") or "?")
                dot = max(5, int(8 * s))  # online (green) or not
                dcol = self.green if p.get("online") else (120, 120, 120)
                dc, _ = T.circle_image(dot, dcol, dcol)
                self._stickers.append((dc, (lx, cy - dot // 2)))
                col = self.soft if p.get("me") else self.text
                nx = lx + dot + int(8 * s)
                shown = name
                while shown and d.textlength(shown, font=self.name) > gx - nx - int(40 * s):
                    shown = shown[:-1]
                if shown != name:
                    shown = shown[:-1] + "…"
                self._text(d, (nx, cy), shown, self.name, col, "lm")
                today = p.get("today")
                self._text(d, (tx, cy), "–" if today is None else f"{today:,}", self.name, col, "rm")
                self._text(d, (gx, cy), str(p["game"]) if p.get("online") else "–", self.name, col, "rm")
                self._text(d, (rx, cy), f"{p['total']:,}", self.name, col, "rm")
                y += int(self.PARTY_ROW * s)

        for sticker, pos in self._stickers:  # chips, boxes and avatars go under the text
            img.alpha_composite(sticker, pos)
        # a drop shadow like the classic overlay: the text's shape, blurred, darker and a little down-right;
        # doubled over a see-through background, where the game shows behind the text
        a = text.getchannel("A").filter(ImageFilter.GaussianBlur(max(1.0, 1.4 * s)))
        strength = 0.95 if see_through else 0.75
        shadow = Image.new("RGBA", text.size, (0, 0, 0, 0))
        shadow.paste((0, 0, 0, 255), (max(1, round(1.5 * s)), max(1, round(2.5 * s))),
                     a.point(lambda v: min(255, int(v * strength * 1.3))))
        if see_through:
            shadow = Image.alpha_composite(shadow, shadow)
        if flash > 0:  # a new chest: the number glows in the theme's colour
            glow_a = text.getchannel("A").crop((x0 - 4, 0, x0 + int(d.textlength(num, font=self.big)) + 8, H))
            g = Image.new("RGBA", text.size, (0, 0, 0, 0))
            g.paste(self.accent + (255,), (x0 - 4, 0), glow_a.filter(ImageFilter.GaussianBlur(6 * s)).point(lambda v: v * 0.8))
            img = Image.alpha_composite(img, g)
        return Image.alpha_composite(Image.alpha_composite(img, shadow), text)
