"""
Where am I on the map? Found from the minimap only (screen capture, like the chest pop-up reader).

The minimap in Fortnite's top-right corner is a north-up crop of the same island art that
fortnite-api.com serves as the full map, drawn at about 1:1 with its 2048 px image. So finding the
minimap's centre (the player arrow) on the full map is a plain template match at a fixed scale:

  * the minimap is a square 0.232 x the window height, 0.031 x height from the right edge and
    0.027 x height from the top (measured at 2560x1032, 2560x1080 and 1280x720);
  * it always spans MINIMAP_MAP_PX pixels of the 2048 px map, whatever the screen resolution;
  * the storm tints it purple and makes it see-through, and lines (path to the safe zone, storm
    circle), icons and the place name are drawn over it. Matching uses edge structure (a high-pass
    image) with normalized correlation, which a tint only scales, and masks out bright lines, the
    icon strip and the place name;
  * the first match searches the whole map; after that only near the last position. Weak or
    ambiguous matches (open water, fields) are rejected and the last position is kept.

Positions are map pixels on the 2048 px map; map_to_world / world_to_map convert to the game's
world units (cm) used by replays.

Nothing here touches the game: it only looks at pictures of the screen.
"""
from __future__ import annotations

import json
import math
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("chest.map")

MAP_SIZE = 2048
MINIMAP_MAP_PX = 239.0  # the minimap's width measured on the 2048 px map (fixed zoom)
# Minimap square as fractions of the Fortnite window's height.
MM_SIZE, MM_RIGHT, MM_TOP = 0.232, 0.031, 0.027
# Part of the minimap used for matching (fractions of its side): skips the icon strip at the top and
# the place name along the bottom.
INNER = (0.05, 0.12, 0.95, 0.80)  # left, top, right, bottom

# World units (cm) -> 2048 px map: px = X * SCALE + OX, py = Y * SCALE + OY (x right = +X, down = +Y).
# Fitted from 400 minimap matches against replay positions (Nov 19, 2026; 1 map px = 1.46 m). Refitted
# after matches automatically when the map changes (see fit_transform).
DEFAULT_TRANSFORM = {"scale": 0.0068307, "ox": 964.87, "oy": 1016.82}

API_URL = "https://fortnite-api.com/v1/map"


def world_to_map(x: float, y: float, tf: dict = DEFAULT_TRANSFORM) -> tuple[float, float]:
    return x * tf["scale"] + tf["ox"], y * tf["scale"] + tf["oy"]


def map_to_world(px: float, py: float, tf: dict = DEFAULT_TRANSFORM) -> tuple[float, float]:
    return (px - tf["ox"]) / tf["scale"], (py - tf["oy"]) / tf["scale"]


def minimap_box(win_w: int, win_h: int) -> tuple[int, int, int]:
    """(left, top, size) of the minimap inside a window of this size (at the default HUD scale)."""
    s = round(MM_SIZE * win_h)
    return win_w - round(MM_RIGHT * win_h) - s, round(MM_TOP * win_h), s


# The minimap's size follows Fortnite's HUD scale setting, so it differs between PCs (Nov 19, 2026: 0.232 of
# the window's height on one, 0.175 on another). When it stops matching, find_minimap looks for it.
FIND_SIDES = (0.12, 0.32)            # minimap side to try, fractions of the window height
FIND_RIGHT, FIND_TOP = 0.135, 0.125  # first guess at its gaps to the window's right and top, fractions of the side
FIND_REGION = 0.5                    # the window's top-right corner searched: this much of its height, square
FIND_AFTER = 15.0     # seconds of tracking with no trusted minimap match before looking for the minimap
FIND_EVERY = 90.0     # then at most this often (doubling up to FIND_MAX while it isn't found)
FIND_MAX = 600.0


def _box_value(region, matcher, l: int, t: int, s: int) -> float:
    """How well a square crop of the corner matches the map (score + margin / 2; -1 = not at all)."""
    h, w = region.shape[:2]
    if s < 40 or l < 0 or t < 0 or l + s > w or t + s > h:
        return -1.0
    f = matcher.locate(region[t:t + s, l:l + s], None)
    return -1.0 if f is None else f.score + 0.5 * f.margin


def _square_edges(region, l: int, t: int, s: int) -> tuple[int, int, int] | None:
    """The minimap's exact square from its straight edges near (l, t, s). Matching the map alone can't tell:
    a crop a few px off still matches (the match just moves), and the player is at the true centre."""
    import cv2
    import numpy as np

    g = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = g.shape
    r = max(12, round(0.12 * s))
    a, b = max(0, t + round(0.25 * s)), min(h, t + round(0.75 * s))  # rows surely inside the minimap
    c, d = max(0, l + round(0.25 * s)), min(w, l + round(0.75 * s))  # columns surely inside
    if b - a < 10 or d - c < 10:
        return None
    dx = np.abs(np.diff(g, axis=1)) > 12  # dx[:, x]: a step between columns x and x + 1
    dy = np.abs(np.diff(g, axis=0)) > 12
    cols = dx[a:b].mean(axis=0)
    rows = dy[:, c:d].mean(axis=1)

    def best(strength, lo, hi):
        lo, hi = max(0, lo), min(len(strength), hi)
        if hi <= lo:
            return None, 0.0
        i = lo + int(np.argmax(strength[lo:hi]))
        return i, float(strength[i])

    left, sl = best(cols, l - r, l + r)
    right, sr = best(cols, l + s - r, l + s + r)
    top, st = best(rows, t - r, t + r)
    bottom, sb = best(rows, t + s - r, t + s + r)
    if None in (left, right, top, bottom) or min(sl, sr, st, sb) < 0.4:
        return None  # no clear straight edge all round
    side_w, side_h = right - left, bottom - top
    if abs(side_w - side_h) > max(3, 0.03 * s) or abs((side_w + side_h) / 2 - s) > max(6, 0.06 * s):
        return None  # not a square of about that size
    return left + 1, top + 1, round((side_w + side_h) / 2)


def find_minimap(region, matcher) -> tuple[int, int, int, float] | None:
    """Finds the minimap in the top-right corner of Fortnite's window (`region`: that corner, BGR, its right
    edge the window's): (left, top, side, score) in region px, or None. The side: the size whose crop matches
    the map best (a scan, then finer steps); the exact place: the square's straight edges. Takes 1-3 s."""
    h = region.shape[0] / FIND_REGION  # the window's height
    w = region.shape[1]
    best_v, best = -1.0, None
    s = FIND_SIDES[0] * h
    while s <= FIND_SIDES[1] * h:
        si = round(s)
        gap, top = round(FIND_RIGHT * si), round(FIND_TOP * si)
        v = _box_value(region, matcher, w - gap - si, top, si)
        if v > best_v:
            best_v, best = v, (gap, top, si)
        s *= 1.04
    if best is None or best_v < 0.3:
        return None
    gap, top, si = best
    for step in (4, 2, 1):  # finer: side, top, right gap
        better = True
        while better:
            better = False
            for dg, dt, ds in ((0, 0, step), (0, 0, -step), (0, step, 0), (0, -step, 0), (step, 0, 0), (-step, 0, 0)):
                c = (gap + dg, top + dt, si + ds)
                v = _box_value(region, matcher, w - c[0] - c[2], c[1], c[2])
                if v > best_v:
                    best_v, (gap, top, si), better = v, c, True
    exact = _square_edges(region, w - gap - si, top, si)
    if exact is None:
        return None
    l, t, s = exact
    gap = w - l - s
    if not (0.03 * s <= gap <= 0.35 * s and 0.03 * s <= t <= 0.35 * s):
        return None  # not where the minimap sits (the big map open, say)
    f = matcher.locate(region[t:t + s, l:l + s], None)
    if f is None or not f.ok:
        return None
    return l, t, s, round(f.score, 2)


# --------------------------------------------------------------------------------------------
# Map image
# --------------------------------------------------------------------------------------------
def fetch_map(dest_dir: Path, timeout: float = 20, download: bool = True) -> Path | None:
    """Downloads the current map image (2048 px, no labels) from fortnite-api.com when it changed.
    No personal information is sent. download=False (offline mode): only the copy already on this PC.
    Returns the image path, or None if there is none."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    img, meta = dest_dir / "map.png", dest_dir / "map.json"
    if not download:
        return img if img.exists() else None
    try:
        req = urllib.request.Request(API_URL, headers={"User-Agent": "ChestTracker"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)["data"]
        url = data["images"]["blank"]
        old = json.loads(meta.read_text()) if meta.exists() else {}
        req = urllib.request.Request(url, headers={"User-Agent": "ChestTracker"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            stamp = r.headers.get("Last-Modified") or r.headers.get("ETag") or ""
            if img.exists() and stamp and old.get("stamp") == stamp:
                return img
            body = r.read()
        tmp = img.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(img)
        meta.write_text(json.dumps({"stamp": stamp, "pois": data.get("pois", []), "fetched": time.time()}))
        log.info("downloaded the map image (%s)", stamp or "no date")
    except Exception as e:
        log.info("map download failed: %s", e)
    return img if img.exists() else None


# --------------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------------
def structure(img_bgr, sigma: float = 6.0):
    """High-pass grey image: edges and texture, without overall colour or brightness. The storm's
    purple tint blends the minimap toward one colour, which only scales this, so normalized
    correlation still matches."""
    import cv2
    import numpy as np

    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = cv2.GaussianBlur(g, (0, 0), 0.8)
    return g - cv2.GaussianBlur(g, (0, 0), sigma)


def overlay_mask(mm_bgr):
    """255 where the minimap shows the island, 0 where something is drawn over it: bright white
    lines and icons, the player arrow, and very dark outlines of text."""
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(mm_bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    b, g, r = cv2.split(mm_bgr.astype(np.int16))
    white = (s < 50) & (v > 200)
    path_blue = (b > 180) & (r < 90) & (g < 90)  # the blue route line to the safe zone
    dark = v < 25
    bad = (white | path_blue | dark).astype(np.uint8)
    n = mm_bgr.shape[0]
    cv2.circle(bad, (n // 2, n // 2), max(3, n // 16), 1, -1)  # the player arrow
    bad = cv2.dilate(bad, np.ones((5, 5), np.uint8))
    return np.where(bad > 0, 0, 255).astype(np.uint8)


def storm_fraction(mm_bgr) -> float:
    """Share of the minimap tinted purple by the storm."""
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(mm_bgr, cv2.COLOR_BGR2HSV)
    h, s = hsv[:, :, 0], hsv[:, :, 1]
    return float(np.mean((h >= 125) & (h <= 160) & (s > 90)))


def island_box(map_bgr) -> tuple[int, int, int, int] | None:
    """Where the island is in the map picture (left, top, right, bottom in map px). The art isn't centred
    (this season it sits right of and below the middle), so "whole island" fits this box. Land is
    anything that isn't open water or a cloud."""
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(map_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    water = (h >= 88) & (h <= 130) & (s > 60)
    cloud = (s < 45) & (v > 190)
    land = cv2.morphologyEx((~water & ~cloud).astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    ys, xs = np.nonzero(land)
    if len(xs) < 1000:
        return None
    x0, x1 = np.percentile(xs, [0.2, 99.8])
    y0, y1 = np.percentile(ys, [0.2, 99.8])
    return int(x0), int(y0), int(x1), int(y1)


def minimap_visible(mm_bgr) -> bool:
    """The minimap's island art is bright (median brightness 113+ in real matches, storm included);
    Fortnite's menus over that corner are dark (about 55)."""
    import cv2
    import numpy as np

    n = mm_bgr.shape[0]
    l, t, r, b = INNER
    v = cv2.cvtColor(mm_bgr[int(t * n):int(b * n), int(l * n):int(r * n)], cv2.COLOR_BGR2HSV)[:, :, 2]
    return float(np.median(v)) >= 85


def fortnite_client_rect() -> dict | None:
    """Screen rectangle of the inside of Fortnite's window (without a title bar and borders in
    windowed mode), found by window class and title only. None if it isn't open."""
    import ctypes
    import sys
    from ctypes import wintypes

    if sys.platform != "win32":
        return None
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        cls, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(128)
        u.GetClassNameW(h, cls, 64)
        if cls.value == "UnrealWindow" and u.IsWindowVisible(h) and not u.IsIconic(h):
            u.GetWindowTextW(h, title, 128)
            if title.value.strip().lower().startswith("fortnite"):
                r = wintypes.RECT()
                u.GetClientRect(h, ctypes.byref(r))
                pt = wintypes.POINT(0, 0)
                u.ClientToScreen(h, ctypes.byref(pt))
                if r.right > 200 and r.bottom > 200:
                    found.append({"left": pt.x, "top": pt.y, "width": r.right, "height": r.bottom})
                    return False
        return True

    u.EnumWindows(visit, 0)
    return found[0] if found else None


@dataclass
class Fix:
    x: float           # map px (2048 map) of the minimap centre = the player
    y: float
    score: float       # normalized correlation of the best match
    margin: float      # best score minus the best score away from it (how unambiguous)
    ok: bool           # trusted (score and margin high enough)
    full: bool         # whole-map search (False: near the last position)
    storm: float       # share of the minimap in the storm


class MinimapMatcher:
    MIN_SCORE = 0.30         # near the last position
    MIN_MARGIN = 0.06
    FULL_MIN_SCORE = 0.50    # whole map (measured: real matches 0.75+, margin 0.6+; a false one 0.32 / 0.06)
    FULL_MIN_MARGIN = 0.15
    NEAR = 70          # local search radius in map px (1 px = 1.46 m: about 100 m, a fast vehicle for 1-2 s)
    COARSE = 4         # whole-map search at 1/4 size first

    def __init__(self, map_bgr):
        import cv2

        self.map = map_bgr
        self.map_s = structure(map_bgr)
        small = cv2.resize(map_bgr, (map_bgr.shape[1] // self.COARSE, map_bgr.shape[0] // self.COARSE),
                           interpolation=cv2.INTER_AREA)
        self.small_s = structure(small, sigma=6.0 / self.COARSE * 2)

    def _template(self, mm_bgr, scale_to: float):
        """Inner part of the minimap, its structure image and mask, resized so the whole minimap is
        `scale_to` px wide; plus the offset of the template's top-left from the minimap centre."""
        import cv2
        import numpy as np

        n = mm_bgr.shape[0]
        l, t, r, b = INNER
        x0, y0, x1, y1 = int(l * n), int(t * n), int(r * n), int(b * n)
        k = scale_to / n
        size = (max(8, round((x1 - x0) * k)), max(8, round((y1 - y0) * k)))
        st = structure(mm_bgr, sigma=6.0 * n / MINIMAP_MAP_PX)[y0:y1, x0:x1]
        mask = overlay_mask(mm_bgr)[y0:y1, x0:x1]
        st = cv2.resize(st, size, interpolation=cv2.INTER_AREA)
        mask = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
        return st.astype(np.float32), mask, ((l - 0.5) * scale_to, (t - 0.5) * scale_to)

    @staticmethod
    def _peak(res, exclude: int):
        """Best value and location, and the best value at least `exclude` px away from it."""
        import cv2
        import numpy as np

        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, best, _, loc = cv2.minMaxLoc(res)
        other = res.copy()
        x, y = loc
        other[max(0, y - exclude):y + exclude + 1, max(0, x - exclude):x + exclude + 1] = -1
        return best, loc, float(other.max()) if other.size else -1.0

    def locate(self, mm_bgr, near: tuple[float, float] | None = None) -> Fix | None:
        """Finds the minimap (a square BGR crop of it) on the map. `near`: last position (map px)."""
        import cv2

        if not minimap_visible(mm_bgr):
            return None  # a menu, the settings, a loading screen...
        storm = storm_fraction(mm_bgr)
        tpl, mask, off = self._template(mm_bgr, MINIMAP_MAP_PX)
        if mask.mean() < 255 * 0.25:
            return None  # almost everything is covered
        th, tw = tpl.shape
        full = near is None
        if full:
            q = self.COARSE
            stpl, smask, soff = self._template(mm_bgr, MINIMAP_MAP_PX / q)
            res = cv2.matchTemplate(self.small_s, stpl, cv2.TM_CCOEFF_NORMED, mask=smask)
            v, loc, _ = self._peak(res, 6)
            cx, cy = (loc[0] - soff[0]) * q, (loc[1] - soff[1]) * q
            radius = 3 * q
        else:
            cx, cy = near
            radius = self.NEAR
        # local search: a window around (cx, cy) big enough for the template moved by +-radius
        x0 = int(round(cx + off[0])) - radius
        y0 = int(round(cy + off[1])) - radius
        x0c, y0c = max(0, x0), max(0, y0)
        x1c = min(self.map_s.shape[1], x0 + tw + 2 * radius)
        y1c = min(self.map_s.shape[0], y0 + th + 2 * radius)
        if x1c - x0c < tw or y1c - y0c < th:
            return None
        win = self.map_s[y0c:y1c, x0c:x1c]
        res = cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
        best, loc, second = self._peak(res, 12)
        x = x0c + loc[0] - off[0]
        y = y0c + loc[1] - off[1]
        margin = best - max(second, 0.0)
        if full:  # anywhere on the island: only a clear, unambiguous match counts
            ok = best >= self.FULL_MIN_SCORE and margin >= self.FULL_MIN_MARGIN
        else:
            ok = best >= self.MIN_SCORE and margin >= self.MIN_MARGIN
        return Fix(float(x), float(y), float(best), float(margin), bool(ok), full, storm)


class Tracker:
    """Keeps the player's position from minimap pictures: whole-map search when lost, local search
    otherwise; untrusted matches keep the last position."""
    LOST_AFTER = 8   # seconds without a trusted match before searching the whole map again

    def __init__(self, matcher: MinimapMatcher):
        self.m = matcher
        self.pos: tuple[float, float] | None = None
        self.last_ok = 0.0

    def reset(self):
        self.pos = None
        self.last_ok = 0.0

    def update(self, mm_bgr, now: float) -> Fix | None:
        near = self.pos if self.pos is not None and now - self.last_ok < self.LOST_AFTER else None
        fix = self.m.locate(mm_bgr, near)
        if fix is None and near is not None:
            return None
        if fix is not None and not fix.ok and near is not None and now - self.last_ok > 3:
            # repeatedly weak near the last spot: maybe teleported (respawn, launch pad): try everywhere
            wide = self.m.locate(mm_bgr, None)
            if wide is not None and wide.ok:
                fix = wide
        if fix is not None and fix.ok:
            self.pos = (fix.x, fix.y)
            self.last_ok = now
        return fix


# --------------------------------------------------------------------------------------------
# World <-> map conversion, refitted from the player's own matches
# --------------------------------------------------------------------------------------------
def fit_transform(pairs) -> tuple[dict, float, int] | None:
    """pairs: [((X, Y) world, (px, py) map)]. Least squares for px = s*X + ox, py = s*Y + oy, dropping
    outliers (moments of fast travel, where screen and replay clocks disagree).
    Returns ({scale, ox, oy}, median residual in px, pairs used) or None."""
    import numpy as np

    if len(pairs) < 20:
        return None
    A = np.array([w for w, _ in pairs], float)
    P = np.array([p for _, p in pairs], float)
    n = len(A)
    M = np.zeros((2 * n, 3))
    M[:n, 0], M[:n, 1] = A[:, 0], 1
    M[n:, 0], M[n:, 2] = A[:, 1], 1
    y = np.concatenate([P[:, 0], P[:, 1]])
    keep = np.ones(2 * n, bool)
    sol = None
    for _ in range(3):
        sol, *_ = np.linalg.lstsq(M[keep], y[keep], rcond=None)
        res = np.abs(M @ sol - y)
        keep = res < max(3.0, 3 * float(np.median(res[keep])))
        if keep.sum() < 20:
            return None
    if not (0.004 < sol[0] < 0.012):
        return None
    res = np.abs(M @ sol - y)[keep]
    return {"scale": float(sol[0]), "ox": float(sol[1]), "oy": float(sol[2])}, float(np.median(res)), int(keep.sum() // 2)


def route_lookup(route, t: float, max_gap: float = 3.0):
    """Position (X, Y) on a replay route [[t, x, y, ...], ...] at time t (linear), or None."""
    import bisect

    if not route:
        return None
    times = [p[0] for p in route]
    i = bisect.bisect_left(times, t)
    if i <= 0 or i >= len(route):
        return None
    a, b = route[i - 1], route[i]
    if b[0] - a[0] > max_gap:
        return None
    w = (t - a[0]) / max(1e-6, b[0] - a[0])
    return a[1] + (b[1] - a[1]) * w, a[2] + (b[2] - a[2]) * w


def fit_from_match(live_points, replay_start: float, route) -> tuple[dict, float, int] | None:
    """Fits the world->map transform from one match: live trusted fixes [(epoch, px, py)] and the
    replay route of the same player. The screen clock and replay time can differ by a second or two,
    so a few offsets are tried."""
    best = None
    for off in [x * 0.25 for x in range(-12, 13)]:
        pairs = []
        for e, px, py in live_points:
            w = route_lookup(route, e - replay_start + off)
            if w is not None:
                pairs.append((w, (px, py)))
        got = fit_transform(pairs)
        if got and (best is None or got[1] < best[1]):
            best = got
    return best


# --------------------------------------------------------------------------------------------
# Chest spots: map-placed chests keep their actor name across matches
# --------------------------------------------------------------------------------------------
def season_of(branch: str | None) -> str:
    """'++Fortnite+Release-43.10' -> '43'. Chest spots are kept per season (minor updates keep them)."""
    import re

    m = re.search(r"Release-(\d+)", branch or "")
    return m.group(1) if m else "unknown"


class SpotDB:
    """Known chest spots per season: {season: {actor name: {x, y, n, cls, actor}}} in world units.
    Positions from the chest's own actor are exact; ones from where its opener stood are averaged."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            self.data = {}
        self.version = 0

    def save(self):
        # (the map worker and the chest counter's replay check both learn spots: a temp file of its own each,
        # retried when Windows refuses the rename for a moment; never raises)
        with self.lock:
            text = json.dumps(self.data, separators=(",", ":"))
            save_atomic(self.path, text)

    def add(self, season: str, spot_id: str, cls: str, x: float, y: float, source: str):
        with self.lock:
            s = self.data.setdefault(season, {})
            cur = s.get(spot_id)
            if cur is None or (source == "actor" and not cur.get("actor")):
                s[spot_id] = {"x": round(x), "y": round(y), "n": 1, "cls": cls, "actor": source == "actor"}
            elif not cur.get("actor"):
                n = min(cur["n"], 30)  # running mean that slowly forgets very old samples
                if ((x - cur["x"]) ** 2 + (y - cur["y"]) ** 2) ** 0.5 < 1500:  # an opener >15 m off: ignore
                    cur["x"] = round((cur["x"] * n + x) / (n + 1))
                    cur["y"] = round((cur["y"] * n + y) / (n + 1))
                    cur["n"] = cur["n"] + 1
            self.version += 1

    def spots(self, season: str) -> dict:
        with self.lock:
            return dict(self.data.get(season, {}))

    def all_spots(self) -> dict[str, tuple[float, float]]:
        """Every known chest spot by id, all seasons merged (as chest_tracker.load_chest_spots)."""
        with self.lock:
            return {cid: (float(v["x"]), float(v["y"])) for season in self.data.values() if isinstance(season, dict)
                    for cid, v in season.items() if isinstance(v, dict) and "x" in v and "y" in v}

    def nearest(self, season: str, x: float, y: float, max_dist: float = 600) -> tuple[str, dict] | None:
        """The known chest spot closest to world (x, y), within max_dist (cm), or None."""
        best = None
        for sid, s in self.spots(season).items():
            d = ((s["x"] - x) ** 2 + (s["y"] - y) ** 2) ** 0.5
            if d <= max_dist and (best is None or d < best[0]):
                best = (d, sid, s)
        return (best[1], best[2]) if best else None

    def seasons(self) -> list[str]:
        with self.lock:
            return sorted(self.data, reverse=True)


# --------------------------------------------------------------------------------------------
# Heatmaps from past matches
# --------------------------------------------------------------------------------------------
HEAT_KINDS = {"routes": "Routes (paths)", "time": "Movement (time spent)", "landing": "Landing spots",
              "chests": "Chests opened", "kills": "Eliminations"}


def heat_points(matches, kind: str, who, season: str | None, tf: dict = DEFAULT_TRANSFORM):
    """Points (map px x, y, weight) for a heatmap. matches: replay map records (see ReplayMaps);
    who(name, is_recorder) -> bool picks the players; season None = every season."""
    pts = []
    for m in matches:
        if season and m.get("season") != season:
            continue
        players = m["map"].get("players", [])
        names = {p.get("name") for p in players if who(p.get("name"), p.get("recorder"))}
        for p in players:
            if p.get("name") not in names:
                continue
            if kind == "landing" and p.get("landing"):
                x, y = world_to_map(p["landing"][1], p["landing"][2], tf)
                pts.append((x, y, 1.0))
            elif kind == "kills":
                for k in p.get("kills", []):
                    k = kill_info(k)
                    if k["approx"]:
                        continue  # a far-away teammate's late, batched count: not where it happened
                    # where the eliminated player went down when known, else where the shooter stood
                    x, y = world_to_map(*(k["victim_at"] or k["at"]), tf)
                    pts.append((x, y, 1.0))
            elif kind == "time":
                route = p.get("route", [])
                for a, b in zip(route, route[1:]):
                    if b[0] - a[0] > 5 or a[3] > 20000:  # gaps, and the bus / high skydiving
                        continue
                    x, y = world_to_map(a[1], a[2], tf)
                    pts.append((x, y, b[0] - a[0]))
        if kind == "chests":
            for c in m["map"].get("chests", []):
                if c.get("who") in names and c.get("source") != "search":  # position heatmap: exact spots only
                    x, y = world_to_map(c["x"], c["y"], tf)
                    pts.append((x, y, 1.0))
    return pts


def replay_totals(e: dict | None) -> dict[str, tuple[int, int]]:
    """{player name: (opened, broken)} for one match from its replays.json entry: the recorder's raw numbers and
    every teammate the reader saw (the same as app_model.replay_counts). The whole match's real numbers."""
    out: dict[str, tuple[int, int]] = {}
    if not e:
        return out
    raw = e.get("raw") or {}
    rec = e.get("recorder") or raw.get("recorder_name")
    if rec:
        out[rec] = (int(raw.get("opened") or 0), int(raw.get("broken") or 0))
    for t in e.get("teammates") or []:
        if t.get("name"):
            out[t["name"]] = (int(t.get("opened") or 0), int(t.get("broken") or 0))
    return out


def chest_total(rec: dict, name) -> tuple[int, int] | None:
    """A player's whole-game (opened, broken) from the replay log (attached by MapManager), or None."""
    return (rec.get("_totals") or {}).get(name)


def chests_so_far(rec: dict, name, T: float | None = None) -> tuple[int, int]:
    """(opened, broken) for a player: the whole game's real numbers (replay log) when T is None, else the map's
    markers up to T, never more than that total."""
    total = chest_total(rec, name)
    if T is None and total is not None:
        return total
    marks = [c for c in rec["map"].get("chests", []) if c.get("who") == name and (T is None or c["t"] <= T)]
    opened = sum(1 for c in marks if not c.get("broken"))
    broken = len(marks) - opened
    if total is not None:
        opened, broken = min(opened, total[0]), min(broken, total[1])
    return opened, broken


SAME_CHEST_M = 6.0  # a search and a credited chest this close (metres) are the same chest (as chest_tracker)


def tidy_chests(rec: dict, spots: dict | None = None) -> dict:
    """Pairs a teammate's credited chests with their far-away searches, in memory (the saved record keeps
    the reader's output). Same rule as chest_tracker.squad_counts, so the map and the counts agree.

    A chest is credited to its opener when its update reaches the recorder, which for a far-away teammate can
    be minutes after they opened it; the reader then lists it where they stood at that later moment. Their
    search for it was seen at the time. So, in time order, each credited chest (opened, not broken) takes the
    nearest unused search by the same player made no later than 1 s after it, within SAME_CHEST_M of the
    chest's real spot (known chest spots, or its own spawn point for "actor"); with no known spot, only a
    search 0-6 s before it. A pair becomes one chest: at the search's time, at the chest's real spot. Credited
    chests left over are opens the search flag missed: kept, moved to their real spot when known.
    spots: {id: (x, y)} or {id: {"x", "y"}}. Returns rec."""
    chests = rec.get("map", {}).get("chests")
    if not chests or rec.get("_tidy") is not None:
        return rec
    spots = spots or {}

    def spot(c):
        if c.get("source") == "actor":
            return c["x"], c["y"]
        v = spots.get(c.get("id") or "")
        if isinstance(v, dict):
            return (v["x"], v["y"]) if "x" in v and "y" in v else None
        return (v[0], v[1]) if v else None

    free: dict[str, list] = {}
    for c in chests:
        if c.get("source") == "search":
            free.setdefault(c.get("who"), []).append(c)
    replace, drop, pairs = {}, set(), 0
    credited = sorted((c for c in chests if c.get("source") in ("opener", "actor") and not c.get("broken")
                       and free.get(c.get("who"))), key=lambda c: c.get("t", 0))
    for c in credited:
        mine = free.get(c.get("who")) or []
        where = spot(c)
        pick = None
        if where is not None:
            near = [(math.hypot(sc["x"] - where[0], sc["y"] - where[1]) / 100, i)
                    for i, sc in enumerate(mine) if sc.get("t", 0) <= c.get("t", 0) + 1]
            near = [x for x in near if x[0] <= SAME_CHEST_M]
            pick = min(near)[1] if near else None
        else:
            near = [i for i, sc in enumerate(mine) if -1 <= c.get("t", 0) - sc.get("t", 0) <= 6]
            pick = near[-1] if near else None
        if pick is not None:
            sc = mine.pop(pick)
            x, y = where if where is not None else (sc["x"], sc["y"])
            replace[id(sc)] = dict(c, t=sc["t"], x=x, y=y, paired=True)  # one chest: its time, its real spot
            drop.add(id(c))
            pairs += 1
        elif where is not None and math.hypot(c["x"] - where[0], c["y"] - where[1]) > 300:
            replace[id(c)] = dict(c, x=where[0], y=where[1], late=True)  # listed where they stood later
    out = [replace.get(id(c), c) for c in chests if id(c) not in drop]
    out.sort(key=lambda c: c.get("t", 0))
    rec["map"]["chests"] = out
    rec["_tidy"] = pairs
    return rec


def kill_info(k) -> dict:
    """A kill from a replay record as {t, at: (x, y), victim_at: (x, y) | None, victim}. Accepts the
    current form ({t, x, y, vx, vy, victim}) and the first one ([t, x, y])."""
    if isinstance(k, dict):
        v = (k["vx"], k["vy"]) if k.get("vx") is not None and k.get("vy") is not None else None
        return {"t": k.get("t", 0), "at": (k["x"], k["y"]), "victim_at": v, "victim": k.get("victim"),
                "approx": bool(k.get("approx"))}
    return {"t": k[0], "at": (k[1], k[2]), "victim_at": None, "victim": None, "approx": False}


def render_heat(points, size: int = MAP_SIZE, cell: int = 4, blur_px: float = 10.0):
    """RGBA heat layer (PIL image, size x size) from (x, y, weight) points in map px."""
    import cv2
    import numpy as np
    from PIL import Image

    n = size // cell
    grid = np.zeros((n, n), np.float32)
    for x, y, w in points:
        i, j = int(y // cell), int(x // cell)
        if 0 <= i < n and 0 <= j < n:
            grid[i, j] += w
    if grid.max() <= 0:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grid = cv2.GaussianBlur(grid, (0, 0), max(0.5, blur_px / cell))
    top = float(np.percentile(grid[grid > 0], 99.5)) or float(grid.max())
    # square root: places visited a few times still show next to the few you go to all the time
    v = np.sqrt(np.clip(grid / top, 0, 1))
    color = cv2.applyColorMap((v * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)[:, :, ::-1]
    alpha = np.where(v < 0.08, 0, np.clip(v * 1.5, 0, 1) * 215).astype(np.uint8)
    return Image.fromarray(np.dstack([color, alpha]), "RGBA").resize((size, size), Image.BILINEAR)


# --------------------------------------------------------------------------------------------
# Runtime: live tracking during matches, replay maps after them, the squad's live positions
# --------------------------------------------------------------------------------------------
TEAM_EVERY = 3.0       # seconds between position messages to the squad (only during matches)
HIST_EVERY = 15.0      # seconds between position messages that also carry this match's route so far (web page)
JUMP_PX = 150          # a fix this far from the last one starts a new route piece (spectating, respawn)
SNAP_CM = 600          # live chest dots move to a known chest spot within 6 m


def save_atomic(path: Path, text: str, tries: int = 6) -> bool:
    """Writes a file safely on Windows: a temporary file of its own (unique name), then a rename over the
    real one, retried a few times when the rename is refused (antivirus or the search indexer can hold a
    file for a moment). Never raises: False (logged) when it couldn't, the caller saves again later."""
    import os
    import secrets

    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}-{secrets.token_hex(3)}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text)
        for i in range(tries):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                if i == tries - 1:
                    raise
                time.sleep(0.05 * (i + 1))  # 50 ms, 100 ms, ... about 0.75 s in all
    except OSError as e:
        log.info("map: couldn't save %s (%s); will retry", path.name, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


class MapManager:
    """Owns everything the map window shows. Threads write, the window reads (under self.lock).

    app is chest_tracker.App (match_in_progress(), stats.game, stats.id, cfg, team.key, replays).
    Helpers from chest_tracker are passed in (window_rect, in_front) so this module never imports it.
    """

    def __init__(self, app, data_dir: Path, demos: Path, reader: Path, window_rect, in_front):
        self.app = app
        self.dir = data_dir / "map"
        self.maps_dir = data_dir / "maps"
        self.demos = demos
        self.reader = reader
        self.window_rect = window_rect
        self.in_front = in_front
        self.lock = threading.Lock()
        self.version = 0
        self.status = "starting"
        self.map_path: Path | None = None
        self.display_path: Path | None = None  # a sharper picture lined up with the map, if one was given
        self.island: tuple[int, int, int, int] | None = None  # the island's box in map px (see island_box)
        self.matcher: MinimapMatcher | None = None
        self.tracker: Tracker | None = None
        self.tf = dict(DEFAULT_TRANSFORM)
        self.live: dict | None = None        # this match: {"id", "started", "route", "chests", "fixes"}
        self.last_live: dict | None = None   # the previous match, until a new one starts
        self.team: dict[str, dict] = {}      # sender id -> {"name", "pos", "t", "route", "chests"}
        # squadmates from the replay Fortnite is recording (read every ~20 s mid-match): name ->
        # {"name", "route", "pos", "t", "chests"}. Squad only; positions are 0-20 s old.
        self.live_squad: dict[str, dict] = {}
        self.spots = SpotDB(self.dir / "chest_spots.json")
        self.replay_maps: dict[str, dict] = {}  # file name -> record
        self._stop = threading.Event()
        self._last_team_send = 0.0
        self._was_tracking = True   # the live route records only my own character on the ground (tracking_ok)
        self._last_game = None      # the chest counter at the last look (see _live_tick)
        self._last_track = 0.0
        self._break_next = False
        self._last_hist_send = 0.0
        self._index_path = self.maps_dir / "index.json"
        self._index_lock = threading.Lock()
        self._index: dict = self._load_index()  # read now: the chest counter may add to it before the loop runs
        self.backlog = 0                     # old replays still to read for the map
        self._timeouts: dict[str, int] = {}
        # the minimap's place on this PC (find_minimap); saved per window size as "minimap_boxes"
        self.save_cfg = None                 # set by the app: saves its settings
        self._miss_since: float | None = None  # tracking, but no trusted minimap match since then
        self._next_find = 0.0
        self._find_wait = FIND_EVERY
        self._finds = 0

    # ---- lifecycle -------------------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._guard, args=(self._live_loop,), daemon=True, name="map-live").start()
        threading.Thread(target=self._guard, args=(self._replay_loop,), daemon=True, name="map-replays").start()

    def stop(self):
        self._stop.set()

    def _guard(self, fn):
        """Runs a map thread's loop; after an error (a locked file, say) it logs it and starts the loop
        again, so one bad moment can't stop new games reaching the map for the rest of the session."""
        fails = 0
        while not self._stop.is_set():
            try:
                fn()
                return
            except Exception:
                fails += 1
                log.exception("map thread stopped (%d); starting it again", fails)
                self.status = "map tracking restarting (see tracker.log)"
            if self._stop.wait(min(300, 5 * fails)):
                return

    def bump(self):
        self.version += 1

    def season(self) -> str:
        """The season of the newest replay read (chest spots and heatmaps are per season)."""
        best = max(self.replay_maps.values(), key=lambda r: r.get("start", 0), default=None)
        return best["season"] if best else "unknown"

    # ---- map image and transform -----------------------------------------------------------
    def _load_map(self) -> bool:
        import cv2

        path = fetch_map(self.dir, download=not self.app.cfg.get("offline"))
        if path is None:
            self.status = "no map image yet (needs the internet once)"
            return False
        img = cv2.imread(str(path))
        if img is None or img.shape[0] != MAP_SIZE:
            self.status = "the map image is unreadable"
            return False
        self.map_path = path
        try:
            self.display_path = prepare_hires(self.dir, path) or path
        except Exception:
            log.exception("map: the sharper picture could not be used")
            self.display_path = path
        self.matcher = MinimapMatcher(img)
        self.tracker = Tracker(self.matcher)
        try:
            self.island = island_box(img)
        except Exception:
            self.island = None
        try:
            saved = json.loads((self.dir / "transform.json").read_text())
            if all(k in saved for k in ("scale", "ox", "oy")):
                self.tf = {k: float(saved[k]) for k in ("scale", "ox", "oy")}
        except (OSError, ValueError):
            pass
        self.status = "ready"
        self.bump()
        return True

    # ---- live tracking ---------------------------------------------------------------------
    def _grab(self, sct):
        import numpy as np

        rect = self.window_rect()
        if not rect:
            return None
        l, t, s = self.box(rect["width"], rect["height"])
        shot = sct.grab({"left": rect["left"] + l, "top": rect["top"] + t, "width": s, "height": s})
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])  # BGRA -> BGR

    def box(self, win_w: int, win_h: int) -> tuple[int, int, int]:
        """(left, top, size) of the minimap in a window of this size: where find_minimap found it on this PC
        (Fortnite's HUD scale changes it), else the default place."""
        saved = (self.app.cfg.get("minimap_boxes") or {}).get(f"{win_w}x{win_h}")
        try:
            gap, top, side = (int(v) for v in saved)
            if 40 <= side <= win_h and 0 <= gap and 0 <= top and gap + side <= win_w and top + side <= win_h:
                return win_w - gap - side, top, side
        except (TypeError, ValueError):
            pass
        return minimap_box(win_w, win_h)

    def _maybe_find_box(self, sct, now: float, matched: bool):
        """No trusted minimap match for a while: look for the minimap (its size depends on the HUD scale),
        at most every FIND_EVERY s, less often while it isn't found."""
        if matched:
            self._miss_since = None
            return
        if self._miss_since is None:
            self._miss_since = now
        if sct is None or now - self._miss_since < FIND_AFTER or now < self._next_find or self.matcher is None:
            return
        try:
            found = self._find_box(sct)
        except Exception:
            log.exception("map: looking for the minimap failed")
            found = False
        if found:
            self._find_wait = FIND_EVERY
            self._miss_since = None
        else:
            self._find_wait = min(FIND_MAX, self._find_wait * 2)
        self._next_find = now + self._find_wait

    def _find_box(self, sct) -> bool:
        import numpy as np

        rect = self.window_rect()
        if not rect:
            return False
        w, h = rect["width"], rect["height"]
        rh = round(FIND_REGION * h)
        rw = min(w, rh)
        shot = sct.grab({"left": rect["left"] + w - rw, "top": rect["top"], "width": rw, "height": rh})
        region = np.ascontiguousarray(np.asarray(shot)[:, :, :3])
        self._finds += 1
        t0 = time.perf_counter()
        found = find_minimap(region, self.matcher)
        took = time.perf_counter() - t0
        if found is None:
            log.info("map: no minimap found in the top-right corner (%dx%d window, %.1f s)", w, h, took)
            return False
        l, t, side, score = found
        gap = rw - l - side
        old = self.box(w, h)
        boxes = dict(self.app.cfg.get("minimap_boxes") or {})
        boxes[f"{w}x{h}"] = [gap, t, side]
        self.app.cfg["minimap_boxes"] = boxes
        if callable(self.save_cfg):
            try:
                self.save_cfg()
            except Exception:
                log.exception("map: saving the minimap's place failed")
        log.info("map: found the minimap: %d px, %d px from the right, %d from the top of the %dx%d window "
                 "(was %d px at %d,%d; match %.2f, %.1f s)", side, gap, t, w, h, old[2], old[0], old[1], score, took)
        if self.tracker is not None:
            self.tracker.reset()
        return True

    def _live_loop(self):
        import mss

        while not self._load_map():
            if self._stop.wait(600):
                return
        in_match = False
        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            while not self._stop.is_set():
                now = time.time()
                try:
                    match = bool(self.app.match_in_progress())
                except Exception:
                    match = False
                if match and not in_match:
                    self._start_live(now)
                elif in_match and not match:
                    self._end_live()
                in_match = match
                if match:
                    self._live_tick(sct, now)
                time.sleep(0.2)

    def _live_tick(self, sct, now: float):
        """One look at the screen during a match: chest marks when the counter goes up, and the position about
        once a second. Only my own character on the ground: not on the bus, skydiving, dead or spectating;
        after a reboot (or landing, or spectating) the route carries on as a new piece."""
        ok = self.tracking_ok()
        if ok and not self._was_tracking:
            self._break_next = True  # (landed, rebooted, back from spectating: no line from where I was)
            if self.tracker:
                self.tracker.reset()  # (somewhere else now: search the whole map once)
        self._was_tracking = ok
        if not ok:
            self._miss_since = None  # (only time spent tracking counts towards looking for the minimap)
        game = self.app.stats.game
        last = self._last_game
        if last is not None and game > last:
            if ok:
                self._chest(sct, game - last, now)
        elif last is not None and game < last:
            self._unchest(last - game)
        self._last_game = game
        if ok and now - self._last_track >= 1.0 and self.in_front():
            self._last_track = now
            self._track(sct, now)

    def tracking_ok(self) -> bool:
        """Whether the screen shows my own character on the ground (Fortnite's log, app.gamelog): the live
        route, chest marks and the position sent to the team are recorded only then. True when there's no
        log to ask, or it can't tell right now (as before)."""
        gl = getattr(self.app, "gamelog", None)
        if gl is None or not hasattr(gl, "tracking_ok"):
            return True
        try:
            if gl.match_state() is None:
                return True  # the log isn't read yet, or went quiet: track as before
            return bool(gl.tracking_ok())
        except Exception:
            return True

    def health(self) -> dict:
        """A few words on whether the live map works on this PC, sent with the team state so a teammate's
        tracker (or whoever helps) can tell why no position arrives: no map image, no minimap match, off..."""
        now = time.time()
        with self.lock:
            live = self.live
            route = live["route"] if live else []
            out = {"status": str(getattr(self, "status", "") or "")[:60], "loaded": self.tracker is not None,
                   "share": bool(self.app.cfg.get("map_share", True)), "live": live is not None,
                   "fixes": len(route), "chests": len(live["chests"]) if live else 0,
                   "fix_age": round(now - route[-1][0]) if route else None,
                   "tracking": bool(self.tracking_ok()), "teammates": len(self.team),
                   # minimap reads tried / screen grabs that worked / best match score (real matches: 0.75+)
                   "tries": getattr(self, "_tries", 0), "grabs": getattr(self, "_grabs", 0),
                   "best": round(getattr(self, "_best", 0.0), 2), "finds": self._finds}
        try:
            out["front"] = bool(self.in_front())
        except Exception:
            out["front"] = None
        try:
            rect = self.window_rect()
            out["window"] = [rect["width"], rect["height"]] if rect else None
            out["box"] = list(self.box(rect["width"], rect["height"])) if rect else None
        except Exception:
            out["window"] = None
        return out

    def camera_note(self) -> str:
        """Why the live route is paused, for the status line ("" while it's recording)."""
        gl = getattr(self.app, "gamelog", None)
        if gl is None or self.tracking_ok():
            return ""
        view = getattr(gl, "view", "")
        if view == "spectating":
            return f"spectating {getattr(gl, 'view_name', '') or 'a teammate'}"
        return {"bus": "on the bus", "skydive": "skydiving", "dead": "eliminated", "": "waiting for the bus"}.get(view, "")

    def _start_live(self, now: float):
        with self.lock:
            if self.live and (self.live["route"] or self.live["chests"]):
                self.last_live = self.live
            self.live = {"id": time.strftime("%Y%m%d-%H%M%S", time.localtime(now)), "started": now,
                         "route": [], "chests": [], "fixes": []}
            self.team.clear()
            self.live_squad = {}
        try:
            self._last_game = self.app.stats.game  # chests counted from here on
        except Exception:
            self._last_game = None
        if self.tracker:
            self.tracker.reset()
        self.bump()

    def _end_live(self):
        with self.lock:
            live = self.live
        if live and (live["route"] or live["chests"]):
            self._save_live(live)

    def _save_live(self, live: dict):
        d = self.dir / "live"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{live['id']}.json").write_text(json.dumps(live, separators=(",", ":")))
        for old in sorted(d.glob("*.json"))[:-300]:
            old.unlink(missing_ok=True)

    def _track(self, sct, now: float) -> tuple[float, float] | None:
        self._tries = getattr(self, "_tries", 0) + 1
        mm = self._grab(sct)
        if mm is None or self.tracker is None:
            return None
        self._grabs = getattr(self, "_grabs", 0) + 1
        before = self.tracker.pos
        fix = self.tracker.update(mm, now)
        if fix is not None:  # (for health(): how well the minimap matches the map on this PC)
            self._best = max(getattr(self, "_best", 0.0), float(getattr(fix, "score", 0.0) or 0.0))
        self._maybe_find_box(sct, now, fix is not None and fix.ok)
        if fix is None or not fix.ok:
            return None
        with self.lock:
            if self.live is None:
                return None
            jump = before is not None and ((fix.x - before[0]) ** 2 + (fix.y - before[1]) ** 2) ** 0.5 > JUMP_PX
            jump = jump or (self._break_next and bool(self.live["route"]))
            self._break_next = False
            self.live["route"].append([round(now, 1), round(fix.x, 1), round(fix.y, 1), 1 if jump else 0])
            self.live["fixes"].append([round(now, 2), round(fix.x, 2), round(fix.y, 2)])
        self.bump()
        return fix.x, fix.y

    def _chest(self, sct, n: int, now: float):
        """The chest counter went up: mark where the player is right now."""
        pos = None
        try:
            pos = self._track(sct, now)
        except Exception:
            log.exception("map: chest position")
        if pos is None and self.tracker is not None:
            pos = self.tracker.pos
        if pos is None:
            return
        x, y = pos
        spot = None
        wx, wy = map_to_world(x, y, self.tf)
        near = self.spots.nearest(self.season(), wx, wy, SNAP_CM)
        if near:
            spot = near[0]
            x, y = world_to_map(near[1]["x"], near[1]["y"], self.tf)
        with self.lock:
            if self.live is not None:
                for _ in range(min(n, 5)):
                    self.live["chests"].append({"t": round(now, 1), "x": round(x, 1), "y": round(y, 1), "spot": spot})
        self.bump()

    def _unchest(self, n: int):
        with self.lock:
            if self.live is not None:
                del self.live["chests"][max(0, len(self.live["chests"]) - n):]
        self.bump()

    # ---- squad over the team relay -----------------------------------------------------------
    def team_message(self) -> bytes | None:
        """This player's position and chests for the squad, at most every TEAM_EVERY seconds and only
        during a match. Small: a few hundred bytes."""
        now = time.time()
        if now - self._last_team_send < TEAM_EVERY or not self.app.cfg.get("map_share", True):
            return None
        with self.lock:
            live = self.live
            if live is None or not live["route"] or not self.app.match_in_progress() or not self.tracking_ok():
                return None  # (not while on the bus, skydiving, out or spectating: that isn't where I am)
            last = live["route"][-1]
            if now - last[0] > 10:
                return None
            chests = [[round(c["x"]), round(c["y"])] for c in live["chests"][-40:]]
            hist = self._history(live) if now - self._last_hist_send >= HIST_EVERY else None
        self._last_team_send = now
        msg = {"app": "chesttracker-map", "type": "map", "team": self.app.team.key, "id": self.app.stats.id,
               "name": str(self.app.cfg.get("name") or "")[:20], "match": live["id"], "t": round(now, 1),
               "pos": [round(last[1]), round(last[2])], "chests": chests}
        try:  # this game's real count (the overlay's): the marks above are only the last 40 with a position
            msg["game"] = int(getattr(self.app.stats, "game", 0) or 0)
        except (TypeError, ValueError):
            pass
        squad = self.guest_positions(now)
        if squad:
            msg["squad"] = squad  # extra field: older trackers ignore it
        if hist:
            msg["hist"] = hist    # extra field too: the team web page's timeline for viewers who open it mid-match
            self._last_hist_send = now
        return json.dumps(msg, separators=(",", ":")).encode()

    @staticmethod
    def _history(live: dict) -> dict:
        """This match so far, small: {"start", "route": [[s, x, y]] (a point every 5 s at most, 300 at most),
        "chests": [[s, x, y]] (where I opened chests)}, seconds counted from "start". Call under self.lock."""
        route = live["route"]
        start = live.get("started") or (route[0][0] if route else 0)
        pts = []
        for i, (t, x, y, *_rest) in enumerate(route):
            if not pts or t - start - pts[-1][0] >= 5 or i == len(route) - 1:
                pts.append([round(t - start), round(x), round(y)])
        if len(pts) > 300:
            step = len(pts) / 299
            pts = [pts[int(i * step)] for i in range(299)] + [pts[-1]]
        chests = [[round(c["t"] - start), round(c["x"]), round(c["y"])] for c in live["chests"][-80:]]
        return {"start": round(start, 1), "route": pts, "chests": chests}

    def guest_positions(self, now: float) -> list[dict]:
        """Where console squadmates (no tracker of their own) are, from the replay being recorded, for the
        squad's map windows. Only console teammates from settings and names teammates claimed, never
        random squad fills; teammates sharing their own position over the relay are left out."""
        def norm(n):
            return "".join(str(n or "").split()).lower()

        known = {norm(c) for c in self.app.cfg.get("console_teammates") or []}
        try:
            known |= {norm(x) for x in self.app.team.claimed_names()}
        except Exception:
            pass
        sharing = {norm(x) for x in self.relay_names()}
        with self.lock:
            squad = list(self.live_squad.values())
        out = []
        for p in squad:
            n = norm(p.get("name"))
            if not n or n not in known or n in sharing or not p.get("pos") or now - (p.get("t") or 0) > 60:
                continue
            out.append({"name": str(p["name"])[:32], "pos": [round(p["pos"][0]), round(p["pos"][1])],
                        "t": round(p["t"], 1)})
        return out[:4]

    def on_team_message(self, m) -> bool:
        """A squad member's position (from NetLink). Returns True if it was used."""
        try:
            if not isinstance(m, dict) or m.get("type") != "map" or m.get("team") != self.app.team.key:
                return False
            sender = str(m.get("id", ""))[:32]
            if not sender or sender == self.app.stats.id or self.live is None:
                return False
            x, y = (float(v) for v in m["pos"])
            if not (0 <= x <= MAP_SIZE and 0 <= y <= MAP_SIZE):
                return False
            chests = [(float(c[0]), float(c[1])) for c in (m.get("chests") or [])[:60]
                      if isinstance(c, list) and len(c) == 2]
            game = m.get("game")
            game = int(game) if isinstance(game, (int, float)) and 0 <= game < 10000 else None
        except (KeyError, TypeError, ValueError):
            return False
        match = str(m.get("match") or "")[:40]
        with self.lock:
            p = self.team.setdefault(sender, {"name": "", "route": [], "chests": []})
            if p.get("match") is not None and p.get("match") != match:  # a new match: start over
                p["route"], p["chests"] = [], []
            p["match"] = match
            p["name"] = str(m.get("name") or "teammate")[:20]
            p["t"] = time.time()
            p["pos"] = (x, y)
            if not p["route"] or abs(p["route"][-1][0] - x) + abs(p["route"][-1][1] - y) >= 2:
                p["route"].append((x, y))
            # each message carries only the last 40 marks: keep the earlier ones too
            have = {(round(cx), round(cy)) for cx, cy in p["chests"]}
            p["chests"] = p["chests"] + [c for c in chests if (round(c[0]), round(c[1])) not in have]
            p["chests"] = p["chests"][-300:]
            if game is not None:
                p["game"] = game  # their overlay's count (trackers from before 1.20 don't send it)
        self.bump()
        return True

    def take_live_squad(self, squad, teammates, started: float):
        """Mid-match: where the squadmates are, from the replay being recorded (the reader's --squad output:
        the recorder's own team only). For console teammates, who can't share their position themselves."""
        # chests opened (anywhere, from their search flag) + broken (near the recorder)
        opened = {t.get("name"): (t.get("opened") or 0) + (t.get("broken") or 0)
                  for t in teammates or [] if isinstance(t, dict)}
        out = {}
        for p in squad or []:
            route = p.get("route") or []
            if p.get("recorder") or not route or not p.get("name"):
                continue  # my own route comes from the minimap, live
            pts = [world_to_map(pt[1], pt[2], self.tf) for pt in route]
            out[p["name"]] = {"name": p["name"], "route": pts, "pos": pts[-1],
                              "t": (started + route[-1][0]) if started else time.time(),
                              "chests": opened.get(p["name"])}
        with self.lock:
            self.live_squad = out
        self.bump()

    def relay_names(self) -> set[str]:
        """Lower-case names (app name and Fortnite names) of teammates sharing their position over the
        relay: their 3-second updates beat the replay's delayed ones."""
        names = set()
        peers = getattr(getattr(self.app, "team", None), "peers", None) or {}
        with self.lock:
            items = list(self.team.items())
        for sid, p in items:
            names.add(str(p.get("name") or "").strip().lower())
            for n in (peers.get(sid) or {}).get("fortnite_names") or []:
                names.add(str(n).strip().lower())
        names.discard("")
        return names

    # ---- replays: exact routes after the match, chest spots, heatmaps ------------------------
    def _load_index(self) -> dict:
        try:
            return json.loads(self._index_path.read_text())
        except (OSError, ValueError):
            return {}

    def _load_records(self):
        self.maps_dir.mkdir(parents=True, exist_ok=True)
        for f in self.maps_dir.glob("*.map.json"):
            try:
                rec = json.loads(f.read_text())
                tidy_chests(rec, self.spots.all_spots())
                self.replay_maps[rec["file"]] = rec
            except (OSError, ValueError, KeyError):
                continue
        self.bump()

    def _run_reader(self, path: Path, background: bool = False) -> dict | None:
        """background: a match is being played: lowest priority, Windows background mode, one CPU core (the
        reader sets those itself on CT_BACKGROUND), so the game keeps its frames."""
        import subprocess
        import sys

        # no window, below normal priority (idle priority in the background)
        flags = 0x08000000 | (0x00000040 if background else 0x00004000) if sys.platform == "win32" else 0
        env = dict(os.environ, CT_BACKGROUND="1") if background else None
        proc = subprocess.run([str(self.reader), "--map", str(path)], capture_output=True, text=True,
                              timeout=600 if background else 180, env=env,
                              creationflags=flags, encoding="utf-8", errors="replace")
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else None

    PLAYING_GAP = 15.0  # s between replays read during a match

    def _playing(self) -> bool:
        try:
            return bool(self.app.match_in_progress())
        except Exception:
            return False

    def _workers(self) -> int:
        """How many replays to read at once: one during a match (in the background: see _run_reader) or while
        Fortnite is open (lobby), several when it's closed (each reader uses about one CPU core). (Nov 20, 2026:
        none during matches left a game without its map when the next match was queued from the results.)"""
        if self._playing():
            return 1
        try:
            if self.window_rect():
                return 1
        except Exception:
            pass
        return max(1, min(4, (os.cpu_count() or 2) // 4))

    def _todo(self, index: dict, reader_stamp: float) -> list:
        todo = []
        for f in self.demos.glob("*.replay"):
            try:
                st = f.stat()
            except OSError:
                continue
            if time.time() - st.st_mtime < 30:
                continue  # still being written
            key = f"{st.st_size}:{int(st.st_mtime)}"
            done = index.get(f.name)
            if done and (done.get("key") == key or done.get("bad")) and done.get("reader", 0) >= reader_stamp - 1:
                continue
            todo.append((st.st_mtime, f, key))
        todo.sort(reverse=True)  # newest first: the match that just ended
        return todo

    def _replay_loop(self):
        from concurrent.futures import ThreadPoolExecutor

        self._load_records()
        index = self._index
        pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="map-reader")
        idle = 0.0
        while not self._stop.wait(idle):
            idle = 20.0
            if not self.reader.exists():
                idle = 60.0
                continue
            workers = self._workers()
            if workers == 0:
                idle = 15.0
                continue
            background = self._playing()
            reader_stamp = self.reader.stat().st_mtime
            with self._index_lock:
                todo = self._todo(index, reader_stamp)
            self.backlog = len(todo)
            if not todo:
                continue
            batch = todo[:workers]
            results = list(pool.map(lambda job: (job, self._read_one(job[1], background)), batch))
            for (_, f, key), out in results:
                entry = {"key": key, "reader": reader_stamp}
                if out == "live":
                    continue  # still recording: try again later
                if out is None:
                    entry["bad"] = True  # an old reader without --map, or an unreadable file
                else:
                    self._store(f, out)
                with self._index_lock:
                    index[f.name] = entry
            self._save_index()
            self.backlog = max(0, len(todo) - len(batch))
            idle = (self.PLAYING_GAP if background else 0.0) if self.backlog else 20.0
            if results and all(out == "live" for _, out in results):
                idle = 30.0  # nothing could be read right now (recording, or a busy PC): wait a bit
        pool.shutdown(wait=False)

    def _save_index(self):
        # The map worker and the chest counter's replay check both save it: one at a time (two writers on
        # index.tmp at once gave "Permission denied" and stopped the map thread), and a failed save is only
        # logged: the index is saved again after the next replay.
        with self._index_lock:
            text = json.dumps(self._index)
        lock = self.__dict__.setdefault("_index_save_lock", threading.Lock())
        with lock:
            save_atomic(self._index_path, text)

    def take_reader_output(self, path: Path, out: dict, finished: bool = False):
        """The chest counter read a replay with --map: keep its map data, so it isn't read twice.
        finished: its header says the recording is over (a past match, also when read during the next one)."""
        if not isinstance(out, dict) or not out.get("map") or out.get("live"):
            return
        try:
            in_match = bool(self.app.match_in_progress())
        except Exception:
            in_match = True
        try:
            st = path.stat()
            stamp = self.reader.stat().st_mtime
        except OSError:
            return
        # Fair play: map data (storm, everyone's routes) only after the match, never from a replay that is
        # still being recorded. The map worker reads it again once the match is over. A complete read of a
        # finished replay (not "live") is kept straight away, so the game that just ended is on the map at once.
        # (a past match read during this one is fine: only the replay of the match being played waits)
        # (Nov 20, 2026: queueing the next match from the results screen used to leave a game without its map)
        current = getattr(getattr(self.app, "stats", None), "replay", None)
        if not finished and ((in_match and (current is None or path.name == current)) or (
                time.time() - st.st_mtime < 30 and not out.get("complete"))):
            return
        self._store(path, out)
        with self._index_lock:
            self._index[path.name] = {"key": f"{st.st_size}:{int(st.st_mtime)}", "reader": stamp}
        self._save_index()

    def _read_one(self, f: Path, background: bool = False):
        """Map data of one replay: the reader's JSON, "live" if it's still recording, None if unreadable.
        (The reader refuses a replay that is still being recorded: the match being played is never read.)"""
        import subprocess

        try:
            out = self._run_reader(f, background) if background else self._run_reader(f)
        except subprocess.TimeoutExpired:
            # usually a busy PC (the reader runs at low priority): try again later, a few times
            self._timeouts[f.name] = self._timeouts.get(f.name, 0) + 1
            log.info("map: reading %s timed out (%d)", f.name, self._timeouts[f.name])
            return "live" if self._timeouts[f.name] < 3 else None
        except Exception as e:
            log.info("map: reading %s failed: %s", f.name, e)
            return None
        if out is None or "map" not in out:
            return None
        if out.get("live"):
            return "live"
        return out if out.get("map") else None

    def _store(self, f: Path, out: dict):
        import datetime as dt

        start = 0.0
        if out.get("start_utc"):
            try:
                start = dt.datetime.fromisoformat(out["start_utc"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                start = 0.0
        rec = {"file": f.name, "guid": out.get("guid"), "start": start, "length": (out.get("length_ms") or 0) / 1000,
               "branch": out.get("branch"), "season": season_of(out.get("branch")),
               "recorder": out.get("recorder_name"), "session": out.get("session_id"),
               "complete": bool(out.get("complete")), "map": out["map"]}
        (self.maps_dir / f"{f.stem}.map.json").write_text(json.dumps(rec, separators=(",", ":")))
        with self.lock:
            self.replay_maps[f.name] = rec
        self._learn_spots(rec)
        tidy_chests(rec, self.spots.all_spots())
        self._refit(rec)
        self.bump()

    def _learn_spots(self, rec: dict):
        season = rec["season"]
        for s in rec["map"].get("spots", []):
            self.spots.add(season, s["id"], s.get("class") or "", s["x"], s["y"], s.get("source") or "opener")
        for c in rec["map"].get("chests", []):
            cid = c.get("id") or ""
            if cid and "@" not in cid and not cid.startswith("#"):
                self.spots.add(season, cid, c.get("class") or "", c["x"], c["y"], c.get("source") or "opener")
        self.spots.save()

    def live_for(self, rec: dict) -> dict | None:
        """The live record of the match a replay recorded (they overlap in time)."""
        start, end = rec.get("start", 0), rec.get("start", 0) + rec.get("length", 0)
        cands = [lv for lv in (self.live, self.last_live) if lv]
        d = self.dir / "live"
        if d.exists():
            for f in sorted(d.glob("*.json"))[-20:]:
                try:
                    cands.append(json.loads(f.read_text()))
                except (OSError, ValueError):
                    pass
        for lv in cands:
            if lv.get("route") and start - 120 <= lv["started"] <= end:
                return lv
        return None

    def _refit(self, rec: dict):
        """Re-measures the world->map conversion from a match with both live fixes and a replay route
        (a new map image can be shifted or scaled)."""
        lv = self.live_for(rec)
        me = next((p for p in rec["map"].get("players", []) if p.get("recorder")), None)
        if not lv or not me or len(lv.get("fixes", [])) < 40 or not rec.get("start"):
            return
        got = fit_from_match(lv["fixes"], rec["start"], me["route"])
        if not got:
            return
        tf, residual, n = got
        if residual > 2.5:
            log.info("map: refit rejected (median %.1f px over %d fixes)", residual, n)
            return
        self.tf = tf
        (self.dir / "transform.json").write_text(json.dumps({**tf, "residual_px": residual, "fixes": n,
                                                             "map": str(self.map_path), "when": time.time()}))
        log.info("map: world->map refitted from %d fixes, median %.2f px", n, residual)

    # ---- for the window ----------------------------------------------------------------------
    def latest_replay_for_live(self, lv: dict | None) -> dict | None:
        if not lv:
            return None
        with self.lock:
            recs = list(self.replay_maps.values())
        for rec in sorted(recs, key=lambda r: -r.get("start", 0)):
            if rec.get("start") and rec["start"] - 120 <= lv["started"] <= rec["start"] + rec.get("length", 0):
                return rec
        return None

    def pois(self) -> list[tuple[str, float, float]]:
        """Named places on the current map (from fortnite-api.com): (name, world x, world y)."""
        path = self.dir / "map.json"
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return []
        cached = getattr(self, "_pois", None)
        if cached and cached[0] == stamp:
            return cached[1]  # read from disk only when the file changes (this is asked every frame)
        try:
            data = json.loads(path.read_text())
            pois = sorted((str(p["name"]), float(p["location"]["x"]), float(p["location"]["y"]))
                          for p in data.get("pois", []) if p.get("name") and p.get("location"))
        except (OSError, ValueError, KeyError, TypeError):
            pois = []
        self._pois = (stamp, pois)
        return pois

    def records(self) -> list[dict]:
        with self.lock:
            self._attach_totals()
            return list(self.replay_maps.values())

    def _attach_totals(self):
        """Each record's rec["_totals"]: whole-game (opened, broken) per player from replays.json (see
        replay_totals), so every count on the map matches the rest of the app. Redone when the log changes."""
        db = getattr(getattr(self.app, "replays", None), "db", None)
        files = getattr(db, "files", None)
        if not isinstance(files, dict):
            return
        key = (id(db), getattr(db, "version", 0), len(self.replay_maps), len(files))
        if key == getattr(self, "_totals_key", None):
            return
        self._totals_key = key
        for rec in self.replay_maps.values():
            rec["_totals"] = replay_totals(files.get(rec.get("file")))

    def totals_version(self):
        """Changes when the replay log's numbers do (for redrawing labels)."""
        db = getattr(getattr(self.app, "replays", None), "db", None)
        return getattr(db, "version", 0), len(getattr(db, "files", None) or {})


# --------------------------------------------------------------------------------------------
# Filters for heatmaps and route overlays
# --------------------------------------------------------------------------------------------
PERIODS = {"All time": None, "Last 7 days": 7, "Last 30 days": 30, "Today": 1}
GAMES = ["All games", "Top 10% by chests", "Top 25% by chests", "Bottom 25% by chests",
         "Top 10% by eliminations", "Longest 25% (time alive)", "Shortest 25% (time alive)"]
NEAR_POI_CM = 30000  # "landed at" a place: within 300 m of its centre


def match_summary(rec: dict, who) -> dict:
    """What the chosen players did in one match: chests opened, eliminations, time alive after landing,
    where they landed. who(name, is_recorder) -> bool."""
    players = [p for p in rec["map"].get("players", []) if who(p.get("name"), p.get("recorder"))]
    names = {p.get("name") for p in players}
    chests = sum(chests_so_far(rec, n)[0] for n in names)  # the replay log's whole-game numbers when known
    kills = sum(1 for p in players for k in p.get("kills", []))
    alive = 0.0
    landings = []
    for p in players:
        route = p.get("route") or []
        start = p["landing"][0] if p.get("landing") else (route[0][0] if route else 0)
        if route:
            alive = max(alive, route[-1][0] - start)
        if p.get("landing"):
            landings.append((p["landing"][1], p["landing"][2]))
    return {"chests": chests, "kills": kills, "alive": alive, "landings": landings,
            "names": names, "all_names": {p.get("name") for p in rec["map"].get("players", [])}}


def filter_matches(recs, who, season: str | None = None, days: float | None = None, games: str = "All games",
                   landed_at: tuple[float, float] | None = None, with_player: str | None = None,
                   now: float | None = None) -> list[dict]:
    """The matches that pass every filter. Ranking filters ("Top 10% by chests"...) rank the matches
    left after the other filters, by what the chosen players did in each."""
    now = time.time() if now is None else now
    picked = []
    for r in recs:
        if season and r.get("season") != season:
            continue
        if days is not None and (r.get("start") or 0) < now - days * 86400:
            continue
        s = match_summary(r, who)
        if not s["names"]:
            continue  # none of the chosen players were in it
        if with_player and with_player not in s["all_names"]:
            continue
        if landed_at and not any(((x - landed_at[0]) ** 2 + (y - landed_at[1]) ** 2) ** 0.5 <= NEAR_POI_CM
                                 for x, y in s["landings"]):
            continue
        picked.append((r, s))
    rank = {"Top 10% by chests": ("chests", 0.10, True), "Top 25% by chests": ("chests", 0.25, True),
            "Bottom 25% by chests": ("chests", 0.25, False), "Top 10% by eliminations": ("kills", 0.10, True),
            "Longest 25% (time alive)": ("alive", 0.25, True), "Shortest 25% (time alive)": ("alive", 0.25, False)}
    if games in rank and picked:
        key, share, top = rank[games]
        picked.sort(key=lambda rs: rs[1][key], reverse=top)
        n = max(1, round(len(picked) * share))
        picked = picked[:n]
    return [r for r, _ in picked]


def route_lines(recs, who, tf: dict = DEFAULT_TRANSFORM, after_landing: bool = True):
    """Each chosen player's path in each match as a list of map px points (bus and way down left out,
    split where the route has gaps)."""
    lines = []
    for r in recs:
        for p in r["map"].get("players", []):
            if not who(p.get("name"), p.get("recorder")):
                continue
            landed = p["landing"][0] if (after_landing and p.get("landing")) else None
            piece, prev = [], None
            for pt in p.get("route", []):
                if (landed is not None and pt[0] < landed) or pt[3] > 20000:
                    prev = None
                    continue
                # a long gap with a big move (riding a vehicle): start a new line; standing still: keep it
                if (prev is not None and pt[0] - prev[0] > 6 and len(piece) > 1
                        and ((pt[1] - prev[1]) ** 2 + (pt[2] - prev[2]) ** 2) ** 0.5 > 3000):
                    lines.append(piece)
                    piece = []
                piece.append(world_to_map(pt[1], pt[2], tf))
                prev = pt
            if len(piece) > 1:
                lines.append(piece)
    return lines


def smooth(points, rounds: int = 2):
    """Rounds off a polyline's corners (Chaikin), keeping its ends. Works on any number of values per
    point; numpy does the work, so long routes are cheap."""
    import numpy as np

    pts = list(points)
    if len(pts) < 3 or rounds <= 0:
        return pts
    a = np.asarray(pts, dtype=np.float64)
    for _ in range(rounds):
        q = 0.75 * a[:-1] + 0.25 * a[1:]
        r = 0.25 * a[:-1] + 0.75 * a[1:]
        mid = np.empty((2 * len(q),) + a.shape[1:])
        mid[0::2], mid[1::2] = q, r
        a = np.concatenate([a[:1], mid, a[-1:]])
    return [tuple(row) for row in a.tolist()]


# --------------------------------------------------------------------------------------------
# Reviewing matches: stats, events, drop spots, chest spot hit rates, loot paths, the storm
# --------------------------------------------------------------------------------------------
RUN_SPEED = 5.5          # m/s, sprinting on foot (for loot path times)
OPEN_TIME = 1.5          # s per chest (walk up and open)
FAST_SPEED = 9.0         # m/s: faster than this on the ground is a vehicle, launch pad, zipline...
VISIT_CM = 4000          # a chest spot within 40 m of your path: you were close enough to see it there


def landing_name(x: float, y: float, pois) -> str:
    """The named place nearest to a landing (pois: [(name, x, y)]); "near X" beyond 300 m."""
    if not pois:
        return "Unknown"
    name, px, py = min(pois, key=lambda p: (p[1] - x) ** 2 + (p[2] - y) ** 2)
    d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
    return name if d <= NEAR_POI_CM else f"near {name}"


def player_stats(rec: dict, player: dict, pois=()) -> dict:
    """One player's match: time alive, time in the air, distances on foot / fast / swimming, chests,
    chests per minute, first chest, eliminations, where they landed."""
    route = player.get("route") or []
    landing = player.get("landing")
    land_t = landing[0] if landing else (route[0][0] if route else 0.0)
    end_t = route[-1][0] if route else land_t
    # time in the air: from leaving the bus (last moment at bus height) to landing
    air = 0.0
    if landing and route:
        before = [p for p in route if p[0] <= land_t]
        if before:
            top = max(p[3] for p in before)
            exit_t = max((p[0] for p in before if p[3] >= top - 500), default=land_t)
            air = max(0.0, land_t - exit_t)
    walk = fast = swim = 0.0
    for a, b in zip(route, route[1:]):
        if a[0] < land_t:
            continue
        d = ((b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5 / 100
        speed = d / max(0.1, b[0] - a[0])
        if b[0] - a[0] > 5:  # nothing recorded for a while: still, or riding a vehicle (counted as fast)
            if speed >= FAST_SPEED:
                fast += d
            continue
        mode = b[4] if len(b) > 4 else 0
        if mode == 4:
            swim += d
        elif speed >= FAST_SPEED:
            fast += d
        elif mode != 3:  # falling (jumping off things) counts as nothing
            walk += d
    name = player.get("name")
    mine = [c for c in rec["map"].get("chests", []) if c.get("who") == name]  # far-away searches are real opens
    opened = [c for c in mine if not c.get("broken")]
    alive = max(0.0, end_t - land_t)
    first = min((c["t"] for c in opened), default=None)
    n_opened, n_broken = chests_so_far(rec, name)  # whole game: the replay log's numbers when known
    return {"name": name, "alive": alive, "air": air, "walk_m": walk, "fast_m": fast, "swim_m": swim,
            "opened": n_opened, "broken": n_broken,
            "build_breaks": sum(1 for c in mine if c.get("by_build")),
            "per_min": n_opened / (alive / 60) if alive > 30 else 0.0,
            "first_chest": None if first is None else max(0.0, first - land_t),
            "kills": len(player.get("kills") or []),
            "landed_at": landing_name(landing[1], landing[2], pois) if landing else "Unknown",
            "landing": (landing[1], landing[2]) if landing else None}


def match_events(rec: dict) -> list[dict]:
    """Everything worth jumping to in a match, in time order: {t, kind, text, who}.
    kind: landing, chest, broken, kill, out (a player's route ends early), storm."""
    ev = []
    length = rec.get("length") or 0
    for p in rec["map"].get("players", []):
        name = p.get("name") or "?"
        if p.get("landing"):
            ev.append({"t": p["landing"][0], "kind": "landing", "text": f"{name} landed", "who": name})
        for k in p.get("kills", []):
            k = kill_info(k)
            victim = f" {k['victim']}" if k["victim"] else ""
            ev.append({"t": k["t"], "kind": "kill", "who": name,
                       "text": f"{name} eliminated{victim}" + (" (about here)" if k["approx"] else "")})
        route = p.get("route") or []
        if route and length and route[-1][0] < length - 20:
            ev.append({"t": route[-1][0], "kind": "out", "text": f"{name} out", "who": name})
    for c in rec["map"].get("chests", []):
        how = "broke a chest with a build" if c.get("by_build") else "broke a chest" if c.get("broken") else "opened a chest"
        if c.get("source") == "search":
            how = "opened a chest (far from the recorder)"
        ev.append({"t": c["t"], "kind": "broken" if c.get("broken") else "chest", "text": f"{c.get('who')} {how}",
                   "who": c.get("who")})
    for i, z in enumerate(rec["map"].get("storm", []) or []):
        ev.append({"t": z["start"], "kind": "storm", "text": f"Storm {i + 1} closing ({z['nr'] / 100:.0f} m circle)",
                   "who": None})
    ev.sort(key=lambda e: e["t"])
    return ev


def storm_at(storm: list, t: float):
    """The storm at time t: (centre x, centre y, radius, next x, next y, next radius) in world units,
    or None before the first circle."""
    current = None
    for z in storm or []:
        if z["t"] - 1 > t and current is not None:
            break
        if z["t"] - 1 > t:
            return None
        current = z
    if current is None or current.get("pr", 0) <= 0:
        return None
    z = current
    if t <= z["start"]:
        cx, cy, r = z["px"], z["py"], z["pr"]
    elif t >= z["end"]:
        cx, cy, r = z["nx"], z["ny"], z["nr"]
    else:
        f = (t - z["start"]) / max(1e-6, z["end"] - z["start"])
        cx, cy, r = z["px"] + (z["nx"] - z["px"]) * f, z["py"] + (z["ny"] - z["py"]) * f, z["pr"] + (z["nr"] - z["pr"]) * f
    return cx, cy, r, z["nx"], z["ny"], z["nr"]


def drop_report(recs, who, pois) -> list[dict]:
    """Per landing place: games, average chests / eliminations / time alive, best game (by chests)."""
    rows: dict[str, dict] = {}
    for r in recs:
        for p in r["map"].get("players", []):
            if not who(p.get("name"), p.get("recorder")) or not p.get("landing"):
                continue
            s = player_stats(r, p, pois)
            row = rows.setdefault(s["landed_at"], {"place": s["landed_at"], "games": 0, "chests": 0, "kills": 0,
                                                    "alive": 0.0, "best": 0, "best_file": None, "landings": []})
            row["games"] += 1
            row["chests"] += s["opened"]
            row["kills"] += s["kills"]
            row["alive"] += s["alive"]
            row["landings"].append(s["landing"])
            if s["opened"] >= row["best"]:
                row["best"], row["best_file"] = s["opened"], r.get("file")
    out = []
    for row in rows.values():
        n = row["games"]
        out.append({**row, "avg_chests": row["chests"] / n, "avg_kills": row["kills"] / n, "avg_alive": row["alive"] / n})
    out.sort(key=lambda r: (-r["avg_chests"], -r["games"]))
    return out


def spot_hit_rates(recs, spots: dict) -> dict[str, tuple[int, int]]:
    """How often each known chest spot actually had a chest: {spot id: (times there, times you were
    close enough to see it)}. Only matches read with the "seen" list count."""
    cell = VISIT_CM
    out = {sid: [0, 0] for sid in spots}
    by_cell: dict[tuple[int, int], list[str]] = {}
    for sid, s in spots.items():
        by_cell.setdefault((int(s["x"] // cell), int(s["y"] // cell)), []).append(sid)
    for r in recs:
        seen = r["map"].get("seen")
        if seen is None:
            continue
        seen = set(seen)
        cells = set()
        for p in r["map"].get("players", []):
            land_t = p["landing"][0] if p.get("landing") else None
            for pt in p.get("route") or []:
                if (land_t is not None and pt[0] < land_t) or pt[3] > 20000:
                    continue
                cells.add((int(pt[1] // cell), int(pt[2] // cell)))
        near = set()
        for cx, cy in cells:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    near.update(by_cell.get((cx + dx, cy + dy), ()))
        limit2 = (cell * 1.2) ** 2
        for sid in near:
            s = spots[sid]
            sx, sy = int(s["x"] // cell), int(s["y"] // cell)
            # only the walked cells next to the spot's own cell can be close enough (not every walked cell)
            if not any(((s["x"] - (cx + 0.5) * cell) ** 2 + (s["y"] - (cy + 0.5) * cell) ** 2) <= limit2
                       for cx in (sx - 1, sx, sx + 1) for cy in (sy - 1, sy, sy + 1) if (cx, cy) in cells):
                continue
            out[sid][1] += 1
            if sid in seen:
                out[sid][0] += 1
    return {sid: (a, b) for sid, (a, b) in out.items() if b}


def loot_path(spots: dict, start: tuple[float, float], radius: float = 25000, count: int = 10,
              rates: dict | None = None, min_rate: float = 0.3) -> dict:
    """A short walk through the known chest spots near start (world units): nearest-first, then
    untangled (2-opt). Spots that rarely have a chest are left out when their hit rate is known.
    Returns {"spots": [(id, x, y)], "metres": total walk, "seconds": walk + opening time}."""
    sx, sy = start
    cands = []
    for sid, s in spots.items():
        d = ((s["x"] - sx) ** 2 + (s["y"] - sy) ** 2) ** 0.5
        if d > radius:
            continue
        if rates and sid in rates:
            there, visits = rates[sid]
            if visits >= 2 and there / visits < min_rate:
                continue
        cands.append((sid, s["x"], s["y"]))
    path, here = [], (sx, sy)
    while cands and len(path) < count:
        nxt = min(cands, key=lambda c: (c[1] - here[0]) ** 2 + (c[2] - here[1]) ** 2)
        cands.remove(nxt)
        path.append(nxt)
        here = (nxt[1], nxt[2])

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    pts = [(sx, sy)] + [(c[1], c[2]) for c in path]
    improved = True
    while improved:  # 2-opt: reverse any stretch that makes the walk shorter
        improved = False
        for i in range(1, len(pts) - 2):
            for j in range(i + 1, len(pts) - 1):
                if dist(pts[i - 1], pts[i]) + dist(pts[j], pts[j + 1]) > dist(pts[i - 1], pts[j]) + dist(pts[i], pts[j + 1]) + 1:
                    pts[i:j + 1] = reversed(pts[i:j + 1])
                    path[i - 1:j] = reversed(path[i - 1:j])
                    improved = True
    metres = sum(dist(a, b) for a, b in zip(pts, pts[1:])) / 100
    return {"spots": path, "start": start, "metres": metres, "seconds": metres / RUN_SPEED + OPEN_TIME * len(path)}


# --------------------------------------------------------------------------------------------
# A sharper map picture (e.g. the game's own 4096 px minimap texture, exported with FModel)
# --------------------------------------------------------------------------------------------
HIRES_SIZE = 2 * MAP_SIZE


def align_hires(hires_bgr, api_bgr):
    """Finds where a sharper map picture sits on the 2048 px map (it may be framed or scaled
    differently): a 2x3 matrix taking its pixels to 2048-map pixels, or None if they don't match."""
    import cv2
    import numpy as np

    h, w = hires_bgr.shape[:2]
    k = 2048 / max(h, w)  # find features on a copy about the size of the 2048 map
    small = cv2.resize(hires_bgr, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    orb = cv2.ORB_create(6000)
    ka, da = orb.detectAndCompute(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), None)
    kb, db = orb.detectAndCompute(cv2.cvtColor(api_bgr, cv2.COLOR_BGR2GRAY), None)
    if da is None or db is None or len(ka) < 50 or len(kb) < 50:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(matches) < 40:
        return None
    src = np.float32([ka[m.queryIdx].pt for m in matches]) / k  # back to the sharp picture's pixels
    dst = np.float32([kb[m.trainIdx].pt for m in matches])
    A, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if A is None or inliers is None or int(inliers.sum()) < 40:
        return None
    scale = (A[0, 0] ** 2 + A[1, 0] ** 2) ** 0.5
    if abs(A[1, 0]) / scale > 0.02:  # the map is never turned
        return None
    return A


def prepare_hires(map_dir: Path, api_path: Path) -> Path | None:
    """If a sharper map picture was put in <map_dir>/hires, lines it up with the 2048 map and saves it
    as a 4096 px picture on the same grid (2 px per 2048-map px). Done again only when either changes."""
    import cv2
    import numpy as np

    folder = map_dir / "hires"
    folder.mkdir(parents=True, exist_ok=True)
    pics = sorted((p for p in folder.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".tga", ".bmp")),
                  key=lambda p: p.stat().st_mtime)
    if not pics or not api_path or not api_path.exists():
        return None
    src = pics[-1]
    out = map_dir / "map_hires.png"
    stamp = f"{src.name}:{src.stat().st_mtime:.0f}:{api_path.stat().st_mtime:.0f}"
    meta = map_dir / "map_hires.json"
    try:
        if out.exists() and json.loads(meta.read_text()).get("stamp") == stamp:
            return out
    except (OSError, ValueError):
        pass
    hires = cv2.imread(str(src), cv2.IMREAD_COLOR)
    api = cv2.imread(str(api_path), cv2.IMREAD_COLOR)
    if hires is None or api is None:
        return None
    A = align_hires(hires, api)
    if A is None:
        log.info("map: %s doesn't line up with the current map; not used", src.name)
        meta.write_text(json.dumps({"stamp": stamp, "ok": False}))
        out.unlink(missing_ok=True)
        return None
    M = (A * (HIRES_SIZE / MAP_SIZE)).astype(np.float32)
    warped = cv2.warpAffine(hires, M, (HIRES_SIZE, HIRES_SIZE), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    cv2.imwrite(str(out), warped)
    meta.write_text(json.dumps({"stamp": stamp, "ok": True, "matrix": A.tolist()}))
    log.info("map: using the sharper picture %s", src.name)
    return out


# --------------------------------------------------------------------------------------------
# Loot path around the outside of the island
# --------------------------------------------------------------------------------------------
def island_circle(box_map: tuple, tf: dict = DEFAULT_TRANSFORM) -> tuple[float, float, float]:
    """The island as a circle in world units (centre x, centre y, radius) from its box in map px."""
    x0, y0 = map_to_world(box_map[0], box_map[1], tf)
    x1, y1 = map_to_world(box_map[2], box_map[3], tf)
    return (x0 + x1) / 2, (y0 + y1) / 2, min(abs(x1 - x0), abs(y1 - y0)) / 2


def spot_rate(rates: dict | None, sid: str) -> float:
    """How often a chest is really at a spot (0..1); unknown spots count as an average 0.45."""
    there, visits = (rates or {}).get(sid, (0, 0))
    return there / visits if visits >= 2 else 0.45


def outer_loot_path(spots: dict, start: tuple[float, float], circle: tuple[float, float, float],
                    rates: dict | None = None, count: int = 25, inner: float = 0.45, min_rate: float = 0.3,
                    avoid=(), checkpoints=(), avoid_middle: bool = True, max_per_place: int | None = None,
                    pois=()) -> dict:
    """A looting path from a landing spot (world units).

    Stops are picked one at a time, always the chest that adds the most expected loot per second from
    where you are, so chests on the way are picked up. It never plans inside a no-go circle (avoid: [(x, y,
    r)], plus the middle of the island when avoid_middle) and walks round them. Checkpoints ([(x, y)]) are
    visited in the order given, with the best chests between each pair (chests are shared out by leg
    length); after the last one it carries on round the outside. Both directions round the island are
    tried; the one with the most expected chests per minute wins.

    max_per_place: at most this many chests from one place (a named place from pois [(name, x, y)] within
    300 m, else a 300 m square), so the path spreads round the island instead of clearing one dense town.

    Returns {"start", "spots": [(id, x, y)] (chests only), "stops": [("chest", id, x, y) | ("checkpoint", n,
    x, y)], "path": [(x, y)] with detours, "metres", "seconds", "chests" (expected), "direction",
    "inner": (cx, cy, r) or None, "avoid": [(x, y, r)], "checkpoints": [(x, y)]}."""
    cx, cy, radius = circle
    r_in = inner * radius
    blocks = [tuple(map(float, b)) for b in avoid]
    if avoid_middle:
        blocks.append((cx, cy, r_in))
    sx, sy = start
    a0 = math.atan2(sy - cy, sx - cx)
    cands = []
    for sid, s in spots.items():
        x, y = s["x"], s["y"]
        q = spot_rate(rates, sid)
        if q < min_rate or math.hypot(x - cx, y - cy) > radius * 1.4:
            continue  # rarely a chest there, or far out at sea
        if any(math.hypot(x - bx, y - by) < br for bx, by, br in blocks):
            continue  # inside a no-go circle (or the middle)
        cands.append((sid, x, y, q, math.atan2(y - cy, x - cx)))
    cps = [tuple(map(float, c)) for c in checkpoints]
    cap = int(max_per_place) if max_per_place else None
    places = {sid: loot_place(x, y, pois) for sid, x, y, _, _ in cands} if cap else {}

    best = None
    for direction in (1, -1):
        items = [(((a - a0) * direction) % (2 * math.pi), sid, x, y, q) for sid, x, y, q, a in cands]
        stops = _plan_stops(items, (sx, sy), (cx, cy), radius, blocks, direction, count, cps, cap, places)
        if not stops:
            continue
        path = _walk(blocks, [(sx, sy)] + [(st[2], st[3]) for st in stops])
        metres = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:])) / 100
        chests = [st for st in stops if st[0] == "chest"]
        seconds = metres / RUN_SPEED + OPEN_TIME * len(chests)
        expected = sum(st[4] for st in chests)
        if best is None or expected / max(seconds, 1) > best["chests"] / max(best["seconds"], 1):
            best = {"start": start, "spots": [(st[1], st[2], st[3]) for st in chests],
                    "stops": [st[:4] for st in stops], "path": path, "metres": metres, "seconds": seconds,
                    "chests": expected, "direction": direction}
    out = best or {"start": start, "spots": [], "stops": [], "path": [start], "metres": 0.0, "seconds": 0.0,
                   "chests": 0.0, "direction": 1}
    out.update({"inner": (cx, cy, r_in) if avoid_middle else None, "avoid": [tuple(b) for b in avoid],
                "checkpoints": cps, "max_per_place": cap})
    return out


def loot_place(x: float, y: float, pois=()) -> str:
    """Which place a chest counts towards for max_per_place: the named place within 300 m, else its 300 m
    square of the map."""
    if pois:
        name, px, py = min(pois, key=lambda p: (p[1] - x) ** 2 + (p[2] - y) ** 2)
        if math.hypot(px - x, py - y) <= NEAR_POI_CM:
            return name
    return f"cell {int(x // NEAR_POI_CM)},{int(y // NEAR_POI_CM)}"


def _detour_len(a, b, blocks) -> float:
    """Extra walking a straight leg a->b needs to go round the circles it would cross (rough)."""
    extra = 0.0
    for bx, by, br in blocks:
        if math.hypot(a[0] - bx, a[1] - by) < br or math.hypot(b[0] - bx, b[1] - by) < br:
            continue  # starts or ends inside (a checkpoint placed there): walk straight in
        if _leg_crosses(a[0], a[1], b[0], b[1], bx, by, br * 0.95):
            extra += math.pi * br * 0.6
    return extra


def _plan_stops(items, start, centre, radius, blocks, direction, count, checkpoints, cap=None, places=None):
    """Stops in visiting order: ("chest", id, x, y, rate) and ("checkpoint", n, x, y, 0).
    items: (angle offset, id, x, y, rate). cap: at most that many chests per place (places: id -> place)."""
    cx, cy = centre
    rest = list(items)
    taken = {}  # place -> chests planned there
    stops, cur = [], start
    # share the chests out over the legs to the checkpoints (and one open leg after them), by length
    targets = list(checkpoints)
    legs, prev = [], start
    for t in targets:
        legs.append(math.hypot(t[0] - prev[0], t[1] - prev[1]) + 1.0)
        prev = t
    open_leg = sum(legs) / len(legs) if legs else 1.0  # the part after the last checkpoint
    total = sum(legs) + open_leg
    quotas = [round(count * leg / total) for leg in legs]
    quotas.append(max(0, count - sum(quotas)))

    def gain_to(it, target):
        """Loot per second for a chest on the way to target: the extra walking it costs vs. going straight."""
        d = math.hypot(it[2] - cur[0], it[3] - cur[1]) + _detour_len(cur, (it[2], it[3]), blocks)
        extra = d + math.hypot(target[0] - it[2], target[1] - it[3]) - math.hypot(target[0] - cur[0], target[1] - cur[1])
        return it[4] / (max(0.0, extra) / 100 / RUN_SPEED + OPEN_TIME)

    def gain_free(it):
        """Loot per second when free to roam: going backwards round the island costs extra."""
        d = math.hypot(it[2] - cur[0], it[3] - cur[1]) + _detour_len(cur, (it[2], it[3]), blocks)
        a_cur = math.atan2(cur[1] - cy, cur[0] - cx)
        fwd = ((math.atan2(it[3] - cy, it[2] - cx) - a_cur) * direction) % (2 * math.pi)
        back = 2 * math.pi - fwd if fwd > math.pi else 0.0
        return it[4] / ((d + back * radius * 0.6) / 100 / RUN_SPEED + OPEN_TIME)

    segment_start = 0
    for leg, quota in enumerate(quotas):
        target = targets[leg] if leg < len(targets) else None
        for _ in range(quota):
            if not rest:
                break
            nxt = max(rest, key=(lambda it: gain_to(it, target)) if target else gain_free)
            rest.remove(nxt)
            if cap:
                place = places.get(nxt[1])
                taken[place] = taken.get(place, 0) + 1
                if taken[place] >= cap:  # that place is done: drop its other chests
                    rest = [it for it in rest if places.get(it[1]) != place]
            stops.append(("chest", nxt[1], nxt[2], nxt[3], nxt[4]))
            cur = (nxt[2], nxt[3])
        # untangle this leg's chests (its ends stay put), then go to the checkpoint
        seg_start = (stops[segment_start - 1][2], stops[segment_start - 1][3]) if segment_start else start
        stops[segment_start:] = _untangle(stops[segment_start:], seg_start, target, blocks)
        if target:
            stops.append(("checkpoint", leg + 1, target[0], target[1], 0.0))
            cur = target
        segment_start = len(stops)
    return stops


def _untangle(stops, start, end, blocks):
    """2-opt within one leg: reverses any stretch that makes the walk shorter (going round no-go circles
    counts); the start and, if given, the end stay fixed."""
    def leg(a, b):
        return math.hypot(b[0] - a[0], b[1] - a[1]) + _detour_len(a, b, blocks)

    stops = list(stops)
    pts = [start] + [(st[2], st[3]) for st in stops] + ([end] if end else [])
    last = len(pts) - (1 if end else 0)  # index after the last movable point
    improved, rounds = True, 0
    while improved and rounds < 30:
        improved, rounds = False, rounds + 1
        for i in range(1, last - 1):
            for j in range(i + 1, last):
                after = pts[j + 1] if j + 1 < len(pts) else None
                before_cost = leg(pts[i - 1], pts[i]) + (leg(pts[j], after) if after else 0.0)
                after_cost = leg(pts[i - 1], pts[j]) + (leg(pts[i], after) if after else 0.0)
                if before_cost > after_cost + 1:
                    pts[i:j + 1] = reversed(pts[i:j + 1])
                    stops[i - 1:j] = reversed(stops[i - 1:j])
                    improved = True
    return stops


def _walk(blocks, points):
    """Points joined in order, going round any no-go circle a straight leg would cross (the shorter way)."""
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        out.extend(_round_circles(a, b, blocks, depth=0))
    return out


def _round_circles(a, b, blocks, depth):
    """The leg a->b as points (b last), with arcs round the first circle it crosses, then the rest."""
    if depth > 6:
        return [b]
    for bx, by, br in blocks:
        if math.hypot(a[0] - bx, a[1] - by) < br or math.hypot(b[0] - bx, b[1] - by) < br:
            continue
        if not _leg_crosses(a[0], a[1], b[0], b[1], bx, by, br * 0.95):
            continue
        aa, ab = math.atan2(a[1] - by, a[0] - bx), math.atan2(b[1] - by, b[0] - bx)
        ccw = (ab - aa) % (2 * math.pi)
        turn, sign = (ccw, 1) if ccw <= math.pi else (2 * math.pi - ccw, -1)  # the shorter way round
        steps = max(2, int(turn / math.radians(20)) + 1)
        rr = br * 1.08
        arc = [(bx + rr * math.cos(aa + sign * turn * i / steps), by + rr * math.sin(aa + sign * turn * i / steps))
               for i in range(1, steps)]
        out, prev = [], a
        for p in arc + [b]:  # the arc's legs may cross other circles: go round those too
            out.extend(_round_circles(prev, p, [c for c in blocks if c != (bx, by, br)], depth + 1))
            prev = p
        return out
    return [b]


def _leg_crosses(ax, ay, bx, by, cx, cy, r) -> bool:
    """Does the straight line from a to b pass within r of (cx, cy)?"""
    vx, vy = bx - ax, by - ay
    length2 = vx * vx + vy * vy
    if length2 <= 0:
        return math.hypot(ax - cx, ay - cy) < r
    f = max(0.0, min(1.0, ((cx - ax) * vx + (cy - ay) * vy) / length2))
    return math.hypot(ax + f * vx - cx, ay + f * vy - cy) < r
