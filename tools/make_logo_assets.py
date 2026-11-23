"""Makes the app's pictures from the logo (assets/logo_source.png, the FCT chest on black).

    python tools/make_logo_assets.py

  chest.png            the logo on a see-through background (the glow kept): window header, classic overlay
  assets/logo.png      the same, bigger (README, share card, setup window)
  assets/icon.png      the logo on a dark rounded tile, readable on light and dark taskbars (window icon)
  assets/icon.ico      the tile at 16-256 px (shortcuts, an installer)
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

APP = Path(__file__).resolve().parents[1]
SRC = APP / "assets" / "logo_source.png"


def transparent(src: Image.Image) -> Image.Image:
    """The logo was drawn on black: brightness becomes opacity, and the colour is divided back out, so the
    glow stays a glow on any dark background."""
    a = np.asarray(src.convert("RGB")).astype(np.float32) / 255.0
    floor = np.percentile(a.max(axis=2), 5)  # the background's own (near) black
    alpha = np.clip((a.max(axis=2) - floor) / (1.0 - floor), 0.0, 1.0)
    rgb = np.clip((a - floor) / (1.0 - floor), 0.0, 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        rgb = np.where(alpha[..., None] > 1e-4, rgb / alpha[..., None], 0.0)
    out = np.dstack([np.clip(rgb, 0, 1), alpha]) * 255.0
    return Image.fromarray(out.round().astype(np.uint8), "RGBA")


def square_crop(img: Image.Image, pad: float = 0.04) -> Image.Image:
    alpha = np.asarray(img)[..., 3]
    ys, xs = np.nonzero(alpha > 10)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    side = int(max(x1 - x0, y1 - y0) * (1 + 2 * pad))
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img.crop((cx - side // 2, cy - side // 2, cx - side // 2 + side, cy - side // 2 + side)), (0, 0))
    return canvas


def tile(logo: Image.Image, size: int = 512) -> Image.Image:
    """The logo on a dark rounded square with a soft cyan edge."""
    k = 4
    big = size * k
    bg = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.22), fill=255)
    grad = np.linspace(0, 1, big, dtype=np.float32)[:, None]
    top, bottom = np.array([14, 26, 48], np.float32), np.array([4, 7, 16], np.float32)
    col = (top * (1 - grad) + bottom * grad)[:, None, :].repeat(big, axis=1).reshape(big, big, 3)
    fill = Image.fromarray(col.astype(np.uint8), "RGB").convert("RGBA")
    bg.paste(fill, (0, 0), mask)
    edge = Image.new("L", (big, big), 0)
    w = max(2, big // 90)
    ImageDraw.Draw(edge).rounded_rectangle((w, w, big - 1 - w, big - 1 - w), radius=int(big * 0.22) - w,
                                           outline=255, width=w)
    ring = Image.new("RGBA", (big, big), (70, 200, 255, 0))
    ring.putalpha(edge.point(lambda v: v * 110 // 255))
    bg = Image.alpha_composite(bg, ring)
    inner = int(big * 1.0)
    lg = logo.resize((inner, inner), Image.LANCZOS)
    bg.alpha_composite(lg, ((big - inner) // 2, (big - inner) // 2))
    return bg.resize((size, size), Image.LANCZOS)


def main():
    logo = square_crop(transparent(Image.open(SRC)))
    logo.resize((400, 400), Image.LANCZOS).save(APP / "assets" / "logo.png", optimize=True)
    logo.resize((384, 384), Image.LANCZOS).save(APP / "chest.png", optimize=True)
    icon = tile(logo, 256)
    icon.save(APP / "assets" / "icon.png", optimize=True)
    icon.save(APP / "assets" / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("made chest.png, assets/logo.png, assets/icon.png, assets/icon.ico")


if __name__ == "__main__":
    main()
