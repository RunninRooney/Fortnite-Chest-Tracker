""""Share my stats": a picture of your season for Discord (1200 x 675, the size Discord previews in full).

make_card(info) draws it; share(info) also copies it to the clipboard (paste it into Discord with Ctrl+V) and
saves it in Pictures\\Chest Tracker. Only your own numbers and the name you chose: no team code, no squadmates'
or other players' names.
"""
from __future__ import annotations

import io
import os
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 675
APP_DIR = Path(__file__).resolve().parent
LOGO = APP_DIR / "assets" / "logo.png"
FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
CYAN, BLUE, WHITE, DIM = (63, 200, 255), (40, 110, 230), (240, 248, 255), (143, 164, 194)


def _font(names, size):
    for n in names:
        for path in (FONTS / n, Path(n)):
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


HEAVY = ["impact.ttf", "seguibl.ttf", "ariblk.ttf", "DejaVuSans-Bold.ttf"]
BOLD = ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]
REG = ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]


def fmt_playtime(seconds) -> str:
    m = int(round((seconds or 0) / 60))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m" if m else "-"


def _background() -> Image.Image:
    y = Image.linear_gradient("L").resize((W, H))
    top, bottom = Image.new("RGB", (W, H), (10, 20, 40)), Image.new("RGB", (W, H), (3, 6, 14))
    bg = Image.composite(bottom, top, y).convert("RGBA")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    d.ellipse((-260, -320, 560, 420), fill=(40, 140, 255, 90))
    d.ellipse((760, 330, 1500, 980), fill=(63, 200, 255, 55))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
    return bg


def _tile(d: ImageDraw.ImageDraw, box, value: str, label: str):
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, radius=22, fill=(14, 24, 42, 225), outline=(40, 70, 110, 255), width=2)
    vf = _font(HEAVY, 54)
    while d.textlength(value, font=vf) > (x1 - x0) - 40 and vf.size > 26:
        vf = _font(HEAVY, vf.size - 4)
    d.text((x0 + 24, y0 + 20), value, font=vf, fill=WHITE)
    d.text((x0 + 26, y1 - 44), label.upper(), font=_font(BOLD, 19), fill=DIM)


def make_card(info: dict) -> Image.Image:
    """info: name, total, games, per_min, best, today, play_s, treasures (all optional but name/total)."""
    img = _background()
    d = ImageDraw.Draw(img)
    try:
        logo = Image.open(LOGO).convert("RGBA").resize((190, 190), Image.LANCZOS)
        img.alpha_composite(logo, (40, 22))
    except OSError:
        pass
    d.text((240, 62), "CHEST TRACKER", font=_font(HEAVY, 40), fill=CYAN)
    name = str(info.get("name") or "Player")[:20]
    d.text((240, 110), name, font=_font(BOLD, 44), fill=WHITE)
    d.text((W - 56, 70), time.strftime("%b %d, %Y", time.localtime(info.get("when") or time.time())).replace(" 0", " "),
           font=_font(REG, 26), fill=DIM, anchor="ra")

    total = f"{int(info.get('total') or 0):,}"
    big = _font(HEAVY, 160)
    d.text((56, 212), total, font=big, fill=WHITE)
    tw = d.textlength(total, font=big)
    d.text((74 + tw, 232), "CHESTS", font=_font(HEAVY, 64), fill=CYAN)
    d.text((76 + tw, 304), "this season", font=_font(REG, 30), fill=DIM)

    per = info.get("per_min") or 0
    tiles = [(f"{int(info.get('games') or 0):,}", "games"),
             (f"{per:.1f}" if per else "-", "chests a minute"),
             (f"{int(info.get('best') or 0):,}", "best game"),
             (fmt_playtime(info.get("play_s")), "played")]
    if info.get("treasures"):
        tiles[3:3] = [(f"{int(info['treasures']):,}", "treasures")]
    n = len(tiles)
    gap, left, right, top, bottom = 18, 56, W - 56, 452, 618
    tw_ = (right - left - gap * (n - 1)) / n
    for i, (v, lab) in enumerate(tiles):
        x0 = left + i * (tw_ + gap)
        _tile(d, (int(x0), top, int(x0 + tw_), bottom), v, lab)
    d.text((W - 56, H - 30), "counted with Chest Tracker", font=_font(REG, 20), fill=(110, 130, 160), anchor="rb")
    return img.convert("RGB")


def save(img: Image.Image, folder: Path | None = None) -> Path:
    folder = folder or Path.home() / "Pictures" / "Chest Tracker"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime("stats-%Y-%m-%d-%H%M%S.png")
    img.save(path, optimize=True)
    return path


def copy_to_clipboard(img: Image.Image) -> bool:
    """Puts the picture on the Windows clipboard (as PNG and as a bitmap), ready to paste into Discord."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype, k32.GlobalAlloc.argtypes = wintypes.HGLOBAL, [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype, k32.GlobalLock.argtypes = wintypes.LPVOID, [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.SetClipboardData.restype, u32.SetClipboardData.argtypes = wintypes.HANDLE, [wintypes.UINT, wintypes.HANDLE]
    u32.RegisterClipboardFormatW.restype, u32.RegisterClipboardFormatW.argtypes = wintypes.UINT, [wintypes.LPCWSTR]

    def handle(data: bytes):
        h = k32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        return h

    bmp, png = io.BytesIO(), io.BytesIO()
    img.convert("RGB").save(bmp, "BMP")
    img.save(png, "PNG")
    for _ in range(5):  # (another program can hold the clipboard for a moment)
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        u32.EmptyClipboard()
        ok = bool(u32.SetClipboardData(8, handle(bmp.getvalue()[14:])))  # CF_DIB: the bitmap without its file header
        fmt = u32.RegisterClipboardFormatW("PNG")
        if fmt:
            u32.SetClipboardData(fmt, handle(png.getvalue()))
        return ok
    finally:
        u32.CloseClipboard()


def info_from(summary: dict, name: str, total: int) -> dict:
    """The card's numbers from app_model.summary over all games."""
    return {"name": name, "total": total, "games": summary.get("games"), "per_min": summary.get("per_min"),
            "best": summary.get("best"), "play_s": summary.get("play_s"), "treasures": summary.get("treasures"),
            "today": summary.get("today")}
