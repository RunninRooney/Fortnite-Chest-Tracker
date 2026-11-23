"""The Chest Tracker app window: opened from the taskbar button (or F11 / right-click on the overlay).

One normal, resizable window for the lobby and between games, in a warm dark "frosted glass" look
(app_theme.py), with pill tabs along the top:
  * Home      - the four numbers that matter, chests per day, when you play, your squad, recent games,
                your best place to land, and a short "how it works" for new people
  * Games     - every game: chests, opened, broken, where you landed, squad (more columns on request);
                click one for its story, Ctrl+click a few to compare them on the map
  * Squad     - siblings with a tracker, console teammates, everyone you played with (from replays)
  * Treasures - prizes found by you and the team, the prize list by week, the Pirate Shop highlighted
  * Map       - the map window's view built in (all its tools) and "Where to land" (drop spot report)
  * Settings  - (gear icon) the same settings as before, the save logic is SettingsWindow's, reused

Little "?" dots explain each number on hover. The small click-through overlay stays the in-match display.
The numbers come from app_model.py; nothing here parses files or touches the game.
"""
from __future__ import annotations

import functools
import logging
import os
import sys
import time
import tkinter as tk

import app_model as M
import app_theme as T
import treasures
def apply_theme(root, theme: str | None = None):
    """Called by chest_tracker at start: the chosen colour theme for every window (map, dialogs too)."""
    if theme:
        T.use_theme(theme)
    T.apply_theme(root)

log = logging.getLogger("chest.app")

PAGES = [("overview", "Home", ""), ("matches", "Games", ""), ("team", "Squad", ""),
         ("treasures", "Treasures", ""), ("map", "Map", ""), ("settings", "Settings", "")]
NAV = [p for p in PAGES if p[0] != "settings"]
ALL_PAGES = dict((p[0], p[1]) for p in PAGES)
ALL_PAGES["player"] = "Player"  # one squadmate's games (from the Squad page)
ALL_PAGES["odds"] = "Odds"  # the treasure odds calculator (from the Treasures page)
NAV_LEFT, NAV_CENTER, NAV_RIGHT = ["overview", "matches", "team"], "map", ["treasures", "settings"]  # Map in the middle
PERIODS = [("Today", 1), ("7 days", 7), ("30 days", 30), ("All", None)]
TITLES = {
    "overview": ("Your chests", "Everything Chest Tracker counted, at a glance."),
    "matches": ("Your games", "Every match it counted. Click a game for its story, Ctrl+click a few to compare them on the map."),
    "team": ("Your squad", "Everyone sharing a team code with you, and who you've played with. Click someone "
                           "to see their games."),
    "treasures": ("Treasures", "Treasure hunt prizes found by you and your team."),
    "map": ("Map", "Your routes, chests and favourite places on the island."),
    "settings": ("Settings", "How the overlay looks, who you are, and your team."),
    "player": ("Player", "Their games with you, from the replays on this PC."),
    "odds": ("Treasure odds", "Your chances of a Pirate Shop access (and the other prizes), from the chests you've opened."),
}
HEAT_MENU = [("Chests opened", "chests"), ("Landing spots", "landing"), ("Eliminations", "kills"),
             ("Where you spend time", "time"), ("Routes", "routes")]
# Shown wherever squadmates' chests are counted from my replays (Squad page, Games' squad total).
SQUAD_COUNT_NOTE = ("Squadmates' counts are the chests they opened. Chests they broke (pickaxe or builds) mostly aren't included: your replay only sees the ones broken near you. A squadmate who runs Chest Tracker too shares their own full count instead.")
HELP = {
    "total": "Every chest you've opened or broken since the season started, on PC (and on console when a "
             "teammate's tracker saw you).",
    "game": "Chests in the match you're playing now, or the last one. Counted live from the screen, then "
            "checked against the match replay when it ends.",
    "team": "Everyone on your team code added together, plus console teammates you added in Settings.",
    "today": "Chests you got today (since midnight).",
    "opened": "Chests you opened by holding the interact button.",
    "broken": "Chests you smashed open with your pickaxe, or broke by placing a build on them. They still count.",
    "squad_total": "Everyone in your squad in that game added together: you plus each teammate your replay saw. "
                   "Only for games recorded on this PC. " + SQUAD_COUNT_NOTE,
    "status": "Checked = the match replay confirmed the count. Live = still counting. Checking = the replay "
              "is being read.",
}


# orange light inside some cards: (x, y as fractions of the card, radius, colour, strength)
def GLOW_BOTTOM():  # noqa: N802  (orange light inside cards, in the theme's accent)
    return [(0.5, 1.2, 0.8, T.acc(0.02), 0.62), (0.05, 1.1, 0.45, T.acc(0.18), 0.25)]


def GLOW_RIGHT():  # noqa: N802
    return [(1.08, 0.55, 0.6, T.acc(0.04), 0.58)]


def GLOW_LEFT():  # noqa: N802
    return [(-0.04, 0.5, 0.5, T.acc(0.02), 0.6)]


def GLOW_CORNER():  # noqa: N802
    return [(1.02, -0.05, 0.75, T.acc(0.08), 0.34)]


GLOW_GOLD = [(0.2, 0.5, 0.65, (255, 172, 56), 0.55)]  # treasure gold, whatever the theme


def show(app, page: str | None = None):
    """Opens (or brings back) the app window. Called on the Tk thread."""
    w = getattr(app, "_app_window", None)
    if w is not None:
        try:
            if w.win.winfo_exists():
                w.show(page)
                return w
        except tk.TclError:
            pass
    w = app._app_window = AppWindow(app)
    w.show(page)
    return w


class Ref:
    """Text shown on a card, with a Label-like configure / cget (used by tests and SettingsWindow's methods)."""

    def __init__(self, text="", on_change=None, fg=None):
        self.text, self.fg, self.on_change = text, fg, on_change

    def configure(self, text=None, foreground=None, fg=None, **_kw):
        changed = False
        if text is not None and text != self.text:
            self.text, changed = text, True
        col = foreground if foreground is not None else fg
        if col is not None and col != self.fg:
            self.fg, changed = col, True
        if changed and self.on_change:
            self.on_change()

    config = configure

    def cget(self, key):
        return self.text if key == "text" else self.fg


def map_colors() -> dict:
    """The map's marker colours (from map_window, with fallbacks)."""
    try:
        import map_window as MW

        return {"me": MW.ME, "mates": MW.MATES, "kill": MW.KILL, "storm": MW.STORM, "loot": MW.LOOTED, "spot": MW.SPOT,
                "dark": MW.DARK}
    except Exception:
        return {"me": (255, 197, 61), "mates": [(80, 200, 255), (255, 110, 180)], "kill": (255, 70, 70),
                "storm": (150, 60, 220), "loot": (96, 214, 120), "spot": (235, 238, 255), "dark": (16, 17, 28)}


def compare_colors() -> list:
    try:
        import map_window as MW

        return list(MW.COMPARE)
    except Exception:
        return [(255, 197, 61), (80, 200, 255), (255, 110, 180), (130, 225, 120), (185, 150, 255), (255, 150, 90)]


def key_icon(kind: str, size: int = 28, col=None):
    """A crisp picture of one map mark (cached; never draw onto it)."""
    return _key_icon(kind, int(size), tuple(col) if col else None)


@functools.lru_cache(maxsize=256)
def _key_icon(kind: str, size: int = 28, col=None):
    """A crisp picture of one map mark (drawn big, then scaled down)."""
    from PIL import Image, ImageDraw

    mc = map_colors()
    col = tuple(col or mc["me"])
    dark = tuple(mc["dark"])
    k = 4
    S = size * k
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = S / 2
    if kind == "route":
        d.line([(S * 0.08, S * 0.7), (S * 0.4, S * 0.35), (S * 0.62, S * 0.6), (S * 0.92, S * 0.28)], fill=dark + (255,),
               width=int(S * 0.2), joint="curve")
        d.line([(S * 0.08, S * 0.7), (S * 0.4, S * 0.35), (S * 0.62, S * 0.6), (S * 0.92, S * 0.28)], fill=col + (255,),
               width=int(S * 0.12), joint="curve")
    elif kind == "opened":
        r = S * 0.3
        d.ellipse((c - r - S * 0.05, c - r - S * 0.05, c + r + S * 0.05, c + r + S * 0.05), fill=dark + (255,))
        d.ellipse((c - r, c - r, c + r, c + r), fill=col + (255,))
    elif kind == "broken":
        r = S * 0.26
        d.rectangle((c - r - S * 0.05, c - r - S * 0.05, c + r + S * 0.05, c + r + S * 0.05), fill=dark + (255,))
        d.rectangle((c - r, c - r, c + r, c + r), fill=col + (255,))
    elif kind == "build":
        q = S * 0.36
        d.polygon([(c, c - q), (c - q, c + q * 0.8), (c + q, c + q * 0.8)], fill=col + (255,), outline=dark + (255,))
    elif kind == "search":
        r = S * 0.3
        d.ellipse((c - r, c - r, c + r, c + r), outline=dark + (255,), width=int(S * 0.14))
        d.ellipse((c - r, c - r, c + r, c + r), outline=col + (255,), width=int(S * 0.07))
    elif kind == "landing":
        q = S * 0.42
        d.polygon([(c, c - q * 1.1), (c - q, c + q * 0.55), (c + q, c + q * 0.55)], fill=dark + (255,))
        q = S * 0.32
        d.polygon([(c, c - q * 1.1), (c - q * 0.8, c + q * 0.45), (c + q * 0.8, c + q * 0.45)], fill=col + (255,))
    elif kind == "kill":
        kc = tuple(mc["kill"])
        d.line([(S * 0.05, S * 0.85), (c, c)], fill=kc + (200,), width=int(S * 0.08))
        q = S * 0.24
        for a, b in (((c - q, c - q), (c + q, c + q)), ((c - q, c + q), (c + q, c - q))):
            d.line([a, b], fill=dark + (255,), width=int(S * 0.2))
            d.line([a, b], fill=kc + (255,), width=int(S * 0.11))
    elif kind == "storm":
        sc = tuple(mc["storm"])
        d.rectangle((0, 0, S, S), fill=sc + (60,))
        r = S * 0.36
        d.ellipse((c - r, c - r, c + r, c + r), fill=(0, 0, 0, 0), outline=sc + (255,), width=int(S * 0.08))
    elif kind == "loot":  # white line from the usual landing spot (white dot) to green numbered stops
        lc = tuple(mc["loot"])
        d.line([(S * 0.15, S * 0.8), (S * 0.75, S * 0.28)], fill=(255, 255, 255, 230), width=int(S * 0.07))
        r = S * 0.12
        d.ellipse((S * 0.15 - r, S * 0.8 - r, S * 0.15 + r, S * 0.8 + r), fill=(255, 255, 255, 255))
        r = S * 0.2
        d.ellipse((S * 0.75 - r, S * 0.28 - r, S * 0.75 + r, S * 0.28 + r), fill=lc + (255,), outline=dark + (255,),
                  width=int(S * 0.04))
    elif kind == "player":  # an in-game style arrowhead: the player's colour, white border, dark edge
        def head(scale):
            q = S * scale
            return [(c, c - q), (c + q * 0.78, c + q * 0.78), (c, c + q * 0.38), (c - q * 0.78, c + q * 0.78)]
        d.polygon(head(0.46), fill=dark + (255,))
        d.polygon(head(0.40), fill=(255, 255, 255, 255))
        d.polygon(head(0.30), fill=col + (255,))
    elif kind == "random":  # a random squad fill: grey
        gc = (170, 170, 182)
        try:
            import map_window as MW

            gc = tuple(getattr(MW, "RANDOM", gc))
        except Exception:
            pass
        r = S * 0.3
        d.ellipse((c - r - S * 0.05, c - r - S * 0.05, c + r + S * 0.05, c + r + S * 0.05), fill=dark + (255,))
        d.ellipse((c - r, c - r, c + r, c + r), fill=gc + (255,))
    elif kind == "avoid":  # the middle the loot route avoids: a dark disc, faint ring
        r = S * 0.4
        d.ellipse((c - r, c - r, c + r, c + r), fill=(0, 0, 0, 120), outline=(255, 255, 255, 90), width=int(S * 0.04))
    elif kind == "nogo":  # a no-go zone: red circle with an X
        rc = (240, 70, 70)
        r = S * 0.4
        d.ellipse((c - r, c - r, c + r, c + r), fill=rc + (60,), outline=rc + (255,), width=int(S * 0.06))
        q = S * 0.12
        d.line([(c - q, c - q), (c + q, c + q)], fill=rc + (255,), width=int(S * 0.07))
        d.line([(c - q, c + q), (c + q, c - q)], fill=rc + (255,), width=int(S * 0.07))
    elif kind == "checkpoint":  # a checkpoint: a blue diamond
        bc = (80, 160, 255)
        q = S * 0.36
        d.polygon([(c, c - q), (c + q, c), (c, c + q), (c - q, c)], fill=bc + (255,), outline=dark + (255,))
    elif kind == "vehicle":  # a dashed line: riding (the replay doesn't record you then)
        for i in range(4):
            x0 = S * (0.06 + i * 0.24)
            d.line([(x0, S * (0.68 - i * 0.12)), (x0 + S * 0.13, S * (0.68 - i * 0.12 - 0.065))], fill=dark + (255,),
                   width=int(S * 0.16))
            d.line([(x0, S * (0.68 - i * 0.12)), (x0 + S * 0.13, S * (0.68 - i * 0.12 - 0.065))], fill=col + (255,),
                   width=int(S * 0.09))
    elif kind == "rough_kill":  # a faint small X: a late, batched teammate count, only roughly there
        kc = tuple(mc["kill"])
        q = S * 0.16
        for a, b in (((c - q, c - q), (c + q, c + q)), ((c - q, c + q), (c + q, c - q))):
            d.line([a, b], fill=kc + (140,), width=int(S * 0.08))
    elif kind == "spot":
        sp = tuple(mc["spot"])
        r = S * 0.14
        d.ellipse((c - r, c - r, c + r, c + r), fill=sp + (150,))
        r = S * 0.3
        d.ellipse((c - r, c - r, c + r, c + r), outline=tuple(mc["loot"]) + (255,), width=int(S * 0.07))
    elif kind == "heat":
        for i in range(S):
            t = i / S
            colr = (int(30 + 225 * t), int(10 + 200 * t ** 2), int(40 + 60 * t * (1 - t)))
            d.line([(i, S * 0.3), (i, S * 0.7)], fill=colr + (255,))
    return im.resize((size, size), Image.LANCZOS)


class KeyView(tk.Frame):
    """The map key: each kind of mark, drawn, with a word on what it means; and the colours of overlaid games."""

    ITEMS = [("player", "A player", "Points the way they're heading. Their name is on the label above."),
             ("route", "Your route", "Where you went after landing. Teammates get their own colours."),
             ("landing", "Where you landed", None),
             ("opened", "Chest opened", None),
             ("broken", "Chest broken", "Smashed with a pickaxe, or broken by a build you placed (a triangle)."),
             ("random", "Grey player", "A random squad fill: shown on the map, but not saved or counted."),
             ("search", "Teammate's chest, far away", "Their replay count, not seen up close."),
             ("vehicle", "Dashed line", "Riding a vehicle: the replay doesn't record you then."),
             ("kill", "Elimination", "The red line goes from the shooter to where they went down."),
             ("rough_kill", "Faint small X", "A teammate's elimination counted late, so only roughly there."),
             ("storm", "Storm", "Purple outside the circle is the storm; the white circle is the next one."),
             ("loot", "Loot route", "From \"Plan route\": starts where you land (white dot), then the numbered "
                                     "chests in order, round the outside of the island."),
             ("avoid", "Dark circle", "The middle of the island the route stays out of."),
             ("nogo", "Red circle", "A no-go zone you drew: the route stays out of it."),
             ("checkpoint", "Blue diamond", "A checkpoint you placed: the route goes through it, in order."),
             ("spot", "Known chest spot", "Brighter = more often really there. Green ring = looted this game."),
             ("heat", "Heatmap", "Dark = rarely, bright = often.")]

    def __init__(self, parent, fonts, owner):
        super().__init__(parent, bg=T.WELL)
        self.f, self.owner = fonts, owner
        self.canvas = tk.Canvas(self, bg=T.WELL, highlightthickness=0, bd=0)
        self.bar = T.SlimScroll(self, self.canvas, bg=T.WELL)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas._wheel_target = True
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self._imgs = []

    def draw(self):
        from PIL import ImageTk

        c, f = self.canvas, self.f
        c.delete("all")
        self._imgs = []
        W = max(160, c.winfo_width())
        y = 6
        overlay = list(getattr(self.owner, "map_overlay", []))
        if len(overlay) >= 2:
            c.create_text(12, y + 8, text="GAMES ON THE MAP", font=f.cap, fill=T.DIM, anchor="w")
            y += 24
            d = self.owner._snap or {}
            by_file = {r["replay"]: r for r in d.get("rows", []) if r.get("replay")}
            cols = compare_colors()
            for i, file in enumerate(overlay[:len(cols)]):
                r = by_file.get(file)
                p = T.photo_of(c, key_icon("route", 28, cols[i % len(cols)]))
                self._imgs.append(p)
                c.create_image(12, y, image=p, anchor="nw")
                label = M.fmt_when(r["started"], d.get("now")) + f" · {r['chests']} chests" if r else file
                c.create_text(50, y + 14, text=label, font=f.bold, fill=T.TEXT, anchor="w")
                y += 34
            y += 8
        c.create_text(12, y + 8, text="MARKS", font=f.cap, fill=T.DIM, anchor="w")
        y += 26
        for kind, title, sub in self.ITEMS:
            tile, _ = T.pill_image(36, 36, T.hl(20), T.hl(8), radius=9, outline=T.hl(40))
            pt = T.photo_of(c, tile)
            pi = T.photo_of(c, key_icon(kind, 28))
            self._imgs += [pt, pi]
            c.create_image(10, y, image=pt, anchor="nw")
            c.create_image(14, y + 4, image=pi, anchor="nw")
            if sub:
                c.create_text(58, y + 9, text=title, font=f.bold, fill=T.TEXT, anchor="w")
                item = c.create_text(58, y + 20, text=sub, font=f.small, fill=T.MUTED, anchor="nw", width=W - 70)
                bb = c.bbox(item)
                y = max(y + 44, (bb[3] if bb else y + 36) + 10)
            else:
                c.create_text(58, y + 18, text=title, font=f.bold, fill=T.TEXT, anchor="w")
                y += 46
        c.configure(scrollregion=(0, 0, W, y + 8))


class PlaybackBar(tk.Canvas):
    """Replay controls under the map, like a video player: a row of buttons (speed, back 10 s, play / pause,
    forward 10 s, who to watch) over a wide timeline with the game's events marked on it. Click or drag the
    timeline to go to any moment. Only shown while a replay (or overlaid games) is on the map."""

    CTRL = 50     # the row of buttons
    LANE = 26     # one row of the timeline per player
    SEEK = 10     # seconds the seek buttons jump

    def __init__(self, parent, owner):
        super().__init__(parent, bg=T.WELL, highlightthickness=0, bd=0, height=owner.px(120))
        self.owner, self.f = owner, owner.f
        self.lanes = []      # [(label, colour, [(t, kind, text)])]
        self._marks = []     # (x, y, text) for hover
        self.want_h = 0
        self._key = None
        self._imgs = []
        self._drag = False
        self._geo = None   # (x0, x1, y) of the timeline track
        self._shown = False
        self.bind("<Configure>", lambda e: self.refresh(force=True))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Motion>", self._hover, add="+")
        self.bind("<Leave>", lambda e: self.owner.tip.hide())
        self._tick()

    # ---- what the map is doing (its public playback() when there, else read carefully) ----------------
    def state(self):
        mw = self.owner.embedded_map
        if mw is None:
            return None
        if hasattr(mw, "playback"):
            try:
                st = dict(mw.playback())
                return st if st.get("length") else None
            except Exception:
                log.exception("playback state")
                return None
        try:
            length = mw._timeline_length()
            if not length:
                return None
            rec = None if mw.compare_files else mw._shown_rec
            players = list(mw._player_names(rec)) if rec is not None and hasattr(mw, "_player_names") else []
            wp = mw._watched(rec) if rec is not None and hasattr(mw, "_watched") else None
            watched = wp.get("name") if isinstance(wp, dict) else wp
            try:
                import map_window as MWmod

                speeds = list(MWmod.SPEEDS)
            except Exception:
                speeds = ["1x", "2x", "5x", "10x", "30x"]
            return {"length": length, "t": mw.t, "playing": bool(mw.playing), "speed": mw.speed.get(), "speeds": speeds,
                    "events": list(getattr(mw, "_events", []) or []) if rec is not None else [], "players": players,
                    "watched": watched, "compare": bool(mw.compare_files)}
        except Exception:
            return None

    # ---- actions ------------------------------------------------------------------------------------
    def toggle(self):
        mw = self.owner.embedded_map
        if mw is not None:
            mw.toggle_play()
            self.refresh(force=True)

    def seek(self, t):
        mw = self.owner.embedded_map
        st = self.state()
        if mw is None or not st:
            return
        t = max(0.0, min(float(t), st["length"]))
        if hasattr(mw, "seek"):
            mw.seek(t)
        else:
            mw.t = None if t >= st["length"] - 0.5 else t
            mw._last_tick = time.perf_counter()
            mw._changed()
        self.refresh(force=True)

    def step(self, d):
        st = self.state()
        if st:
            now = st["length"] if st["t"] is None else st["t"]
            self.seek(now + d * self.SEEK)

    def set_speed(self, label):
        mw = self.owner.embedded_map
        if mw is None:
            return
        if hasattr(mw, "set_speed"):
            mw.set_speed(label)
        else:
            mw.speed.set(label)
        self.refresh(force=True)

    def watch(self, name):
        mw = self.owner.embedded_map
        if mw is None:
            return
        if hasattr(mw, "watch") and callable(getattr(mw, "watch")):
            mw.watch(name)
        else:
            getattr(mw, "watch_var", getattr(mw, "watch", None)).set(f"Watch: {name}")
            if hasattr(mw, "_watch_changed"):
                mw._watch_changed()
        self.refresh(force=True)
        if self.owner.info is not None:
            self.owner.info.refresh(force=True)

    def _speed_menu(self):
        st = self.state() or {}
        self.owner._menu([(("✓  " if v == st.get("speed") else "     ") + f"Speed {v}", lambda v=v: self.set_speed(v))
                          for v in st.get("speeds", [])])

    def _watch_menu(self):
        st = self.state() or {}
        self.owner._menu([(("✓  " if n == st.get("watched") else "     ") + n, lambda n=n: self.watch(n))
                          for n in st.get("players", [])] or [("Nobody to watch here", lambda: None)])

    # ---- timeline dragging --------------------------------------------------------------------------
    def _t_at(self, x):
        st = self.state()
        if not st or not self._geo:
            return None
        x0, x1, _y0, _y1 = self._geo
        return max(0.0, min(1.0, (x - x0) / max(1, x1 - x0))) * st["length"]

    def _on_track(self, e):
        if not self._geo:
            return False
        x0, x1, y0, y1 = self._geo
        return x0 - 10 <= e.x <= x1 + 10 and y0 - 14 <= e.y <= y1 + 4

    def _press(self, e):
        if self._on_track(e):
            self._drag = True
            t = self._t_at(e.x)
            if t is not None:
                self.seek(t)

    def _motion(self, e):
        if self._drag:
            t = self._t_at(e.x)
            if t is not None:
                self.seek(t)

    def _release(self, e):
        self._drag = False

    def _hover(self, e):
        if self._drag or not self._marks:
            return
        best = min(self._marks, key=lambda m: (m[0] - e.x) ** 2 + (m[1] - e.y) ** 2)
        if (best[0] - e.x) ** 2 + (best[1] - e.y) ** 2 <= 81:
            self.owner.tip.show(best[2], e.x_root, e.y_root)
        else:
            self.owner.tip.hide()

    # ---- what goes in the lanes -------------------------------------------------------------------
    def _lanes(self, st):
        """[(label, colour, [(t, kind, text)])]: the players of the shown game (watched first), or the
        overlaid games (my marks in each, on the shared timeline)."""
        mw = self.owner.embedded_map
        out = []
        try:
            items = [tuple(x)[:3] for x in (mw.legend_items() if hasattr(mw, "legend_items") else [])]
            if st.get("compare"):
                recs = mw._compare_recs() if hasattr(mw, "_compare_recs") else []
                self.lane_totals = {}
                names = (self.owner._snap or {}).get("names") or set()
                cols = [c for c, _l, k in items if k == "game"]
                for i, rec in enumerate(recs):
                    zero = mw._compare_zero(rec) if hasattr(mw, "_compare_zero") else 0
                    me = {pl.get("name") for pl in rec["map"].get("players", []) if M.is_me(pl.get("name"),
                                                                                           pl.get("recorder"), names)}
                    marks = [(c["t"] - zero, "broken" if c.get("broken") else "chest",
                              f"{M.fmt_length(c['t'] - zero)}  {'broke' if c.get('broken') else 'opened'} a chest")
                             for c in rec["map"].get("chests", []) if c.get("who") in me]
                    for pl in rec["map"].get("players", []):
                        if pl.get("name") in me:
                            for k in pl.get("kills") or []:
                                kt = (k.get("t", 0) if isinstance(k, dict) else k[0]) - zero
                                marks.append((kt, "kill", f"{M.fmt_length(kt)}  elimination"))
                    label = M.fmt_when(rec.get("start") or 0, time.time())
                    out.append((label, tuple(cols[i]) if i < len(cols) else map_colors()["me"], marks))
                return out
            rec = mw._shown_rec
            if rec is None:
                return []
            totals = M.replay_counts(((self.owner._snap or {}).get("files") or {}).get(rec.get("file")))
            self.lane_totals = {n: o + b for n, (o, b) in totals.items()}
            cols = {lbl.split(":")[0].replace("  (you)", ""): col for col, lbl, _k in items}
            order = [lbl.split(":")[0] for _c, lbl, _k in items]
            players = rec["map"].get("players", [])
            players = sorted(players, key=lambda pl: order.index(pl.get("name")) if pl.get("name") in order else 99)
            for i, pl in enumerate(players):
                n = pl.get("name") or "?"
                marks = [(c["t"], "broken" if c.get("broken") else "chest",
                          f"{M.fmt_length(c['t'])}  {n} {'broke' if c.get('broken') else 'opened'} a chest"
                          + (" (far from the recorder: rough spot)" if c.get("source") == "search" else ""))
                         for c in rec["map"].get("chests", []) if c.get("who") == n]
                for k in pl.get("kills") or []:
                    kt = k.get("t", 0) if isinstance(k, dict) else k[0]
                    victim = k.get("victim") if isinstance(k, dict) else None
                    marks.append((kt, "kill", f"{M.fmt_length(kt)}  {n} eliminated {victim or 'someone'}"))
                if pl.get("landing"):
                    marks.append((pl["landing"][0], "landing", f"{M.fmt_length(pl['landing'][0])}  {n} landed"))
                col = cols.get(n) or (map_colors()["me"] if pl.get("recorder") else map_colors()["mates"][i % 5])
                out.append((n, tuple(col), marks))
        except Exception:
            log.exception("timeline lanes")
        return out

    # ---- drawing ------------------------------------------------------------------------------------
    def _tick(self):
        try:
            if not self.winfo_exists():
                return
            self.refresh()
        except tk.TclError:
            return
        except Exception:
            log.exception("playback bar")
        st = getattr(self, "_last", None)
        self.after(40 if st and st.get("playing") else 200, self._tick)

    def refresh(self, force=False):
        st = self.state()
        self._last = st
        owner = self.owner
        want = st is not None and owner.page == "map" and owner.map_tab == "map" and not getattr(owner, "plan_on", False)
        if want:
            lanes_key = (st.get("compare"), st["watched"], tuple(st["players"]), len(st["events"]),
                         getattr(owner.embedded_map, "legend_version", None), st["length"])
            if force or lanes_key != getattr(self, "_lanes_key", None):
                self._lanes_key = lanes_key
                self.lanes = self._lanes(st) if getattr(owner, "show_lanes", True) else []
                force = True
        p = owner.px
        if not want:
            want_h = 0
        elif self.lanes:
            want_h = p(self.CTRL) + p(30) + p(self.LANE) * len(self.lanes) + p(12)
        else:  # simple: the buttons and a slim scrub bar
            want_h = p(self.CTRL) + p(40)
        if want != self._shown or want_h != self.want_h:
            self._shown, self.want_h = want, want_h
            if want:
                self.configure(height=want_h)
            owner.map_card.repaint()  # the card lays the map out around the bar
        if not want:
            return
        key = (st["length"], st["playing"], st["speed"], st["watched"], tuple(st["players"]), st["compare"],
               self.winfo_width(), len(self.lanes))
        if force or key != self._key:
            self._key = key
            self._draw(st)
        self._move(st)

    def _draw(self, st):
        c, f, p = self, self.f, self.owner.px
        c.delete("all")
        self._imgs = []
        self._marks = []
        W = max(400, c.winfo_width())
        mid = p(24)
        # the controls, centred: speed | back | play / pause | forward | watch
        play_d = p(42)
        items = [("speed", f"{st['speed']}  ▾"), ("back", f"‹‹  {self.SEEK}s"), ("play", None), ("fwd", f"{self.SEEK}s  ››")]
        if st["players"] and not st["compare"]:
            items.append(("watch", f"Watch: {st['watched'] or st['players'][0]}  ▾"))
        widths = [play_d if k == "play" else f.small_bold.measure(label) + p(26) for k, label in items]
        gap = p(10)
        x = W / 2 - (sum(widths) + gap * (len(items) - 1)) / 2
        for (k, label), w in zip(items, widths):
            tag = f"pb_{k}"
            if k == "play":
                img, m = T.circle_image(play_d, T.PRIMARY[0], T.PRIMARY[1], glow=T.acc(0, 120))
                ph = T.photo_of(c, img)
                self._imgs.append(ph)
                c.create_image(x - m, mid - play_d / 2 - m, image=ph, anchor="nw", tags=(tag,))
                cx = x + play_d / 2
                if st["playing"]:
                    for dx in (-p(6), p(6)):
                        c.create_rectangle(cx + dx - p(3), mid - p(9), cx + dx + p(3), mid + p(9), fill=T.ON_ACCENT,
                                           outline="", tags=(tag,))
                else:
                    c.create_polygon(cx - p(6), mid - p(10), cx - p(6), mid + p(10), cx + p(11), mid, fill=T.ON_ACCENT,
                                     outline="", tags=(tag,))
            else:
                h = p(32)
                img, _m = T.pill_image(int(w), h, T.GHOST[0], T.GHOST[1], radius=p(10), outline=T.hl(50))
                ph = T.photo_of(c, img)
                self._imgs.append(ph)
                c.create_image(x, mid - h / 2, image=ph, anchor="nw", tags=(tag,))
                c.create_text(x + w / 2, mid, text=label, font=f.small_bold, fill=T.TEXT, tags=(tag,))
            fn = {"speed": self._speed_menu, "back": lambda: self.step(-1), "play": self.toggle,
                  "fwd": lambda: self.step(1), "watch": self._watch_menu}[k]
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, fn=fn: fn())
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
            x += w + gap
        # the timeline: a time ruler, then one lane per player with their chests, broken chests and elims
        label_w = p(150) if self.lanes else p(44)
        x0, x1 = p(14) + label_w, W - p(58)
        ruler = p(self.CTRL) + p(10)
        y0 = ruler + p(18)
        y1 = y0 + (p(self.LANE) * len(self.lanes) if self.lanes else p(8))
        self._geo = (x0, x1, ruler, y1)
        L = st["length"] or 1
        step = next(s_ for s_ in (30, 60, 120, 180, 300, 600) if (x1 - x0) / (L / s_) >= p(56))
        tt = 0
        while tt <= L:
            tx = x0 + (x1 - x0) * tt / L
            c.create_line(tx, ruler + p(8), tx, y1, fill=T.WELL_LINE)
            c.create_text(tx, ruler + p(1), text=M.fmt_length(tt), font=f.cap, fill=T.DIM, tags=("rl",))
            tt += step
        if not self.lanes:  # simple: a slim track with the game's moments as ticks
            track, _ = T.pill_image(int(x1 - x0), p(8), T.hl(40), T.hl(24), outline=T.hl(30))
            pt = T.photo_of(c, track)
            self._imgs.append(pt)
            c.create_image(x0, y0, image=pt, anchor="nw")
            try:
                import map_window as MWmod

                ecols = MWmod.EVENT_COLORS
            except Exception:
                ecols = {}
            for ev in st["events"]:
                ex = x0 + (x1 - x0) * min(1.0, ev["t"] / L)
                c.create_line(ex, y0 - p(6), ex, y0, fill=T.hexc(ecols.get(ev.get("kind"), (200, 200, 200))), width=2)
                self._marks.append((ex, y0 - p(3), f"{M.fmt_length(ev['t'])}  {ev.get('text', '')}"))
        for i, (label, col, marks) in enumerate(self.lanes):
            ly = y0 + i * p(self.LANE)
            cy = ly + p(self.LANE) / 2
            if i % 2 == 0:
                c.create_rectangle(x0 - p(4), ly, x1 + p(4), ly + p(self.LANE), fill=T.ROW_HOVER, outline="")
            dot, _ = T.circle_image(p(10), col, col)
            pd = T.photo_of(c, dot)
            self._imgs.append(pd)
            c.create_image(p(16), cy - p(5), image=pd, anchor="nw")
            c.create_text(p(32), cy, text=T.fit_text(label, f.small_bold, label_w - p(30)), font=f.small_bold,
                          fill=T.TEXT, anchor="w")
            hexcol = T.hexc(col)
            for t, kind, text in sorted(marks):
                mx = x0 + (x1 - x0) * max(0.0, min(1.0, t / L))
                r = p(4)
                if kind == "chest":
                    c.create_oval(mx - r, cy - r, mx + r, cy + r, fill=hexcol, outline="#101014")
                elif kind == "broken":
                    c.create_rectangle(mx - r, cy - r, mx + r, cy + r, fill=hexcol, outline="#101014")
                elif kind == "kill":
                    for a, b in (((mx - r, cy - r), (mx + r, cy + r)), ((mx - r, cy + r), (mx + r, cy - r))):
                        c.create_line(*a, *b, fill="#ff5a5a", width=p(2))
                elif kind == "landing":
                    c.create_polygon(mx, cy - r - p(1), mx - r, cy + r, mx + r, cy + r, fill="#f2f2f5",
                                     outline="#101014")
                self._marks.append((mx, cy, text))
            # chests so far, at the end of the lane
            c.create_text(W - p(16), cy, text="0", font=f.small_bold, fill=T.ACCENT_SOFT, anchor="e", tags=(f"cnt{i}",))
        # what hasn't happened yet is dimmed; the playhead runs down all lanes
        c.create_rectangle(x1, y0, x1, y1, fill=T.WELL, outline="", stipple="gray50", tags=("pb_future",))
        c.create_line(x0, ruler + p(8), x0, y1, fill=T.TEXT, width=p(2), tags=("pb_head",))
        c.create_oval(0, 0, 0, 0, fill="#ffffff", outline=T.ACCENT, width=p(3), tags=("pb_knob",))
        c.create_text(0, 0, text="0:00", font=f.small_bold, fill=T.ON_ACCENT, tags=("pb_now",))
        c.create_rectangle(0, 0, 0, 0, fill=T.ACCENT, outline="", tags=("pb_nowbg",))
        c.tag_raise("pb_now")
        self._L = L

    def _move(self, st):
        if not self._geo:
            return
        x0, x1, ruler, y1 = self._geo
        p = self.owner.px
        L = st["length"] or 1
        t = L if st["t"] is None else st["t"]
        x = x0 + (x1 - x0) * max(0.0, min(1.0, t / L))
        self.coords("pb_future", x, ruler + p(18), x1 + p(4), y1)
        self.coords("pb_head", x, ruler + p(10), x, y1)
        text = M.fmt_length(t)
        w = self.f.small_bold.measure(text) + p(12)
        self.coords("pb_nowbg", x - w / 2, ruler - p(7), x + w / 2, ruler + p(10))
        self.coords("pb_now", x, ruler + p(1))
        self.itemconfigure("pb_now", text=text)
        for item in self.find_withtag("rl"):  # ruler times under the playhead's label hide
            bb = self.bbox(item)
            if bb:
                self.itemconfigure(item, state="hidden" if bb[0] < x + w / 2 + p(2) and bb[2] > x - w / 2 - p(2)
                                   else "normal")
        totals = getattr(self, "lane_totals", {}) or {}
        for i, (label, _col, marks) in enumerate(self.lanes):
            n = sum(1 for tm, kind, _x in marks if kind in ("chest", "broken") and tm <= t)
            if label in totals:  # the replay's real total at the end, never past it before
                n = totals[label] if t >= L - 1 else min(n, totals[label])
            self.itemconfigure(f"cnt{i}", text=str(n))


class GameInfo(tk.Canvas):
    """Over the map's top-left, on see-through dark glass: the game being shown and everyone in it, in their
    colours on the map, with their chests so far (following the timeline). Click a player to watch them.
    While overlaying games: each game in its colour; on the heatmap: what it shows."""

    WIDTH = 220
    ALPHA = 168  # how dark the glass is (0-255): the map shows through

    def __init__(self, parent, owner):
        super().__init__(parent, bg=T.WELL, highlightthickness=0, bd=0)
        self.owner, self.f = owner, owner.f
        self._key = None
        self._imgs = []
        self._bd = None          # the backdrop: the map under the panel, darkened
        self._bd_key = None
        self._bd_at = 0.0
        self._placed = None
        self.H = 100
        self._tick()

    def _tick(self):
        try:
            if not self.winfo_exists():
                return
            owner = self.owner
            show = (owner.page == "map" and getattr(owner, "map_tab", "map") == "map" and owner.embedded_map is not None
                    and getattr(owner, "show_players", True))
            if show:
                self.refresh()
                self._backdrop()
                mw = owner.embedded_map  # the map switched games (it does so on its next frame): relabel the picker
                shown = (getattr(getattr(mw, "_shown_rec", None) or {}, "get", lambda k: None)("file"),
                         tuple(getattr(mw, "compare_files", ()) or ()), mw.view.get())
                if shown != getattr(self, "_shown_game", None):
                    self._shown_game = shown
                    owner.map_card.repaint()
            elif self._placed is not None:
                self.place_forget()
                self._placed = None
        except tk.TclError:
            return
        except Exception:
            log.exception("game info")
        self.after(150, self._tick)

    def _place(self):
        p = self.owner.px
        mw = self.owner.embedded_map
        try:
            ox = mw.canvas.winfo_rootx() - self.master.winfo_rootx()
            oy = mw.canvas.winfo_rooty() - self.master.winfo_rooty()
        except Exception:
            ox = oy = 0
        geo = (ox + p(10), oy + p(10), p(self.WIDTH), self.H)
        if geo != self._placed:
            self._placed = geo
            self.place(x=geo[0], y=geo[1], width=geo[2], height=geo[3])
            self.tk.call("raise", self._w)
            self._bd_key = None

    def _backdrop(self):
        """The map under the panel, under dark glass with rounded corners; redone a few times a second at most."""
        mw = self.owner.embedded_map
        img = getattr(mw, "_last_image", None)
        if img is None or self._placed is None:
            return
        key = (id(img), getattr(mw, "render_count", 0), self._placed)
        now = time.monotonic()
        if key == self._bd_key or now - self._bd_at < 0.2:
            return
        self._bd_key, self._bd_at = key, now
        try:
            from PIL import Image

            x, y, w, h = self._placed
            ox = mw.canvas.winfo_rootx() - self.master.winfo_rootx()
            oy = mw.canvas.winfo_rooty() - self.master.winfo_rooty()
            cx, cy = x - ox, y - oy
            under = Image.new("RGBA", (w, h), T.rgb(T.WELL) + (255,))
            under.paste(img.convert("RGBA").crop((cx, cy, cx + w, cy + h)), (0, 0))
            glass, _ = T.pill_image(w, h, T._mix(T.rgb(T.BASE), T.GLASS_TINT, 0.45) + (self.ALPHA,),
                                    T._mix(T.rgb(T.BASE), T.GLASS_TINT, 0.25) + (self.ALPHA + 20,), radius=14,
                                    outline=T.hl(60))
            under.alpha_composite(glass)
            from PIL import ImageTk

            self._bd = ImageTk.PhotoImage(under, master=self)
            if self.find_withtag("bd"):
                self.itemconfigure("bd", image=self._bd)
            else:
                self.create_image(0, 0, image=self._bd, anchor="nw", tags=("bd",))
            self.tag_lower("bd")
        except Exception:
            log.exception("game info backdrop")

    def _state(self):
        mw = self.owner.embedded_map
        if mw is None:
            return {"kind": "none"}
        if getattr(self.owner, "plan_on", False):
            lo = getattr(mw, "loot", None) or {}
            marks = mw.plan_marks() if hasattr(mw, "plan_marks") else {}
            return {"kind": "plan", "place": lo.get("place"), "stops": len(lo.get("spots") or []),
                    "chests": lo.get("chests"), "metres": lo.get("metres"), "seconds": lo.get("seconds"),
                    "has": bool(lo), "tool": marks.get("tool", "landing"), "n_avoid": len(marks.get("avoid") or []),
                    "n_check": len(marks.get("checkpoints") or [])}
        if mw.view.get() == "heat":
            return {"kind": "heat", "what": self.owner._heat_label(), "who": self.owner._who_label(),
                    "period": self.owner._period_label(), "n": getattr(mw, "_layer_matches", None)}
        items = [tuple(x)[:3] for x in (mw.legend_items() if hasattr(mw, "legend_items") else [])]
        if any(k == "game" for _c, _l, k in items):
            return {"kind": "compare", "items": items}
        rec = getattr(mw, "_shown_rec", None)
        if rec is None or (hasattr(mw, "match_box") and mw.match_box.get() == "Live / latest match" and not items):
            return {"kind": "live", "items": items}
        t = getattr(mw, "t", None)
        cols = {lbl.split(":")[0].replace("  (you)", ""): col for col, lbl, _k in items}
        randoms = {lbl.split(":")[0] for _c, lbl, k in items if k == "random"}
        watched = next((lbl for _c, lbl, k in items if k == "watched"), None)
        snap = self.owner._snap or {}
        names = snap.get("names") or set()
        totals = M.replay_counts((snap.get("files") or {}).get(rec.get("file")))  # the reader's real numbers
        length = rec.get("length") or 0
        whole = t is None or (length and t >= length - 1)
        people = []
        for pl in rec["map"].get("players", []):
            n = pl.get("name") or "?"
            # every chest counts, far-away teammates' ones too (only their spot on the map is rough)
            mine = [c for c in rec["map"].get("chests", []) if c.get("who") == n and (t is None or c["t"] <= t)]
            opened, broken = sum(1 for c in mine if not c.get("broken")), sum(1 for c in mine if c.get("broken"))
            if n in totals:
                if whole:
                    opened, broken = totals[n]
                else:  # so far: what the map has up to now, never more than the match's total
                    opened, broken = min(opened, totals[n][0]), min(broken, totals[n][1])
            kills = [k for k in pl.get("kills") or [] if t is None or (k.get("t", 0) if isinstance(k, dict) else k[0]) <= t]
            col = cols.get(n)
            if col is None:
                col = map_colors()["me"] if pl.get("recorder") else map_colors()["mates"][len(people) % 5]
            people.append({"name": n, "col": tuple(col), "opened": opened, "broken": broken, "kills": len(kills),
                           "me": M.is_me(n, pl.get("recorder"), names), "watched": n == watched,
                           "random": n in randoms})
        people.sort(key=lambda q: (not q["watched"], not q["me"], -(q["opened"] + q["broken"])))
        return {"kind": "game", "file": rec.get("file"), "start": rec.get("start"), "length": rec.get("length"),
                "t": None if t is None else round(t), "people": people}

    def refresh(self, force=False):
        st = self._state()
        key = repr(st)
        if key == self._key and not force:
            return
        if st.get("kind") == "plan" and self._key is not None:
            self.owner.map_card.repaint()  # ("click where you'd land" → "click elsewhere to re-plan", Clear)
        self._key = key
        self._draw(st)

    def _draw(self, st):
        c, f = self, self.f
        c.delete("content")
        self._imgs = []
        W = self.owner.px(self.WIDTH)
        y = 14
        c._content_tag = True
        self._draw_content(st, W, y)
        # everything drawn goes under the "content" tag (the backdrop stays), then fit the panel to it
        for item in c.find_all():
            if "bd" not in c.gettags(item):
                c.addtag_withtag("content", item)
        bb = c.bbox("content")
        self.H = int((bb[3] if bb else 60) + 14)
        self._place()

    def _draw_content(self, st, W, y):
        c, f = self, self.f

        def head(text, yy):
            c.create_text(14, yy, text=text.upper(), font=f.cap, fill=T.DIM, anchor="w")

        kind = st["kind"]
        if kind == "none":
            c.create_text(W / 2, 60, text="The map isn't ready yet.", font=f.small, fill=T.DIM, width=W - 30)
            return
        if kind == "plan":
            head("Loot route", y)
            y += 22
            hint = {"landing": "Click where you'd land.",
                    "avoid": "Drag on the map to draw a no-go zone (from its middle outwards).",
                    "checkpoint": "Click places the route must go through, in order."}.get(st.get("tool"), "")
            item = c.create_text(14, y, text=hint + "  Right-click a mark to remove it.", font=f.small, fill=T.MUTED,
                                 anchor="nw", width=W - 28)
            bb = c.bbox(item)
            y = (bb[3] if bb else y + 30) + 10
            if st.get("n_avoid") or st.get("n_check"):
                c.create_text(14, y, text=f"{st['n_avoid']} no-go zone{'s' * (st['n_avoid'] != 1)}  ·  "
                              f"{st['n_check']} checkpoint{'s' * (st['n_check'] != 1)}", font=f.small, fill=T.TEXT,
                              anchor="w")
                y += 22
            if not st["has"]:
                return
            c.create_text(14, y, text=st["place"] or "From your spot", font=f.bold, fill=T.TEXT, anchor="w", width=W - 28)
            y += 24
            rows = [("Chest spots", f"{st['stops']}")]
            if st.get("chests") is not None:
                rows.append(("Usually there", f"about {round(st['chests'])}"))
            if st.get("metres") is not None:
                rows.append(("Walk", f"{st['metres'] / 1000:.1f} km"))
            if st.get("seconds") is not None:
                rows.append(("Time", f"about {max(1, round(st['seconds'] / 60))} min"))
            for label, val in rows:
                c.create_text(14, y, text=label, font=f.small, fill=T.MUTED, anchor="w")
                c.create_text(W - 14, y, text=val, font=f.bold, fill=T.ACCENT_SOFT, anchor="e")
                y += 22
            return
        if kind == "heat":
            head("Heatmap", y)
            y += 22
            c.create_text(14, y, text=st["what"], font=f.bold, fill=T.TEXT, anchor="w", width=W - 28)
            y += 20
            c.create_text(14, y, text=f"{st['who']}  ·  {st['period']}", font=f.small, fill=T.MUTED, anchor="w",
                          width=W - 28)
            y += 20
            if st.get("n"):
                c.create_text(14, y, text=f"from {st['n']:,} match{'es' if st['n'] != 1 else ''}", font=f.small, fill=T.DIM, anchor="w")
                y += 22
            p = self.owner.px
            bar = key_icon("heat", p(28))
            from PIL import Image as _Im

            bar = bar.resize((W - 28, p(10)), _Im.BILINEAR)
            pb = T.photo_of(c, bar)
            self._imgs.append(pb)
            c.create_image(14, y, image=pb, anchor="nw")
            c.create_text(14, y + p(18), text="rarely", font=f.cap, fill=T.DIM, anchor="w")
            c.create_text(W - 14, y + p(18), text="often", font=f.cap, fill=T.DIM, anchor="e")
            return
        if kind in ("compare", "live"):
            head("Games on the map" if kind == "compare" else "Live / latest game", y)
            y += 26
            if not st["items"]:
                c.create_text(14, y, text="Play a match and your route shows up here, live.", font=f.small,
                              fill=T.DIM, anchor="nw", width=W - 28)
                return
            for col, lbl, k in st["items"]:
                p = T.photo_of(c, key_icon("route" if k == "game" else "opened", 22, col))
                self._imgs.append(p)
                c.create_image(14, y, image=p, anchor="nw")
                c.create_text(44, y + 11, text=lbl, font=f.bold, fill=T.TEXT, anchor="w", width=W - 56)
                y += 34
            if kind == "compare":
                c.create_text(14, y + 6, text="Untick the games in the list on the right, or press \"Back to the "
                              "latest game\" above.", font=f.small, fill=T.DIM, anchor="nw", width=W - 28)
            elif self.owner.app.cfg.get("console_teammates"):
                c.create_text(14, y + 6, text="Console teammates: after the match (their positions aren't shared "
                              "live).", font=f.small, fill=T.DIM, anchor="nw", width=W - 28)
            return
        # one game: just who's in it, in their colours, with their chests; click someone to follow them
        p = self.owner.px
        head("Players", y)
        c.create_text(W - 14, y, text="CHESTS", font=f.cap, fill=T.DIM, anchor="e")
        y += 16
        for i, q in enumerate(st["people"]):
            tag = f"pl{i}"
            rh = p(30)
            if q["watched"]:
                bg, _ = T.pill_image(W - 12, rh - 4, T.acc(-0.5, 130), T.acc(-0.65, 100), radius=8, outline=T.acc(0.1, 200))
                pb = T.photo_of(c, bg)
                self._imgs.append(pb)
                c.create_image(6, y, image=pb, anchor="nw", tags=(tag,))
            else:  # (an invisible box, so the whole row is clickable)
                c.create_rectangle(6, y, W - 6, y + rh - 4, fill="", outline="", tags=(tag,))
            mid = y + (rh - 4) / 2
            dot, _ = T.circle_image(p(12), q["col"], q["col"], outline=(255, 255, 255, 150))
            pd = T.photo_of(c, dot)
            self._imgs.append(pd)
            c.create_image(16, mid - p(6), image=pd, anchor="nw", tags=(tag,))
            dim = q.get("random")
            name = q["name"] + ("  (you)" if q["me"] else "")
            c.create_text(36, mid, text=T.fit_text(name, f.bold if q["watched"] else f.body, W - 90), anchor="w",
                          font=f.bold if q["watched"] else f.body, fill=T.MUTED if dim else T.TEXT, tags=(tag,))
            total = q["opened"] + q["broken"]
            c.create_text(W - 16, mid, text=str(total), font=f.bold, fill=T.DIM if dim else T.ACCENT_SOFT, anchor="e",
                          tags=(tag,))
            tip = (f"{q['name']}: {q['opened']} opened, {q['broken']} broken, {q['kills']} eliminations"
                   + (" (random squad fill: not saved)" if dim else "") + ("\nThe map follows them." if q["watched"]
                                                                       else "\nClick to follow them on the map."))
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, n=q["name"]: self._watch(n))
            c.tag_bind(tag, "<Enter>", lambda e, t_=tip: (c.configure(cursor="hand2"),
                                                          self.owner.tip.show(t_, e.x_root, e.y_root)))
            c.tag_bind(tag, "<Leave>", lambda e: (c.configure(cursor=""), self.owner.tip.hide()))
            y += rh

    def _watch(self, name):
        mw = self.owner.embedded_map
        st = self._state()
        if mw is None or st.get("kind") != "game" or not st.get("file"):
            return
        try:
            mw.show_match(st["file"], player=name)
        except TypeError:
            mw.show_match(st["file"])
        self.refresh(force=True)


class MapLegend(tk.Canvas):
    """Which game or player is which, laid over the top-left of the map: a small dark glass panel listing the
    overlaid games (or the players in the game shown) in their colours. Its background is the map picture
    under it, so it looks like part of the map."""

    PAD = 12

    def __init__(self, holder, owner):
        super().__init__(holder, highlightthickness=0, bd=0, bg=T.WELL)
        self.holder, self.owner = holder, owner
        self._key = None
        self._photo = None
        self._icons = []
        self._tick()

    def _tick(self):
        try:
            if not self.winfo_exists():
                return
            self.refresh()
        except tk.TclError:
            return
        except Exception:
            log.exception("map legend")
        self.after(300, self._tick)

    def items(self) -> list:
        """[(colour, label, kind)]: the overlaid games, or the players in the one game shown; [] for nothing."""
        mw = self.owner.embedded_map
        if mw is None:
            return []
        if hasattr(mw, "legend_items"):
            return [tuple(x)[:3] for x in mw.legend_items() or []]
        if getattr(mw, "view", None) is None or mw.view.get() != "match":
            return []
        files = list(getattr(mw, "compare_files", []) or [])
        d = self.owner._snap or {}
        rows = {r["replay"]: r for r in d.get("rows", []) if r.get("replay")}
        if len(files) >= 2:
            cols = compare_colors()
            out = []
            for i, f_ in enumerate(files[:len(cols)]):
                r = rows.get(f_)
                label = f"{M.fmt_when(r['started'], d.get('now'))} · {r['chests']} chests" if r else f_
                out.append((cols[i], label, "game"))
            return out
        rec = getattr(mw, "_shown_rec", None)
        players = (rec or {}).get("map", {}).get("players", []) if rec else []
        if len(players) < 2:
            return []
        cols = mw._colors(players) if hasattr(mw, "_colors") else {}
        mates = map_colors()["mates"]
        out = []
        for i, pl in enumerate(players):
            col = cols.get(pl.get("name")) or (map_colors()["me"] if pl.get("recorder") else mates[i % len(mates)])
            out.append((col, (pl.get("name") or "?") + ("  (you)" if pl.get("recorder") else ""), "player"))
        return out

    def refresh(self):
        mw = self.owner.embedded_map
        items = self.items() if (mw is not None and self.owner.map_tab == "map") else []
        img = getattr(mw, "_last_image", None) if mw is not None else None
        drawn = getattr(mw, "render_count", None) if mw is not None else None  # changes on every map redraw
        key = (tuple((tuple(c), lbl, k) for c, lbl, k in items), id(img) if drawn is None else drawn,
               self.holder.winfo_width())
        if key == self._key:
            return
        self._key = key
        if not items:
            self.place_forget()
            return
        self._draw(mw, items, img)

    def _draw(self, mw, items, img):
        from PIL import Image, ImageTk

        f = self.owner.f
        kinds = {k for _c, _l, k in items}
        title = "Games on the map" if "game" in kinds else ("Live squad" if "me" in kinds else "Players")
        row_h, top = 26, 32
        w = max(f.bold.measure(lbl) + (f.small.measure("watching") + 10 if k == "watched" else 0)
                for _c, lbl, k in items) + 64
        w = max(w, f.cap.measure(title.upper()) + 30)
        h = top + row_h * len(items) + 8
        canvas = getattr(mw, "canvas", None)
        try:
            ox = canvas.winfo_rootx() - self.holder.winfo_rootx() if canvas is not None else 0
            oy = canvas.winfo_rooty() - self.holder.winfo_rooty() if canvas is not None else 0
        except tk.TclError:
            ox = oy = 0
        x, y = self.PAD, self.PAD
        if img is not None and img.size[0] >= x + w and img.size[1] >= y + h:
            bg = img.convert("RGBA").crop((x, y, x + w, y + h))
        else:
            bg = Image.new("RGBA", (w, h), T.rgb(T.WELL) + (255,))
        panel, _ = T.pill_image(w, h, T._mix(T.rgb(T.BASE), T.GLASS_TINT, 0.5) + (215,),
                                T._mix(T.rgb(T.BASE), T.GLASS_TINT, 0.3) + (225,), radius=12, outline=T.hl(70))
        bg.alpha_composite(panel)
        self.delete("all")
        self._photo = ImageTk.PhotoImage(bg)
        self.create_image(0, 0, image=self._photo, anchor="nw")
        self.create_text(12, 17, text=title.upper(), font=f.cap, fill=T.DIM, anchor="w")
        self._icons = []
        for i, (col, lbl, kind) in enumerate(items):
            yy = top + i * row_h
            icon = key_icon("route" if kind == "game" else "opened", 22, col)
            p = T.photo_of(self, icon)
            self._icons.append(p)
            self.create_image(12, yy + 2, image=p, anchor="nw")
            self.create_text(44, yy + 13, text=lbl, font=f.bold, fill=T.TEXT, anchor="w")
            if kind == "watched":  # the player the camera follows
                self.create_text(44 + f.bold.measure(lbl) + 8, yy + 13, text="watching", font=f.small,
                                 fill=T.ACCENT_SOFT, anchor="w")
        self.configure(width=w, height=h)
        self.place(x=ox + x, y=oy + y, width=w, height=h)
        self.tk.call("raise", self._w)


def p_(owner, n) -> int:
    return owner.px(n)


def fmt_n(n) -> str:
    return "-" if n is None else f"{n:,}"


def plural(n, word) -> str:
    return f"{n:,} {word}{'s' * (n != 1)}"


# --------------------------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------------------------
class AppWindow:
    def __init__(self, app):
        self.app = app
        self.ct = sys.modules[type(app).__module__]  # chest_tracker, however it was started
        root = app.root
        w = self.win = tk.Toplevel(root)
        w.title("Chest Tracker")
        w.configure(bg=T.BASE)
        w.protocol("WM_DELETE_WINDOW", self.hide)
        T.use_theme(app.cfg.get("app_theme") or "arctic")
        self.f = T.Fonts(root)
        T.apply_theme(root)
        self.tip = T.Tooltip(root, self.f)
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        w.minsize(self.px(980), self.px(640))
        geo = app.cfg.get("app_geometry")
        if isinstance(geo, str) and self._on_screen(geo):
            w.geometry(geo)
        else:
            sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
            ww, wh = min(self.px(1320), sw - 80), min(self.px(860), sh - 120)
            w.geometry(f"{ww}x{wh}+{(sw - ww) // 2}+{max(20, (sh - wh) // 2 - 20)}")
        pages = dict((p[0], 1) for p in PAGES)
        self.page = app.cfg.get("app_page") if app.cfg.get("app_page") in pages else "overview"
        self.period = 30
        self.detail_cols = bool(app.cfg.get("app_more_columns", False))
        self.chart_days = 14
        self._key = None
        self._dirty: set[str] = set()
        self._snap = None
        self._chest_img = None
        self.pages: dict[str, tk.Widget] = {}
        self._built: set[str] = set()
        self.title = Ref("")  # the page's title (tests read it)

        self._build_header()
        self.stack = tk.Frame(w, bg=T.BASE)
        self.stack.pack(side="top", fill="both", expand=True)
        w.bind_all("<MouseWheel>", self._wheel, add="+")
        self._tick()

    def px(self, n) -> int:
        return int(round(n * self.scale))

    def _on_screen(self, geo: str) -> bool:
        try:
            size, x, y = geo.split("+")[0], int(geo.split("+")[1]), int(geo.split("+")[2])
            ww, wh = (int(v) for v in size.split("x"))
            sw, sh = self.win.winfo_vrootwidth(), self.win.winfo_vrootheight()
            return ww >= 600 and wh >= 400 and -50 <= x <= sw - 200 and -10 <= y <= sh - 200
        except (ValueError, IndexError, tk.TclError):
            return False

    # ---- showing ----------------------------------------------------------------------------
    def show(self, page: str | None = None):
        if page in ALL_PAGES:
            self.page = page
        self.win.deiconify()
        self.win.lift()
        try:
            self.win.focus_force()
        except tk.TclError:
            pass
        self._key = None
        self.go(self.page)

    def hide(self):
        panel = getattr(self, "settings_panel", None)
        if panel is not None:
            try:
                panel.save_all()
            except Exception:
                log.exception("saving settings")
        self.tip.hide()
        try:
            if self.win.state() == "normal":
                self.app.cfg["app_geometry"] = self.win.geometry()
            self.app.cfg["app_page"] = self.page
            self.app.cfg["app_more_columns"] = self.detail_cols
            self.ct.save_json(self.ct.CONFIG_PATH, self.app.cfg)
        except Exception:
            log.exception("saving the window position")
        self.win.withdraw()

    def switch_theme(self, key: str):
        """Changes the colour theme: saves it and rebuilds the window in it, on the same page."""
        if key == (self.app.cfg.get("app_theme") or "arctic"):
            return
        self.app.cfg["app_theme"] = key
        page = self.page
        self.hide()  # saves settings, size and page
        try:
            self.win.destroy()  # (the separate map window keeps its colours until the app restarts)
        except tk.TclError:
            pass
        self.app._app_window = None
        show(self.app, page)
        self.refresh_overlay()

    def refresh_overlay(self):
        """Redraws the in-game overlay in the current theme / look (it's rebuilt from the config)."""
        app = self.app
        maker = getattr(self.ct, "make_painter", None)
        if maker is None or not hasattr(app, "painter"):
            return
        try:
            app.painter = maker(app.cfg, float(app.cfg.get("overlay_scale") or 1.0))
            if hasattr(app, "redraw"):
                app.redraw(force=True)
        except Exception:
            log.exception("restyling the overlay")

    def visible(self) -> bool:
        try:
            return self.win.state() == "normal"
        except tk.TclError:
            return False

    # ---- header: logo, tabs, status, help, settings, you -------------------------------------------
    def _build_header(self):
        f = self.f
        hs = self.header = T.Surface(self.win, glows=[(0.0, 0.0, 0.4, T.acc(-0.5), 0.12)])
        hs.configure(height=self.px(70))
        hs.pack(side="top", fill="x")
        try:
            from PIL import Image, ImageTk

            here = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(here, "assets", "icon.png")
            img = Image.open(path if os.path.exists(path) else os.path.join(here, "chest.png")).convert("RGBA")
            n = self.px(36)
            self._chest_img = ImageTk.PhotoImage(img.resize((n, n), Image.LANCZOS))
            hs.create_image(self.px(24), self.px(35), image=self._chest_img, anchor="w")
        except Exception:
            pass
        self._logo_text = hs.create_text(self.px(68), self.px(35), text="Chest Tracker", font=f.logo, fill=T.TEXT, anchor="w")
        self.nav_card = T.Card(hs, self._paint_nav, radius=1, height=self.px(56), width=self.px(800), fonts=f, plain=True)
        self.me_card = T.Card(hs, self._paint_me, radius=self.px(24), height=self.px(50), width=self.px(250), fonts=f)
        self.me_card.place(relx=1.0, x=-self.px(20), y=self.px(10), anchor="ne")
        self._nav_layout = []
        hs.bind("<Configure>", lambda e: self._layout_nav(e.width), add="+")

    def _nav_items(self, compact):
        """[(key, label, glyph, width, height, big)] for the left group, the Map button, the right group."""
        f, p = self.f, self.px
        info = dict((k, (label, g)) for k, label, g in PAGES)
        pad = p(12) if compact else p(18)

        def item(key, big=False):
            label, glyph = info[key]
            font = f.nav_big if big else f.nav
            icon = 0 if compact or not f.icons else f.icon.measure(glyph) + p(8)
            return (key, label, glyph, font.measure(label) + icon + 2 * pad + (p(14) if big else 0), p(46) if big else p(38), big)

        return [item(k) for k in NAV_LEFT], item(NAV_CENTER, True), [item(k) for k in NAV_RIGHT]

    def _layout_nav(self, width):
        """Centres the navigation in the window, Map exactly in the middle, if it fits between the logo and
        your name; otherwise a compact row after the logo."""
        p = self.px
        gap = p(8)
        me_left = width - p(20) - int(self.me_card.cget("width"))
        logo_end = p(68) + self.f.logo.measure("Chest Tracker") + p(20)
        for compact in (False, True):
            left, mid, right = self._nav_items(compact)
            lw = sum(i[3] for i in left) + gap * len(left)
            rw = sum(i[3] for i in right) + gap * len(right)
            half = max(lw, rw)
            total = 2 * half + mid[3] + p(8)
            x0 = width / 2 - total / 2
            if x0 >= logo_end and x0 + total <= me_left - p(10):
                break
        else:
            # no room to centre: hide the words next to the logo, keep the row as centred as it can be
            logo_end = p(70)
            x0 = max(logo_end, min(width / 2 - total / 2, me_left - p(10) - total))
        self.header.itemconfigure(self._logo_text, state="normal" if logo_end > p(70) else "hidden")
        cx = total / 2  # the Map button's centre, in the nav card
        items = [(*mid, cx - mid[3] / 2)]
        x = cx - mid[3] / 2 - gap
        for it in reversed(left):
            x -= it[3]
            items.append((*it, x))
            x -= gap
        x = cx + mid[3] / 2 + gap
        for it in right:
            items.append((*it, x))
            x += it[3] + gap
        self._nav_layout = items
        self._nav_compact = compact
        self.nav_card.configure(width=int(total), height=p(56))
        self.nav_card.place(x=int(x0), y=p(7), anchor="nw")
        self.nav_card.repaint()

    def _paint_nav(self, c: T.Card):
        f, p = self.f, self.px
        for key, label, glyph, w, h, big, x in self._nav_layout:
            on = key == self.page
            T.pill(c, x, c.H / 2, label, lambda k=key: self.go(k), kind="soft" if big else "ghost", active=on,
                   font=f.nav_big if big else f.nav, anchor="w", h=h, width=w, radius=p(16) if big else p(12),
                   icon=None if self._nav_compact or not f.icons else glyph, tag=f"nav_{key}")

    def _paint_me(self, c: T.Card):
        f, app, p = self.f, self.app, self.px
        in_match = self._in_match()
        name = app.cfg.get("name") or "You"
        team = app.cfg.get("team_code") or ""
        T.avatar(c, p(10), (c.H - p(36)) / 2, name, d=p(36), online=True)
        c.text(p(56), c.H / 2 - p(9), name, font=f.bold, anchor="w")
        status = "In a match" if in_match else "In the lobby"
        c.text(p(56), c.H / 2 + p(10), status + (f" · {team}" if team else ""), font=f.small,
               fill=T.GREEN if in_match else T.DIM, anchor="w")
        T.icon_circle(c, c.W - p(46), (c.H - p(34)) / 2, "\uE897" if f.icons else "?", d=p(34), command=self._show_intro,
                      tag="helpbtn")

    def go(self, key):
        if key not in ALL_PAGES:
            return
        self.page = key
        self.tip.hide()
        if self._nav_layout:
            self.nav_card.repaint()
        self.me_card.repaint()
        if key not in self._built:
            self.pages[key] = getattr(self, f"_build_{key}")()
            self._built.add(key)
            self._dirty.add(key)
        for k, fr in self.pages.items():
            if k != key:
                fr.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        self.title.configure(text=ALL_PAGES[key])
        self._refresh_page(force=True)

    def _wheel(self, e):
        try:
            w = self.win.winfo_containing(e.x_root, e.y_root)
        except (tk.TclError, KeyError):
            return
        while w is not None:
            if getattr(w, "_wheel_target", False):
                if str(w).startswith(str(self.win)):
                    w.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
                return
            w = w.master

    # ---- page scaffolding -----------------------------------------------------------------------------
    def _scroll_page(self, key, cols=4, actions=None):
        sp = T.ScrollSurface(self.stack)
        s = sp.inner
        for i in range(cols):
            s.columnconfigure(i, weight=1, uniform=f"{key}c")
        self._title_card(s, key, cols, actions)
        return sp, s

    def _title_card(self, s, key, cols, actions=None):
        title, sub = TITLES[key]

        def paint(c):
            c.text(self.px(6), self.px(8), title, font=self.f.title)
            c.text(self.px(8), self.px(52), sub, font=self.f.body, fill=T.MUTED)
            x = c.W - self.px(6)
            for label, cmd, kind in reversed((actions() if callable(actions) else actions) or []):
                x0, *_ = T.pill(c, x, self.px(16), label, cmd, kind=kind, anchor="ne", h=self.px(38))
                x = x0 - self.px(10)

        card = T.Card(s, paint, height=self.px(82), fonts=self.f, plain=True)
        card.grid(row=0, column=0, columnspan=cols, sticky="ew", padx=self.px(14), pady=(self.px(8), 0))
        return card

    def _card(self, s, paint, row, col, colspan=1, rowspan=1, height=200, pad=None, **kw):
        c = T.Card(s, paint, height=self.px(height), fonts=self.f, radius=self.px(22), **kw)
        p = self.px(9) if pad is None else pad
        c.grid(row=row, column=col, columnspan=colspan, rowspan=rowspan, sticky="nsew", padx=p, pady=p)
        return c

    def _help(self, c, x, y, key):
        T.help_dot(c, x, y, HELP[key], self.tip)

    # ---- data ---------------------------------------------------------------------------------
    def snapshot(self) -> dict:
        """Everything the pages show, read from the running app (cheap; in-memory)."""
        app = self.app
        s = app.stats
        with s.lock:
            history = list(s.history)
            current = {"started": s.game_started, "chests": s.game, "seen": s.seen, "manual": s.manual,
                       "adjust": s.adjust, "replay": s.replay, "confirmed": s.confirmed, "finished": s.finished}
            partial = dict(getattr(s, "partial", None) or {})  # mid-match replay read so far (if any)
            total = s.total
        files = dict(list(app.replays.db.files.items()))
        live = getattr(app, "_live_entry", None)  # the current match's replay, read mid-match
        if live and live[0] == current.get("replay") and live[0] not in files:
            files[live[0]] = dict(live[1], how="recorder")
        names = app.replays.my_names()
        maps = {}
        mgr = getattr(app, "map", None)
        if mgr is not None:
            try:
                maps = {r.get("file"): r for r in mgr.records()}
            except Exception:
                log.exception("map records")
        with app.treasures.lock:
            mine = list(app.treasures.items)
        rows = M.match_rows(history, current, files, maps, mine, names, pois=self._pois() if maps else None,
                            now=time.time())
        t = app.team
        now_mono, now = time.monotonic(), time.time()
        with t.lock:
            peers = [dict(p, online=t._is_online(p, now_mono, now), id=pid) for pid, p in t.peers.items()]
        try:
            guests = t.guests()
        except Exception:
            guests = {}
        try:
            team_total, team_game = t.team_total(), t.team_game()
        except Exception:
            team_total = team_game = 0
        return {"rows": rows, "total": total, "game": s.game, "current": current, "files": files, "names": names,
                "maps": maps, "mine": mine, "peers": peers, "guests": guests, "team_total": team_total,
                "team_game": team_game, "has_team": t.has_team(), "team_code": app.cfg.get("team_code") or "",
                "in_match": self._in_match(), "now": now, "partial": partial,
                "live_nokey": bool((getattr(app.replays, "_live", None) or {}).get("nokey"))}

    def _in_match(self) -> bool:
        try:
            return bool(self.app.match_in_progress())
        except Exception:
            return False

    def _tick(self):
        try:
            if not self.win.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            self._update()
        except Exception:
            log.exception("app window update")
        self.win.after(1000, self._tick)

    def _update(self):
        app = self.app
        if not self.visible():
            return
        in_match = self._in_match()
        mgr = getattr(app, "map", None)
        key = (app.stats.version, app.team.version, getattr(app.replays.db, "version", 0), len(app.treasures.items),
               getattr(mgr, "version", 0), in_match, int(time.time() // 10))  # (online states time out by themselves)
        if key != self._key:
            self._key = key
            self._snap = None
            self._dirty = set(self._built)
            self.me_card.repaint()
            self._refresh_page()

    def _refresh_page(self, force=False):
        if self.page not in self._dirty and not force:
            return
        if self._snap is None:
            self._snap = self.snapshot()
        self._dirty.discard(self.page)
        try:
            getattr(self, f"_fill_{self.page}")(self._snap)
        except Exception:
            log.exception("filling the %s page", self.page)

    def _people(self, d) -> list[dict]:
        """Me, my siblings (tracker), console teammates: [{"name", "game", "total", "online", "tag", "me"}]."""
        app = self.app
        stats_today = getattr(app.stats, "today", None)
        me = {"name": app.cfg.get("name") or "You", "game": d["game"], "total": d["total"], "online": True,
              "tag": "You", "me": True, "today": stats_today() if stats_today else None}
        lt = time.localtime(d["now"])
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        out = []
        for p in d["peers"]:
            today = p.get("today")
            if today is not None and p.get("ts", 0) < midnight:
                today = 0  # their last update was before today
            out.append(dict(p, name=p.get("name") or "?", tag="Chest Tracker", me=False, today=today))
        for g in d["guests"].values():
            out.append({"name": g.get("name") or "?", "fname": g.get("name"), "game": g.get("game", 0),
                        "total": g.get("total", 0), "online": bool(g.get("online")), "me": False, "today": g.get("today", 0),
                        "tag": (M.platform_name(g.get("platform")) or "Console") + " guest"})
        out.sort(key=lambda p: (not p["online"], -p["total"]))
        return [me] + out

    def _status(self, r, in_match):
        if r.get("not_me"):
            return "Not you", "muted"
        if r["current"] and in_match:
            return "Live", "orange"
        if r["confirmed"]:
            return "Checked", "green"
        if r["replay"] or r["current"]:
            return "Checking", "amber"
        return "Live count", "muted"

    # ==========================================================================================
    # Home
    # ==========================================================================================
    def _build_overview(self):
        sp, s = self._scroll_page("overview", 4, [("Share my stats", self._share_stats, "primary")])
        self.ov = {k: (Ref(""), Ref("")) for k in ("total", "game", "team", "today")}
        self._ov_data = None
        self._today_badge = self._game_badge = None
        self.intro = self._card(s, self._paint_intro, 1, 0, colspan=4, height=150, glows=GLOW_RIGHT())
        if self.app.cfg.get("app_intro_done"):
            self.intro.grid_remove()
        self.chart_card = self._card(s, self._paint_chart, 2, 0, colspan=2, rowspan=2, height=330, glows=GLOW_BOTTOM())
        self.chart_card.bind("<Motion>", self._chart_motion, add="+")
        self.chart_card.bind("<Leave>", lambda e: self._chart_hover(None), add="+")
        self._chart_hover_i = None
        self.kpi_cards = {}
        for i, (key, label, glyph, page) in enumerate([("total", "My total", "", "matches"),
                                                        ("today", "Today", "", "matches"),
                                                        ("game", "This game", "", "matches"),
                                                        ("team", "Team total", "", "team")]):
            self.kpi_cards[key] = self._card(
                s, lambda c, k=key, lb=label, g=glyph, pg=page: self._paint_kpi(c, k, lb, g, pg), 2 + i // 2, 2 + i % 2,
                height=155, glows=GLOW_CORNER() if key == "total" else ())
        self.when_card = self._card(s, self._paint_when, 4, 0, height=300)
        self.squad_card = self._card(s, self._paint_squad_now, 4, 1, height=300)
        self.recent_card = self._card(s, self._paint_recent, 4, 2, colspan=2, height=300, glows=GLOW_CORNER())
        self.recent = Ref()
        self.recent.rows = []
        self.land_card = self._card(s, self._paint_land, 5, 0, colspan=4, height=92, glows=GLOW_LEFT())
        tk.Frame(s, bg=T.BASE, height=self.px(10)).grid(row=6, column=0)
        return sp

    def _share_stats(self):
        """A picture of my season for Discord: copied to the clipboard and saved in Pictures (share_card.py)."""
        import share_card

        data = self._ov_data
        if not data:
            self.app.toast("Your stats are still loading. Try again in a moment.", 4)
            return
        try:
            info = share_card.info_from(data["sum"], self.app.cfg.get("name") or "Player", data["d"]["total"])
            img = share_card.make_card(info)
            path = share_card.save(img)
            copied = share_card.copy_to_clipboard(img)
        except Exception:
            log.exception("share card")
            self.app.toast("Couldn't make the stats picture (see the log).", 6)
            return
        self._show_share(img, path, copied)

    def _show_share(self, img, path, copied: bool):
        from PIL import Image, ImageTk

        w = tk.Toplevel(self.win)
        w.title("Share my stats")
        w.configure(bg=T.BASE)
        w.transient(self.win)
        w.resizable(False, False)
        p = self.px
        width = p(600)
        self._share_img = ImageTk.PhotoImage(img.resize((width, int(width * img.height / img.width)), Image.LANCZOS))
        tk.Label(w, image=self._share_img, bg=T.BASE, bd=0).pack(padx=p(18), pady=(p(18), p(10)))
        text = ("Copied! Paste it into Discord with Ctrl+V." if copied else "Saved.") + f"  Also saved in {path.parent}."
        tk.Label(w, text=text, font=self.f.body, fg=T.TEXT, bg=T.BASE, wraplength=width, justify="left").pack(
            padx=p(18), anchor="w")
        row = tk.Frame(w, bg=T.BASE)
        row.pack(fill="x", padx=p(18), pady=p(14))
        for label, cmd in (("Done", w.destroy), ("Show in folder", lambda: os.startfile(str(path.parent)))):  # noqa: S606
            T.Button(row, label, cmd, self.f, bg=T.BASE, h=p(34)).pack(side="right", padx=(p(8), 0))
        w.update_idletasks()
        x = self.win.winfo_rootx() + (self.win.winfo_width() - w.winfo_reqwidth()) // 2
        y = self.win.winfo_rooty() + max(0, (self.win.winfo_height() - w.winfo_reqheight()) // 3)
        w.geometry(f"+{max(0, x)}+{max(0, y)}")
        w.deiconify()
        w.lift()
        w.focus_force()

    def _fill_overview(self, d):
        rows = d["rows"]
        allsum = M.summary(rows, d["now"])
        cur = next((r for r in rows if r["current"]), None)
        days = M.daily(rows, max(2, self.chart_days), d["now"])
        today, yesterday = days[-1]["chests"], days[-2]["chests"]
        self._ov_data = {"d": d, "sum": allsum, "cur": cur, "days": days, "people": self._people(d),
                         "drops": self._drops(d), "when": M.play_times(rows, 60, d["now"])}
        self.ov["total"][0].configure(text=fmt_n(d["total"]))
        self.ov["total"][1].configure(text=f"{plural(allsum['games'], 'game')} · {M.fmt_playtime(allsum['play_s'])} played")
        self.ov["today"][0].configure(text=fmt_n(allsum["today"]))
        self.ov["today"][1].configure(text=f"{plural(allsum['today_games'], 'game')} today"
                                           + (f" · {allsum['today_per_min']:.1f} a min" if allsum["today_per_min"] else ""))
        diff = today - yesterday
        if not (yesterday or today):
            self._today_badge = None
        elif diff >= 0:
            self._today_badge = (f"+{diff:,} vs yesterday", "green")
        else:
            self._today_badge = (f"yesterday {yesterday:,}", "muted")  # (the day isn't over: no red)
        self.ov["game"][0].configure(text=fmt_n(d["game"]))
        if d["in_match"] and d.get("partial"):
            pp = d["partial"]
            note = f"Playing now · replay so far: {pp.get('opened', 0)} opened + {pp.get('broken', 0)} broken"
        elif d["in_match"]:
            note = "Playing now · broken chests and console teammates come right after the match"
        elif cur and cur["confirmed"]:
            note = f"Last game · {cur['opened']} opened + {cur['broken']} broken"
        elif cur and cur["chests"]:
            note = "Last game · checking the replay"
        else:
            note = "Waiting for your next match"
        self.ov["game"][1].configure(text=note)
        avg = allsum["avg"]
        self._game_badge = ((f"{d['game'] - avg:+.0f} vs your average", "green" if d["game"] >= avg else "amber")
                            if allsum["games"] > 2 and d["game"] else None)
        if d["has_team"]:
            online = sum(1 for p in d["peers"] if p["online"])
            self.ov["team"][0].configure(text=fmt_n(d["team_total"]))
            self.ov["team"][1].configure(text=f"Team game {d['team_game']} · {online} online")
        else:
            self.ov["team"][0].configure(text="-")
            self.ov["team"][1].configure(text="No team yet")
        self.recent.rows = rows[:5]
        for c in (self.chart_card, self.when_card, self.squad_card, self.recent_card, self.land_card, self.intro,
                  *self.kpi_cards.values()):
            c.repaint()

    # -- intro
    def _show_intro(self):
        self.app.cfg["app_intro_done"] = False
        self.go("overview")
        self.intro.grid()
        self.win.after(80, self.intro.render)

    def _hide_intro(self):
        self.app.cfg["app_intro_done"] = True
        self.ct.save_json(self.ct.CONFIG_PATH, self.app.cfg)
        self.intro.grid_remove()

    def _paint_intro(self, c):
        f, p = self.f, self.px
        c.text(p(26), p(20), "New here? Here's how Chest Tracker works", font=f.h2)
        steps = [("1", "Play Fortnite as usual", "The small overlay counts every chest you open, live."),
                 ("2", "After each match", "Your replay is checked, so broken chests count too."),
                 ("3", "Play with your squad", "Share a team code and see everyone's chests together.")]
        colw = (c.W - p(240)) / 3
        for i, (n, head, body) in enumerate(steps):
            x = p(26) + i * colw
            img, _ = T.circle_image(p(30), T.PRIMARY[0], T.PRIMARY[1])
            c.image(x, p(58), img)
            c.text(x + p(15), p(73), n, font=f.bold, fill=T.ON_ACCENT, anchor="center")
            c.text(x + p(42), p(56), head, font=f.bold)
            c.text(x + p(42), p(78), body, font=f.small, fill=T.MUTED, width=colw - p(60))
        T.pill(c, c.W - p(26), p(36), "Got it", self._hide_intro, kind="primary", anchor="ne")
        T.pill(c, c.W - p(26), p(84), "Set up a team", lambda: self.go("settings"), anchor="ne")

    # -- KPI tiles
    def _paint_kpi(self, c, key, label, glyph, page):
        f, p = self.f, self.px
        T.icon_circle(c, p(18), p(16), glyph if f.icons else "•", d=p(34))
        c.text(p(62), p(33), label, font=f.small_bold, fill=T.MUTED, anchor="w")
        self._help(c, p(62) + f.small_bold.measure(label) + p(8), p(25), key)
        T.icon_circle(c, c.W - p(50), p(16), "↗", d=p(32), command=lambda: self.go(page), tag=f"go{key}")
        val, note = self.ov[key]
        empty = val.text in ("", "-")
        c.text(p(18), p(56), val.text or "-", font=f.huge, fill=T.DIM if empty else T.TEXT)
        b = {"today": self._today_badge, "game": self._game_badge}.get(key)
        if b:
            bx = p(18) + f.huge.measure(val.text or "-") + p(12)
            if bx + f.cap.measure(b[0]) + p(30) > c.W:
                T.badge(c, p(18), c.H - p(46), b[0], b[1], anchor="w")
            else:
                T.badge(c, bx, p(82), b[0], b[1], anchor="w")
        c.text(p(18), c.H - p(22), note.text, font=f.small, fill=T.DIM, anchor="w", width=c.W - p(36))
        if key == "team" and empty:
            T.pill(c, c.W - p(18), c.H - p(42), "Add a team", lambda: self.go("settings"), anchor="ne", h=p(30), pad=p(12))

    # -- chest chart
    def _paint_chart(self, c):
        f, p = self.f, self.px
        data = self._ov_data
        c.text(p(24), p(20), "Chests per day", font=f.h2)
        c.text(p(24), p(44), "Bright = opened, dark = broken. Hover a day for its numbers.", font=f.small, fill=T.DIM)
        tabs_x = c.W - p(22)
        for label, n in reversed([("2 weeks", 14), ("30 days", 30)]):
            x0, *_ = T.pill(c, tabs_x, p(18), label, lambda n=n: self._chart_days(n), anchor="ne", h=p(32),
                            active=self.chart_days == n, pad=p(14), font=f.small_bold)
            tabs_x = x0 - p(6)
        if not data:
            return
        days = data["days"][-self.chart_days:]
        left, right, top, bottom = p(56), c.W - p(24), p(96), c.H - p(42)
        peak = max([x["chests"] for x in days] + [1])
        step = 10 ** max(0, len(str(peak)) - 1)
        nice = ((peak + step - 1) // step) * step
        for k in range(5):
            y = bottom - (bottom - top) * k / 4
            c.line(left - p(4), y, right, y, fill=T.WELL_LINE, dash=(2, 6) if k else None)
            c.text(left - p(12), y, f"{int(nice * k / 4):,}", font=f.small, fill=T.DIM, anchor="e")
        n = len(days)
        slot = (right - left) / n
        bw = int(max(p(8), min(p(26), slot * 0.5)))
        rb = max(2, min(p(6), bw // 3))  # rounded rectangles, not pills
        self._chart_geo = (left, slot, days)
        for i, dd in enumerate(days):
            x = left + slot * i + slot / 2
            track, _ = T.pill_image(bw, int(bottom - top), T.hl(14), T.hl(6), radius=rb)
            c.image(x - bw / 2, top, track)
            if dd["chests"]:
                hh = max(bw, int((bottom - top) * dd["chests"] / nice))
                hb = int((bottom - top) * min(dd["broken"], dd["chests"]) / nice)
                hover = self._chart_hover_i == i
                glow, m = T.glow_image(bw, hh, T.acc(0), 130 if hover else 70, p(9))  # (soft light behind the bar)
                c.image(x - bw / 2 - m, bottom - hh - m, glow)
                top_c, bot_c = (T.acc(0.45), T.acc(0.0)) if hover else (T.acc(0.3), T.acc(-0.12))
                bar, _ = T.pill_image(bw, hh, top_c, bot_c, radius=rb)
                c.image(x - bw / 2, bottom - hh, bar)
                if hb > p(4):
                    br, _ = T.pill_image(bw, min(hh, hb), T.acc(-0.45), T.acc(-0.6), radius=rb)
                    c.image(x - bw / 2, bottom - min(hh, hb), br)
            today = i == n - 1
            label = "Today" if today and slot > p(40) else (dd["label"] if (n <= 14 or i % 2 == 0 or today) else "")
            c.text(x, bottom + p(16), label, font=f.small_bold if today else f.small, fill=T.TEXT if today else T.DIM,
                   anchor="center")
        if self._chart_hover_i is not None and self._chart_hover_i < n:
            i = self._chart_hover_i
            dd = days[i]
            x = left + slot * i + slot / 2
            lines = [time.strftime("%a %b %d", time.localtime(dd["day"] + 3600)),
                     f"{dd['chests']:,} chests in {plural(dd['games'], 'game')}",
                     f"{dd['opened']:,} opened · {dd['broken']:,} broken"]
            w = max(f.small_bold.measure(t) for t in lines) + p(24)
            bx = min(max(left, x - w / 2), right - w)
            box, _ = T.pill_image(int(w), p(70), (58, 40, 30, 248), (42, 29, 22, 248), radius=p(12),
                                  outline=T.acc(0.35, 80))
            c.image(bx, top - p(10), box)
            for k, t in enumerate(lines):
                c.text(bx + p(12), top + p(2) + k * p(18), t, font=f.small_bold if k == 0 else f.small,
                       fill=T.TEXT if k < 2 else T.MUTED)

    def _chart_days(self, n):
        self.chart_days = n
        if self._snap:
            self._fill_overview(self._snap)

    def _chart_motion(self, e):
        geo = getattr(self, "_chart_geo", None)
        if not geo:
            return
        left, slot, days = geo
        i = int((e.x - left) // slot) if e.x >= left else None
        self._chart_hover(i if i is not None and 0 <= i < len(days) and e.y > self.px(80) else None)

    def _chart_hover(self, i):
        if i != self._chart_hover_i:
            self._chart_hover_i = i
            self.chart_card.repaint()

    # -- when you play
    def _paint_when(self, c):
        f, p = self.f, self.px
        c.text(p(22), p(20), "When you play", font=f.h2)
        c.text(p(22), p(44), "Chests by day and time, last 60 days", font=f.small, fill=T.DIM)
        data = self._ov_data
        if not data:
            return
        grid = data["when"]
        peak = max([v for row in grid for v in row] + [1])
        blocks = ["12a", "4a", "8a", "12p", "4p", "8p"]
        days = ["M", "T", "W", "T", "F", "S", "S"]
        left, top = p(50), p(72)
        cell = min((c.W - left - p(18)) / 7, (c.H - top - p(36)) / 6)
        sz = int(cell - p(6))
        for r, lab in enumerate(blocks):
            c.text(left - p(10), top + r * cell + sz / 2, lab, font=f.cap, fill=T.DIM, anchor="e")
            for d in range(7):
                v = grid[d][r] / peak
                if v <= 0:
                    img, _ = T.pill_image(sz, sz, T.hl(26), T.hl(18), radius=p(7),
                                          outline=T.hl(22))
                else:
                    a = 0.25 + 0.75 * v
                    col = T._mix(T.acc(-0.55), T.acc(0.12), a)
                    img, _ = T.pill_image(sz, sz, col, tuple(int(x * 0.82) for x in col), radius=p(7))
                c.image(left + d * cell, top + r * cell, img)
        for d, lab in enumerate(days):
            c.text(left + d * cell + sz / 2, top + 6 * cell + p(2), lab, font=f.cap, fill=T.DIM, anchor="n")

    # -- squad now
    def _paint_squad_now(self, c):
        f, p = self.f, self.px
        c.text(p(22), p(20), "Squad right now", font=f.h2)
        T.icon_circle(c, c.W - p(50), p(14), "↗", d=p(32), command=lambda: self.go("team"), tag="gosquad")
        data = self._ov_data
        if not data:
            return
        people = data["people"]
        if len(people) == 1 and not data["d"]["has_team"]:
            c.text(p(22), p(62), "Play with your siblings? Make up a team code and everyone enters the same one.",
                   font=f.small, fill=T.MUTED, width=c.W - p(44))
            T.pill(c, p(22), p(112), "Set up a team", lambda: self.go("settings"), kind="primary")
            return
        y = p(62)
        for person in people[:5]:
            T.avatar(c, p(22), y, person["name"], d=p(32), online=person["online"])
            c.text(p(64), y + p(1), person["name"], font=f.bold, fill=T.TEXT if person["online"] else T.MUTED)
            c.text(p(64), y + p(18), person["tag"], font=f.small, fill=T.DIM)
            c.text(c.W - p(22), y + p(1), fmt_n(person["total"]), font=f.bold,
                   fill=T.ACCENT_SOFT if person.get("me") else T.TEXT, anchor="ne")
            today = person.get("today")
            c.text(c.W - p(22), y + p(18), f"{person['game']} this game" + (f" · {today:,} today" if today is not None else ""),
                   font=f.small, fill=T.DIM, anchor="ne")
            y += p(44)

    # -- recent games
    def _paint_recent(self, c):
        f, p = self.f, self.px
        c.text(p(22), p(20), "Recent games", font=f.h2)
        self._help(c, p(22) + f.h2.measure("Recent games") + p(8), p(22), "status")
        T.pill(c, c.W - p(22), p(14), "All games", lambda: self.go("matches"), anchor="ne", h=p(32), font=f.small_bold)
        data = self._ov_data
        if not data:
            return
        rows = self.recent.rows
        if not rows:
            c.text(p(22), p(70), "No games yet. Play a match and it shows up here.", font=f.body, fill=T.MUTED)
            return
        y = p(62)
        for i, r in enumerate(rows[:5]):
            tag = f"recent{i}"
            hit = c.create_rectangle(p(12), y - p(5), c.W - p(12), y + p(39), fill="", outline="", tags=("c", tag))
            img, _ = T.circle_image(p(34), T.hl(26), T.hl(12), outline=T.hl(30))
            c.image(p(22), y, img, tags=(tag,))
            c.text(p(39), y + p(17), "" if f.icons else "■", font=f.icon, fill=T.ACCENT_SOFT, anchor="center",
                   tags=(tag,))
            c.text(p(68), y + p(1), M.fmt_when(r["started"], data["d"]["now"]), font=f.bold, tags=(tag,))
            where = (r.get("map") or {}).get("landed_at") or ""
            squad = ", ".join(x["name"] for x in r["squad"][:2]) or ("solo" if r["confirmed"] else "")
            sub = " · ".join(x for x in (where, squad) if x) or r["source"]
            c.text(p(68), y + p(19), sub, font=f.small, fill=T.DIM, tags=(tag,))
            label, kind = self._status(r, data["d"]["in_match"])
            T.badge(c, c.W - p(84), y + p(17), label, kind, anchor="e", tag=(tag,))
            c.text(c.W - p(24), y + p(17), str(r["chests"]), font=f.mid, anchor="e", tags=(tag,))
            c.tag_raise(hit)
            c.on_click(tag, lambda r=r: self._open_match(r))
            y += p(46)

    # -- best place to land
    def _paint_land(self, c):
        f, p = self.f, self.px
        data = self._ov_data
        drops = (data or {}).get("drops") or []
        best = next((x for x in drops if x["games"] >= 3), None)
        T.icon_circle(c, p(22), (c.H - p(40)) / 2, "" if f.icons else "•", d=p(40), active=True)
        if not best:
            c.text(p(78), c.H / 2 - p(10), "Your best place to land", font=f.h2, anchor="w")
            c.text(p(78), c.H / 2 + p(12), "Shows up after a few matches, once the map has read them.", font=f.small,
                   fill=T.DIM, anchor="w")
            return
        c.text(p(78), c.H / 2 - p(12), "Your best place to land", font=f.small_bold, fill=T.MUTED, anchor="w")
        c.text(p(78), c.H / 2 + p(10), best["place"], font=f.mid, anchor="w")
        x = p(92) + f.mid.measure(best["place"]) + p(10)
        x0, _y0, x1, _ = T.badge(c, x, c.H / 2 + p(10), f"{best['avg_chests']:.1f} chests a game", "orange", anchor="w")
        c.text(x1 + p(12), c.H / 2 + p(10), f"over {plural(best['games'], 'landing')} · best game {best['best']}",
               font=f.small, fill=T.DIM, anchor="w")
        T.pill(c, c.W - p(22), c.H / 2, "Where to land", lambda: (self.go("map"), self._show_map_tab("drops")),
               anchor="e", h=p(34))

    # ==========================================================================================
    # Games
    # ==========================================================================================
    def _columns(self):
        cols = [{"title": "When", "width": 170}, {"title": "Chests", "width": 72, "anchor": "e"},
                {"title": "Squad total", "width": 100, "anchor": "e"},
                {"title": "Opened", "width": 76, "anchor": "e"}, {"title": "Broken", "width": 76, "anchor": "e"},
                {"title": "Per min", "width": 74, "anchor": "e"}]
        if self.detail_cols:
            cols += [{"title": "Elims", "width": 62, "anchor": "e"},
                     {"title": "Length", "width": 66, "anchor": "e"}, {"title": "Treasures", "width": 120}]
        cols += [{"title": "Landed at", "width": 150}, {"title": "Squad", "width": 140, "stretch": True},
                 {"title": "Status", "width": 104, "anchor": "center"}]
        return cols

    def _build_matches(self):
        s = T.Surface(self.stack)
        for i in range(6):
            s.columnconfigure(i, weight=1, uniform="mc")
        s.rowconfigure(2, weight=1)
        self._title_card(s, "matches", 6)
        self.m_sum_cards = {}
        self.m_sum = {}
        for i, (key, label) in enumerate([("games", "Games"), ("chests", "My chests"), ("squad", "Squad chests"),
                                          ("best", "Best game"), ("play", "Playtime"), ("rate", "Chests a minute")]):
            self.m_sum[key] = Ref("")
            self.m_sum_cards[key] = self._card(s, lambda c, k=key, lb=label: self._paint_sum(c, k, lb), 1, i, height=92)
        self.m_card = self._card(s, self._paint_table_card, 2, 0, colspan=6, height=360)
        self.m_table = T.Table(self.m_card, self.f, self._columns(), on_select=self._show_match,
                               empty="No games in this period", multi=True, on_marks=self._marks_changed)
        self.m_period = T.Tabs(self.m_card, [(lbl, v) for lbl, v in PERIODS], self.period, self._period, self.f,
                               h=self.px(34), pad=self.px(14), radius=self.px(11))
        self.m_more = tk.BooleanVar(value=self.detail_cols)
        self.m_switch = T.Switch(self.m_card, self.m_more, self._more_cols)
        self.m_compare = Ref("", self.m_card.repaint)
        self.m_detail_card = self._card(s, self._paint_detail, 3, 0, colspan=6, height=150)
        self.m_detail = None
        self.m_detail_lines: list[str] = []
        return s

    def _paint_sum(self, c, key, label):
        f, p = self.f, self.px
        c.text(p(20), p(16), label.upper(), font=f.cap, fill=T.DIM)
        text = self.m_sum[key].text or "-"
        font = f.big if f.big.measure(text) <= c.W - p(40) else f.h2  # ("~61h 20m" in a small window)
        c.text(p(20), p(34), text, font=font, fill=T.ACCENT_SOFT if key == "chests" else T.TEXT)

    def _paint_table_card(self, c):
        f, p = self.f, self.px
        c.put(self.m_period, p(18), p(16))
        x = p(18) + int(self.m_period.cget("width")) + p(22)
        c.put(self.m_switch, x, p(21))
        c.text(x + p(52), p(33), "More detail", font=f.body, fill=T.MUTED, anchor="w")
        if self.m_compare.text:
            T.pill(c, c.W - p(18), p(16), self.m_compare.text, self._compare_marked, kind="primary", anchor="ne", h=p(34))
        else:
            c.text(c.W - p(22), p(33), "Tip: Ctrl+click games to compare them on the map", font=f.small, fill=T.DIM,
                   anchor="e")
        top = p(64)
        T.well(c, p(12), top, c.W - p(12), c.H - p(12))
        c.put(self.m_table, p(18), top + p(6), width=c.W - p(36), height=c.H - top - p(24), tint=False)

    def _more_cols(self):
        self.detail_cols = bool(self.m_more.get())
        self.app.cfg["app_more_columns"] = self.detail_cols
        self.m_table.columns = self._columns()
        self._m_sig = None
        if self._snap:
            self._fill_matches(self._snap)

    def _period(self, v):
        self.period = v
        self.m_period.set(v)
        if self._snap:
            self._fill_matches(self._snap)

    def _match_row(self, r, now) -> dict:
        when = M.fmt_when(r["started"], now)
        if r["current"] and self._snap and self._snap["in_match"]:
            when = "Now · " + when
        squad = ", ".join(q["name"] for q in r["squad"]) or ("solo" if r["replay"] and r["confirmed"] else "")
        tr = r["treasures"]
        pirate = any(treasures.describe(x.get("prize"))["pirate"] for x in tr)
        tr_text = "PIRATE SHOP" if pirate else ", ".join(x.get("name") or "prize" for x in tr)
        landed = (r.get("map") or {}).get("landed_at") or ""
        status, kind = self._status(r, bool(self._snap and self._snap["in_match"]))
        st = r.get("squad_total")
        values = [when, r["chests"], fmt_n(st) if st is not None else "-", r["opened"], fmt_n(r["broken"]),
                  M.fmt_rate(r.get("per_min"))]
        sort = [r["started"], r["chests"], st, r["opened"], r["broken"], r.get("per_min")]
        colors = {0: T.TEXT, 1: T.ACCENT_SOFT, 2: T.TEXT if st is not None else T.DIM, 3: T.TEXT,
                  4: T.TEXT if r["broken"] else T.DIM, 5: T.TEXT if r.get("per_min") is not None else T.DIM}
        if self.detail_cols:
            values += [fmt_n(r["kills"]), M.fmt_length(r["length"] or r.get("play")), tr_text]
            sort += [r["kills"], r["length"] or r.get("play"), len(tr) or None]
            colors.update({6: T.TEXT if r["kills"] else T.DIM, 7: T.MUTED, 8: T.GOLD if pirate else T.AMBER})
        n = len(values)
        values += [landed, squad, status]
        sort += [landed or None, squad or None, status]
        colors.update({n: T.MUTED, n + 1: T.MUTED})
        if r.get("not_me"):
            colors = {i: T.DIM for i in range(len(values))}
        return {"values": values, "sort": sort, "data": r, "bold": (1, 2), "colors": colors, "badge": {n + 2: kind},
                "accent": T.ACCENT if r["current"] else (T.GOLD if tr else None)}

    def _fill_matches(self, d):
        rows = M.filter_rows(d["rows"], self.period, d["now"])
        s = M.summary(rows, d["now"])
        self.m_sum["games"].configure(text=f"{s['games']:,}")
        self.m_sum["chests"].configure(text=f"{s['chests']:,}")
        self.m_sum["squad"].configure(text=f"{s['squad_chests']:,}")
        self.m_sum["best"].configure(text=f"{s['best']:,}")
        self.m_sum["play"].configure(text=("~" if s["play_est"] else "") + M.fmt_playtime(s["play_s"]))
        self.m_sum["rate"].configure(text=M.fmt_rate(s["per_min"]))
        for c in self.m_sum_cards.values():
            c.repaint()
        new = [self._match_row(r, d["now"]) for r in rows]
        sig = ([row["values"] for row in new], self.detail_cols)
        if sig == getattr(self, "_m_sig", None):
            return  # nothing changed: keep the list (and what's picked in it) as it is
        self._m_sig = sig
        t = self.m_table

        def same(a, b):
            return (a["replay"] and a["replay"] == b["replay"]) or a["started"] == b["started"]

        sel, marked = t.selected_data(), t.marked_data()
        on_marks, t.on_marks = t.on_marks, None
        t.set_rows(new)
        t.on_marks = on_marks
        t.selected = next((i for i, row in enumerate(t.rows) if sel is not None and same(row["data"], sel)), None)
        t.marked = {i for i, row in enumerate(t.rows) if any(same(row["data"], m) for m in marked)}
        t._draw()
        if t.selected is not None:
            self._show_match(t.rows[t.selected]["data"])
        if on_marks:
            on_marks(t.marked_data())

    def _marks_changed(self, marked):
        files = [r["replay"] for r in marked if r.get("on_map")]
        self.m_compare.configure(text=f"Compare {len(files)} on the map  ›" if len(files) >= 2 else "")

    def _compare_marked(self):
        files = [r["replay"] for r in self.m_table.marked_data() if r.get("on_map")]
        if len(files) >= 2:
            self._compare(files[:12])

    def _open_match(self, r):
        self.go("matches")
        if self.period is not None and r["started"] < time.time() - self.period * 86400 and not r["current"]:
            self._period(None)
        for i, row in enumerate(self.m_table.rows):
            if row["data"]["started"] == r["started"]:
                self.m_table.select(i)
                self.m_table.canvas.yview_moveto(max(0.0, i / max(1, len(self.m_table.rows)) - 0.02))
                return

    def _show_match(self, r):
        self.m_detail = r
        self.m_detail_card.repaint()

    def _paint_detail(self, c):
        f, p = self.f, self.px
        r = self.m_detail
        self.m_detail_lines = []
        if r is None:
            T.icon_circle(c, p(22), (c.H - p(40)) / 2, "" if f.icons else "•", d=p(40))
            c.text(p(78), c.H / 2, "Click a game above to see where you landed, who was with you and what you found.",
                   font=f.body, fill=T.MUTED, anchor="w")
            return
        now = self._snap["now"] if self._snap else time.time()
        title = M.fmt_when(r["started"], now) + (f"  ·  {M.fmt_length(r['length'])} long" if r["length"] else "")
        if r.get("per_min") is not None:
            title += f"  ·  {r['per_min']:.1f} chests a minute"
        status, kind = self._status(r, bool(self._snap and self._snap["in_match"]))
        c.text(p(24), p(16), title, font=f.h2)
        T.badge(c, p(36) + f.h2.measure(title), p(18), status, kind)
        self.m_detail_lines.append(r["source"])
        c.text(p(24), p(40), r["source"], font=f.small, fill=T.DIM)
        if r.get("on_map"):
            T.pill(c, c.W - p(22), p(14), "Show on map", lambda: self._open_map(False, r["replay"]), kind="primary",
                   anchor="ne", h=p(34))
        elif r["replay"]:
            c.text(c.W - p(22), p(30), "On the map once it has read this match", font=f.small, fill=T.DIM, anchor="e")
        x = p(24)
        for label, val, key in (("Chests", r["chests"], None), ("Opened", r["opened"], "opened"),
                                ("Broken", r["broken"], "broken"), ("Squad total", r.get("squad_total"), "squad_total"),
                                ("Elims", r["kills"], None)):
            c.text(x, p(70), label.upper(), font=f.cap, fill=T.DIM)
            if key:
                self._help(c, x + f.cap.measure(label.upper()) + p(6), p(67), key)
            c.text(x, p(86), fmt_n(val), font=f.big, fill=T.ACCENT_SOFT if label == "Chests" else T.TEXT)
            x += max(p(104), f.cap.measure(label.upper()) + p(44))
        x += p(10)
        ms = r.get("map")
        lines = []
        if ms:
            first = "-" if ms.get("first_chest") is None else f"{ms['first_chest']:.0f} s"
            lines.append(f"Landed at {ms.get('landed_at', '?')} · alive {M.fmt_length(ms.get('alive'))} · "
                         f"{ms.get('per_min', 0):.1f} chests a minute · first chest {first} after landing")
        if r["squad"]:
            me = self.app.cfg.get("name") or "You"
            lines.append(f"{me} {r['chests']}  ·  " + "  ·  ".join(
                f"{q['name']} {q['chests']}" + (f" ({q['platform']})" if q["platform"] and q["platform"] != "PC" else "")
                for q in r["squad"]) + (f"   =  {r['squad_total']:,} for the squad" if r.get("squad_total") is not None else ""))
        elif r.get("credited_by"):
            lines.append(f"Counted from {r['credited_by']}'s replay: the squad's numbers are on their tracker")
        else:
            lines.append("Just you" if r["confirmed"] else "Squad shows up once the replay is read")
        for x_ in r["treasures"]:
            dd = treasures.describe(x_.get("prize"))
            lines.append(("PIRATE SHOP ACCESS!" if dd["pirate"] else f"Treasure: {dd['name']} ({dd['kind']})")
                         + f" at {M.fmt_length(x_.get('t'))}")
        y = p(68)
        for k, line in enumerate(lines[:4]):
            self.m_detail_lines.append(line)
            item = c.text(x, y, line, font=f.body if k == 0 else f.small,
                          fill=T.GOLD if line.startswith(("PIRATE", "Treasure")) else (T.TEXT if k == 0 else T.MUTED),
                          width=c.W - x - p(20))
            box = c.bbox(item)
            y = (box[3] if box else y + p(18)) + p(4)  # the next line goes under this one, however it wrapped

    # ==========================================================================================
    # Squad
    # ==========================================================================================
    def _build_team(self):
        sp, s = self._scroll_page("team", 3)
        self.t_s = s
        self._team_data = None
        self.t_banner = self._card(s, self._paint_team_banner, 1, 0, colspan=3, height=104, glows=GLOW_RIGHT())
        self.t_people_cards = []
        self.t_mates = T.Table(s, self.f, on_select=lambda m: m and self.open_player(m["name"], [m["name"]]), columns=[
            {"title": "Name", "width": 200}, {"title": "Platform", "width": 120},
            {"title": "", "width": 130, "nosort": True, "stretch": True},
            {"title": "Games together", "width": 130, "anchor": "e"},
            {"title": "Their chests", "width": 110, "anchor": "e"}, {"title": "Per game", "width": 90, "anchor": "e"},
            {"title": "Best", "width": 70, "anchor": "e"}, {"title": "Last played", "width": 110, "anchor": "e"}],
            empty="Nobody yet: squadmates show up after your replays are read")
        self.t_mates_card = None
        return sp

    def _paint_team_banner(self, c):
        f, p = self.f, self.px
        d = self._team_data
        if d is None:
            return
        T.icon_circle(c, p(24), (c.H - p(44)) / 2, "" if f.icons else "•", d=p(44), active=True)
        if d["team_code"]:
            net = (self.app.net.status if self.app.cfg.get("internet_sync", True) and not self.app.cfg.get("offline")
                   else ("offline mode" if self.app.cfg.get("offline") else "internet sync off"))
            c.text(p(84), c.H / 2 - p(12), f"Team  {d['team_code']}", font=f.mid, anchor="w")
            ok = net == "connected"
            word = "Connected" if ok else ("Offline mode" if net == "offline mode" else
                                           "Sync off" if net in ("off", "internet sync off") else str(net)[:40].capitalize())
            T.badge(c, p(84), c.H / 2 + p(16), word, "green" if ok else "amber", anchor="w")
        else:
            c.text(p(84), c.H / 2 - p(12), "No team yet", font=f.mid, anchor="w")
            c.text(p(84), c.H / 2 + p(14), "Everyone who enters the same team code shares their chest counts live.",
                   font=f.small, fill=T.MUTED, anchor="w")
        c.text(c.W - p(26), c.H / 2 - p(14), "TEAM TOTAL", font=f.cap, fill=T.DIM, anchor="e")
        self._help(c, c.W - p(26) - f.cap.measure("TEAM TOTAL") - p(24), c.H / 2 - p(22), "team")
        c.text(c.W - p(26), c.H / 2 + p(12), fmt_n(d["team_total"]), font=f.huge, fill=T.ACCENT_SOFT, anchor="e")
        T.pill(c, c.W - p(26) - f.huge.measure(fmt_n(d["team_total"])) - p(30), c.H / 2, "Team settings",
               lambda: self.go("settings"), anchor="e")

    def _person_clicked(self, person):
        if person.get("me"):
            self.go("matches")  # your own games
            return
        names = list(person.get("fortnite_names") or []) or [person.get("fname") or person["name"]]
        self.open_player(person["name"], names)

    def open_player(self, title, names):
        """Shows one squadmate's games and numbers. names: the in-game names they play as."""
        self.player_sel = {"title": str(title), "names": [n for n in names if n]}
        self._player_sig = None
        self.go("player")

    def _paint_person(self, c, person):
        f, p = self.f, self.px
        T.avatar(c, p(20), p(18), person["name"], d=p(44), online=person["online"])
        c.text(p(76), p(20), person["name"], font=f.h2)
        c.text(p(76), p(42), person["tag"], font=f.small, fill=T.DIM)
        T.badge(c, c.W - p(20), p(22), "Online" if person["online"] else "Offline",
                "green" if person["online"] else "muted", anchor="ne")
        c.text(c.W - p(20), p(52), "Your games  ›" if person.get("me") else "Their games  ›", font=f.small_bold,
               fill=T.ACCENT_SOFT, anchor="e")
        col_w = (c.W - p(40)) / 3
        for i, (label, val, accent) in enumerate((("THIS GAME", person["game"], False), ("TODAY", person.get("today"), False),
                                                  ("TOTAL", person["total"], bool(person.get("me"))))):
            x = p(20) + i * col_w
            c.text(x, p(82), label, font=f.cap, fill=T.DIM)
            c.text(x, p(98), fmt_n(val), font=f.big, fill=T.ACCENT_SOFT if accent else T.TEXT)

    def _fill_team(self, d):
        people = self._people(d)
        sig = (repr(people), len(d["files"]), getattr(self.app.replays.db, "version", 0), d["team_code"],
               self.app.net.status, d["team_total"], time.strftime("%Y%m%d%H"))
        if sig == getattr(self, "_team_sig", None):
            return
        self._team_sig = sig
        self._team_data = d
        self.t_banner.repaint()
        s = self.t_s
        for c in self.t_people_cards:
            if c in s.cards:
                s.cards.remove(c)
            c.destroy()
        self.t_people_cards = []
        for i, person in enumerate(people):
            c = self._card(s, lambda cc, pp=person: self._paint_person(cc, pp), 2 + i // 3, i % 3, height=150)
            c.configure(cursor="hand2")
            c.bind("<ButtonRelease-1>", lambda e, pp=person: self._person_clicked(pp))
            self.t_people_cards.append(c)
        row = 3 + (len(people) - 1) // 3
        mates = M.played_with(d["files"], d["names"])
        guests = {M.norm(n) for n in self.app.cfg.get("console_teammates") or []}
        claimed = set()
        for pr in d["peers"]:
            claimed |= set(pr.get("fortnite_names") or [])
        rows = []
        for m in mates:
            n = M.norm(m["name"])
            kind = "Chest Tracker" if n in claimed else ("Console guest" if n in guests else "")
            avg = m["chests"] / m["games"] if m["games"] else 0
            rows.append({"values": [m["name"], m["platform"] or "-", kind, m["games"], f"{m['chests']:,}", f"{avg:.1f}",
                                    m["best"], M.fmt_ago(m["last"], d["now"])],
                         "sort": [m["name"].lower(), m["platform"], kind or None, m["games"], m["chests"], avg, m["best"],
                                  m["last"]],
                         "colors": {1: T.MUTED, 3: T.TEXT, 4: T.ACCENT_SOFT, 7: T.DIM}, "bold": (0,), "data": m,
                         "badge": {2: "orange"} if kind else {}})
        self.t_mates.set_rows(rows)
        if self.t_mates_card is None:
            def paint(c):
                c.text(self.px(24), self.px(20), "Played with", font=self.f.h2)
                c.text(self.px(24), self.px(44), "Everyone who was in your squad in a replay on this PC. " + SQUAD_COUNT_NOTE,
                       font=self.f.small, fill=T.DIM, width=c.W - self.px(48))
                top = self.px(96)
                T.well(c, self.px(12), top, c.W - self.px(12), c.H - self.px(12))
                c.put(self.t_mates, self.px(18), top + self.px(6), width=c.W - self.px(36),
                      height=c.H - top - self.px(24), tint=False)

            self.t_mates_card = self._card(s, paint, row, 0, colspan=3, height=520)
        else:
            self.t_mates_card.grid(row=row, column=0, columnspan=3)

    # ==========================================================================================
    # Player (one squadmate)
    # ==========================================================================================
    def _build_player(self):
        sp = T.ScrollSurface(self.stack)
        s = sp.inner
        for i in range(4):
            s.columnconfigure(i, weight=1, uniform="plc")
        self.player_sel = getattr(self, "player_sel", None)
        self._pl = None

        def title(c):
            f, p = self.f, self.px
            x0, *_ = T.pill(c, p(6), p(14), "‹  Squad", lambda: self.go("team"), h=p(34), pad=p(14), font=f.body)
            sel = self.player_sel
            name = sel["title"] if sel else "Pick someone"
            T.avatar(c, p(100), p(8), name, d=p(46))
            c.text(p(158), p(6), name, font=self.f.title)
            sub = ("Their games with you, from the replays on this PC (their chests are what your replays saw)"
                   if sel else "Click someone on the Squad page to see their games here.")
            c.text(p(160), p(52), sub, font=self.f.body, fill=T.MUTED)
            if sel and self._pl and self._pl["rows"]:
                T.pill(c, c.W - p(6), p(16), "Their games on the map  ›", self._player_on_map, kind="primary",
                       anchor="ne", h=p(38))

        self.pl_title = T.Card(s, title, height=self.px(86), fonts=self.f, plain=True)
        self.pl_title.grid(row=0, column=0, columnspan=4, sticky="ew", padx=self.px(14), pady=(self.px(8), 0))
        self.pl_tiles = {}
        for i, (key, label) in enumerate([("games", "Games with you"), ("chests", "Their chests"), ("avg", "Per game"),
                                          ("best", "Best game"), ("today", "Today"), ("kills", "Eliminations"),
                                          ("favourite", "Favourite drop"), ("last", "Last played")]):
            self.pl_tiles[key] = self._card(s, lambda c, k=key, lb=label: self._paint_pl_tile(c, k, lb), 1 + i // 4, i % 4,
                                            height=96)
        self.pl_table = T.Table(s, self.f, [
            {"title": "When", "width": 170}, {"title": "Chests", "width": 72, "anchor": "e"},
            {"title": "Opened", "width": 76, "anchor": "e"}, {"title": "Broken", "width": 76, "anchor": "e"},
            {"title": "Elims", "width": 64, "anchor": "e"}, {"title": "Landed at", "width": 170},
            {"title": "With", "width": 160, "stretch": True}, {"title": "", "width": 120, "anchor": "center", "nosort": True}],
            on_select=self._player_game_clicked, empty="No games together on this PC yet.")

        def table(c):
            c.text(self.px(24), self.px(20), "Their games", font=self.f.h2)
            c.text(self.px(24), self.px(44), "Click a game to watch it on the map from their side.", font=self.f.small,
                   fill=T.DIM)
            top = self.px(70)
            T.well(c, self.px(12), top, c.W - self.px(12), c.H - self.px(12))
            c.put(self.pl_table, self.px(18), top + self.px(6), width=c.W - self.px(36), height=c.H - top - self.px(24),
                  tint=False)

        self.pl_card = self._card(s, table, 3, 0, colspan=4, height=520)
        tk.Frame(s, bg=T.BASE, height=self.px(10)).grid(row=4, column=0)
        return sp

    def _paint_pl_tile(self, c, key, label):
        f, p = self.f, self.px
        c.text(p(20), p(16), label.upper(), font=f.cap, fill=T.DIM)
        st = (self._pl or {}).get("sum")
        if not st:
            c.text(p(20), p(36), "-", font=f.big, fill=T.DIM)
            return
        val = {"games": f"{st['games']:,}", "chests": f"{st['chests']:,}", "avg": f"{st['avg']:.1f}",
               "best": f"{st['best']:,}", "today": f"{st['today']:,}", "kills": f"{st['kills']:,}",
               "favourite": st["favourite"] or "-", "last": (self._pl or {}).get("last") or "-"}[key]
        big = key not in ("favourite", "last")
        c.text(p(20), p(36) if big else p(42), val, font=f.big if big else f.mid,
               fill=T.ACCENT_SOFT if key == "chests" else T.TEXT, width=c.W - p(36))
        if key == "favourite" and st.get("favourite_n"):
            c.text(p(20), c.H - p(14), f"landed there {plural(st['favourite_n'], 'time')}", font=f.small, fill=T.DIM,
                   anchor="w")

    def _fill_player(self, d):
        sel = self.player_sel
        sig = (repr(sel), len(d["files"]), len(d["maps"]))
        if sig == getattr(self, "_player_sig", None):
            return
        self._player_sig = sig
        if sel:
            rows = M.player_games(d["files"], d["maps"], sel["names"], d["names"], self._pois() if d["maps"] else None)
            self._pl = {"rows": rows, "sum": M.player_summary(rows, d["now"]),
                        "last": M.fmt_ago(rows[0]["started"], d["now"]) if rows else None}
        else:
            rows, self._pl = [], None
        table = []
        for r in rows:
            table.append({"values": [M.fmt_when(r["started"], d["now"]), r["chests"], r["opened"], r["broken"],
                                     fmt_n(r["kills"]), r["landed_at"] or "", ", ".join(r["with"][:3]),
                                     "Watch on map" if r["on_map"] else "Not on the map"],
                          "sort": [r["started"], r["chests"], r["opened"], r["broken"], r["kills"], r["landed_at"] or None,
                                   None, None],
                          "colors": {1: T.ACCENT_SOFT, 3: T.TEXT if r["broken"] else T.DIM, 5: T.MUTED, 6: T.MUTED,
                                     7: T.ACCENT_SOFT if r["on_map"] else T.DIM},
                          "bold": (1,), "data": r})
        self.pl_table.set_rows(table)
        self.pl_title.repaint()
        for c in self.pl_tiles.values():
            c.repaint()

    def _player_game_clicked(self, r):
        if not r or not r.get("on_map"):
            return
        mw = self._map_view()
        if mw is None:
            return
        self.map_sel, self.map_overlay = r["replay"], []
        try:
            mw.show_match(r["replay"], player=r["name"])
        except TypeError:
            mw.show_match(r["replay"])
        if self._snap and getattr(self, "games_list", None) is not None:
            self._fill_side(self._snap)

    def _player_on_map(self):
        """The Map page, its games list showing just the games with this player."""
        sel = self.player_sel
        rows = (self._pl or {}).get("rows") or []
        if not sel or not rows:
            return
        self.go("map")
        self.win.update_idletasks()
        self._show_map_tab("map")
        self._set_player_filter(rows[0]["name"])
        first = next((r for r in rows if r["on_map"]), None)
        if first:
            self._player_game_clicked(first)

    # ==========================================================================================
    # Odds calculator
    # ==========================================================================================
    def _build_odds(self):
        sp = T.ScrollSurface(self.stack)
        s = sp.inner
        for i in range(4):
            s.columnconfigure(i, weight=1, uniform="oddsc")
        self.odds_who = "me"
        self.odds_target = 0.5
        self._odds = None
        self._title_card(s, "odds", 4, actions=[("‹  Treasures", lambda: self.go("treasures"), "ghost")])
        self.odds_hero = self._card(s, self._paint_odds_hero, 1, 0, colspan=4, height=190, glows=GLOW_RIGHT())
        self.odds_who_tabs = T.Tabs(self.odds_hero, [("Just me", "me"), ("Whole squad", "squad")], "me", self._odds_set_who,
                                    self.f, h=self.px(34), pad=self.px(14), radius=self.px(10))
        self.odds_goal = self._card(s, self._paint_odds_goal, 2, 0, colspan=4, height=190)
        self.odds_slider = T.Slider(self.odds_goal, 10, 99, None, width=self.px(320))
        self.odds_slider.v = 50
        self.odds_slider.command = self._odds_slide
        self.odds_table = T.Table(s, self.f, [
            {"title": "Prize", "width": 200, "stretch": True}, {"title": "Odds", "width": 210, "anchor": "e"},
            {"title": "Chance by now", "width": 130, "anchor": "e"}, {"title": "Chests for target", "width": 150, "anchor": "e"},
            {"title": "More to go", "width": 130, "anchor": "e"}], empty="")

        def table(c):
            c.text(self.px(24), self.px(20), "Every prize", font=self.f.h2)
            c.text(self.px(24), self.px(44), f"The same maths for each treasure prize, for a {round(self.odds_target * 100)}% "
                   "chance.", font=self.f.small, fill=T.DIM)
            top = self.px(70)
            T.well(c, self.px(12), top, c.W - self.px(12), c.H - self.px(12))
            c.put(self.odds_table, self.px(18), top + self.px(6), width=c.W - self.px(36), height=c.H - top - self.px(24),
                  tint=False)

        self.odds_list = self._card(s, table, 3, 0, colspan=4, height=104 + 38 * (len(treasures.ODDS) + 1))

        def note(c):
            c.text(self.px(24), self.px(18), "Good to know", font=self.f.h2)
            c.text(self.px(24), self.px(44), "Every chest is its own roll: past chests don't make the next one luckier. "
                   "The odds are Epic's posted rates per chest in a normal match: every chest counts. Treasure Hours "
                   "raise some of them (not counted here). This is just maths, not a prediction.", font=self.f.small, fill=T.MUTED,
                   width=c.W - self.px(48))

        self._card(s, note, 4, 0, colspan=4, height=110)
        tk.Frame(s, bg=T.BASE, height=self.px(10)).grid(row=5, column=0)
        return sp

    def _odds_set_who(self, v):
        self.odds_who = v
        if self._snap:
            self._fill_odds(self._snap)

    def _odds_slide(self, value):
        self.odds_target = max(0.1, min(0.99, round(float(value)) / 100))
        if self._snap:
            self._fill_odds(self._snap)

    def _odds_preset(self, pct):
        self.odds_slider.v = pct
        self.odds_slider._paint_now()
        self._odds_slide(pct)

    def _fill_odds(self, d):
        share, known = M.tiered_share(d["maps"], d["names"]) if treasures.TIERED_ONLY else (1.0, 0)
        me = int(d["total"])
        squad = int(d.get("team_total") or me) if d.get("has_team") or d.get("guests") else me
        chests = squad if self.odds_who == "squad" else me
        tiered = int(round(chests * share))
        pz = M.pace(d["rows"], 14, d["now"])
        per_day = pz["chests_per_day"] * (chests / me if me else 1)  # (the squad: my pace scaled up, an estimate)
        per_game = pz["chests_per_game"] * (chests / me if me else 1)
        one_in = treasures.ODDS["pirate shop"]
        need = M.chests_for(self.odds_target, one_in)
        need_all = int(round(need / share)) if share else need
        more = max(0, need_all - chests)
        self._odds = {"chests": chests, "me": me, "tiered": tiered, "share": share, "known": known, "one_in": one_in,
                      "chance": M.chance_by_now(tiered, one_in), "need": need_all, "more": more,
                      "games": (more / per_game) if per_game else None, "days": (more / per_day) if per_day else None,
                      "squad": self.odds_who == "squad"}
        rows = []
        for r in M.odds_table(chests, share, self.odds_target):
            pir = r["kind"] == "pirate shop"
            rows.append({"values": [r["label"], f"1 in {r['one_in']:,} " + ("per chest" if r["any_chest"] else "tiered"), f"{r['chance'] * 100:.1f}%" if r["chance"] < 0.995
                                    else "over 99%", f"{r['need']:,}", "done" if not r["more"] else f"{r['more']:,}"],
                         "sort": [r["label"], r["one_in"], r["chance"], r["need"], r["more"]],
                         "colors": {0: T.GOLD if pir else T.TEXT, 2: T.ACCENT_SOFT, 4: T.GREEN if not r["more"] else T.TEXT},
                         "bold": (0,) if pir else (), "accent": T.GOLD if pir else None, "data": r})
        self.odds_table.set_rows(rows)
        for c in (self.odds_hero, self.odds_goal, self.odds_list):
            c.repaint()

    def _paint_odds_hero(self, c):
        f, p = self.f, self.px
        o = self._odds
        c.put(self.odds_who_tabs, p(24), p(18))
        self.odds_who_tabs.set(self.odds_who)
        if not o:
            return
        c.text(c.W - p(24), p(35), f"Pirate Shop access  ·  1 in {o['one_in']:,} per chest" + ("" if o["share"] >= 1 else " (tiered)"), font=f.bold,
               fill=T.GOLD, anchor="e")
        pct = o["chance"] * 100
        c.text(p(24), p(70), f"{pct:.1f}%" if pct >= 0.1 else f"{pct:.2f}%", font=f.hero, fill=T.TEXT)
        c.text(p(24) + f.hero.measure(f"{pct:.1f}%") + p(16), p(84),
               "chance a Pirate Shop access would have come up by now", font=f.body, fill=T.MUTED)
        w = c.W - p(48)
        track, _ = T.pill_image(int(w), p(10), T._mix(T.hl(0)[:3], (0, 0, 0), 0.7), T._mix(T.hl(0)[:3], (0, 0, 0), 0.78))
        c.image(p(24), p(136), track)
        fill_w = max(p(10), int(w * min(1.0, o["chance"])))
        bar, _ = T.pill_image(fill_w, p(10), (255, 214, 102), (230, 160, 40))
        c.image(p(24), p(136), bar)
        who = "your squad's" if o["squad"] else "your"
        if o["share"] >= 1:
            text = f"From {who} {o['chests']:,} chests."
        else:
            est = "  (estimate: your own tiered share applied to the squad)" if o["squad"] else ""
            text = f"From {who} {o['tiered']:,} tiered chests ({o['share'] * 100:.0f}% of {o['chests']:,} chests){est}."
        c.text(p(24), p(160), text, font=f.small, fill=T.DIM)

    def _paint_odds_goal(self, c):
        f, p = self.f, self.px
        o = self._odds
        c.text(p(24), p(20), "What chance do you want?", font=f.h2)
        c.put(self.odds_slider, p(24), p(54))
        c.text(p(24) + int(self.odds_slider.cget("width")) + p(12), p(68), f"{round(self.odds_target * 100)}%",
               font=f.mid, fill=T.ACCENT_SOFT, anchor="w")
        x = p(24) + int(self.odds_slider.cget("width")) + p(80)
        for pct in (50, 75, 90, 95):
            _x0, _y0, x1, _ = T.pill(c, x, p(52), f"{pct}%", lambda v=pct: self._odds_preset(v), h=p(32), pad=p(12),
                                     font=f.small_bold, active=round(self.odds_target * 100) == pct)
            x = x1 + p(6)
        if not o:
            return
        c.text(p(24), p(104), f"{o['need']:,} chests", font=f.huge, fill=T.TEXT)
        c.text(p(24) + f.huge.measure(f"{o['need']:,} chests") + p(16), p(116),
               f"for a {round(self.odds_target * 100)}% chance of a Pirate Shop access", font=f.body, fill=T.MUTED)
        if o["more"]:
            bits = [f"{o['more']:,} more to go"]
            if o["games"]:
                bits.append(f"about {o['games']:,.0f} more matches")
            if o["days"]:
                bits.append(f"about {o['days']:,.0f} days of playing at your recent pace")
            line = "  ·  ".join(bits)
        else:
            line = "You're already past that many chests."
        c.text(p(24), p(156), line, font=f.body, fill=T.TEXT)

    # ==========================================================================================
    # Treasures
    # ==========================================================================================
    def _build_treasures(self):
        sp = T.ScrollSurface(self.stack)
        s = sp.inner
        for i in range(3):
            s.columnconfigure(i, weight=1, uniform="treasuresc")
        self._title_card(s, "treasures", 3, actions=[("Odds calculator  ›", lambda: self.go("odds"), "primary")])
        self._tr = None
        self.tr_hero = self._card(s, self._paint_pirate, 1, 0, colspan=3, height=124, glows=GLOW_GOLD)
        self.tr_tiles = [self._card(s, lambda c, i=i: self._paint_tr_tile(c, i), 2, i, height=116) for i in range(3)]
        self.tr_weeks = [self._card(s, lambda c, i=i: self._paint_week(c, i), 3 + i // 3, i % 3, height=226)
                         for i in range(6)]
        self.tr_table = T.Table(s, self.f, [{"title": "When", "width": 180}, {"title": "Who", "width": 160},
                                            {"title": "Prize", "width": 240, "stretch": True},
                                            {"title": "Kind", "width": 160}],
                                empty="No treasures yet. They show up after a match's replay is read.")

        def paint(c):
            c.text(self.px(24), self.px(20), "Every find", font=self.f.h2)
            top = self.px(56)
            T.well(c, self.px(12), top, c.W - self.px(12), c.H - self.px(12))
            c.put(self.tr_table, self.px(18), top + self.px(6), width=c.W - self.px(36), height=c.H - top - self.px(24),
                  tint=False)

        self.tr_list = self._card(s, paint, 5, 0, colspan=3, height=340)
        tk.Frame(s, bg=T.BASE, height=self.px(10)).grid(row=6, column=0)
        return sp

    def _fill_treasures(self, d):
        found = M.treasure_rows(d["mine"], d["peers"])
        sig = (repr(found), time.strftime("%Y%m%d"))
        if sig == getattr(self, "_tr_sig", None):
            return
        self._tr_sig = sig
        self._tr = {"found": found, "weeks": M.collection(found), "now": d["now"]}
        rows = [{"values": [M.fmt_when(r["when"], d["now"]), r["who"], "PIRATE SHOP ACCESS" if r["pirate"] else r["name"],
                            r["kind"]],
                 "sort": [r["when"], r["who"], r["name"], r["kind"]],
                 "colors": {0: T.MUTED, 2: T.GOLD if (r["pirate"] or r["rarity"] >= 3) else T.TEXT, 3: T.DIM},
                 "bold": (2,) if r["pirate"] or r["rarity"] >= 3 else (), "accent": T.GOLD if r["pirate"] else None, "data": r}
                for r in found]
        self.tr_table.set_rows(rows)
        for c in [self.tr_hero, *self.tr_tiles, *self.tr_weeks, self.tr_list]:
            c.repaint()

    def _paint_pirate(self, c):
        f, p = self.f, self.px
        tr = self._tr
        if tr is None:
            return
        pirates = [r for r in tr["found"] if r["pirate"]]
        if pirates:
            glow, m = T.glow_image(int(c.W * 0.45), p(50), (255, 170, 60), 110, p(28))
            c.image(p(60) - m, c.H / 2 - p(25) - m, glow)
            T.icon_circle(c, p(26), (c.H - p(56)) / 2, "" if f.icons else "★", d=p(56), active=True)
            c.text(p(100), c.H / 2 - p(14), "PIRATE SHOP ACCESS", font=f.huge, fill=T.GOLD, anchor="w")
            first = pirates[-1]
            who = ", ".join(sorted({r["who"] for r in pirates}))
            c.text(p(100), c.H / 2 + p(22), f"Won by {who} · first on {M.fmt_when(first['when'], tr['now'])}", font=f.body,
                   fill=T.TEXT, anchor="w")
            T.badge(c, c.W - p(26), c.H / 2, "You have it!" if any(r["me"] for r in pirates) else "Your team has it",
                    "amber", anchor="e")
        else:
            T.icon_circle(c, p(26), (c.H - p(56)) / 2, "" if f.icons else "★", d=p(56))
            c.text(p(100), c.H / 2 - p(14), "Pirate Shop Access", font=f.big, fill=T.MUTED, anchor="w")
            c.text(p(100), c.H / 2 + p(18), "Not found yet. It's one of the rarest prizes: it shows up here, in gold, "
                   "the moment anyone on your team wins it.", font=f.body, fill=T.DIM, anchor="w", width=c.W - p(140))

    def _paint_tr_tile(self, c, i):
        f, p = self.f, self.px
        tr = self._tr
        if tr is None:
            return
        found = tr["found"]
        mine = [r for r in found if r["me"]]
        team = [r for r in found if not r["me"]]
        best = max(found, key=lambda r: r["rarity"], default=None)
        label, val, note = [("You found", str(len(mine)), "from your replays"),
                            ("Team found", str(len(team)), "shared by teammates' trackers"),
                            ("Rarest", best["name"] if best else "-",
                             (best["kind"] + " · " + best["who"]) if best else "nothing yet")][i]
        c.text(p(20), p(16), label.upper(), font=f.cap, fill=T.DIM)
        c.text(p(20), p(34), val, font=f.huge if i < 2 else f.mid,
               fill=T.ACCENT_SOFT if i == 0 else (T.GOLD if i == 2 and best and best["rarity"] >= 3 else T.TEXT))
        c.text(p(20), c.H - p(20), note, font=f.small, fill=T.DIM, anchor="w")

    def _paint_week(self, c, i):
        f, p = self.f, self.px
        tr = self._tr
        if tr is None:
            return
        wk = tr["weeks"][i]
        pirate = wk["week"] == "Pirate Shop"
        c.text(p(22), p(18), wk["week"], font=f.h2, fill=T.GOLD if pirate else T.TEXT)
        c.text(p(30) + f.h2.measure(wk["week"]), p(21), wk["title"], font=f.small, fill=T.DIM)
        y = p(54)
        for it in wk["items"]:
            on = bool(it["found_by"])
            dot, _ = T.circle_image(p(20), T.PRIMARY[0] if on else T.hl(26),
                                    T.PRIMARY[1] if on else T.hl(16), outline=None if on else T.hl(40))
            c.image(p(22), y, dot)
            if on:
                c.text(p(32), y + p(10), "✓", font=f.cap, fill=T.ON_ACCENT, anchor="center")
            c.text(p(52), y + p(10), it["name"], font=f.bold if on else f.body, fill=T.TEXT if on else T.MUTED, anchor="w")
            c.text(c.W - p(22), y + p(10), ", ".join(it["found_by"]) if on else it["kind"], font=f.small,
                   fill=T.ACCENT_SOFT if on else T.DIM, anchor="e")
            y += p(32)

    # ==========================================================================================
    # Map: the map window's view built into the page (all its tools), and "where to land"
    # ==========================================================================================
    def _build_map(self):
        s = T.Surface(self.stack)
        s.columnconfigure(0, weight=1)
        s.rowconfigure(0, weight=1)  # (no page title here: the map gets the room)
        self.map_tab = "map"
        cfg = self.app.cfg
        self.show_list = bool(cfg.get("app_map_list", True))        # the games / events / key panel
        self.show_players = bool(cfg.get("app_map_players", True))  # the players panel over the map
        self.show_lanes = bool(cfg.get("app_map_lanes", True))      # the detailed (per player) timeline
        self.map_card = self._card(s, self._paint_map_card, 0, 0, height=600, pad=self.px(8))
        self.plan_on = False
        self.plan_tools = T.Tabs(self.map_card, [("Landing", "landing"), ("No-go zone", "avoid"),
                                                 ("Checkpoint", "checkpoint")], "landing", self._plan_tool,
                                 self.f, h=self.px(34), pad=self.px(12), radius=self.px(10))
        self.plan_slider = T.Slider(self.map_card, 10, 80, None, width=self.px(150))
        self.plan_slider.command = self._plan_count_changed
        self.view_tabs = T.Tabs(self.map_card, [("Game", "match"), ("Heatmap", "heat"), ("Plan route", "plan"),
                                                ("Where to land", "drops")],
                                "match", self._pick_view, self.f, h=self.px(36), pad=self.px(16), radius=self.px(11))
        self.map_tabs = T.Tabs(self.map_card, [("Map", "map"), ("Where to land", "drops")], "map", self._show_map_tab,
                               self.f, h=self.px(36), pad=self.px(16), radius=self.px(11))
        self.map_holder = tk.Frame(self.map_card, bg=T.WELL)
        self.drops_holder = tk.Frame(self.map_card, bg=T.WELL)
        self.mode_tabs = T.Tabs(self.map_card, [("Match", "match"), ("Heatmap", "heat")], "match", self._set_map_mode,
                                self.f, h=self.px(34), pad=self.px(16), radius=self.px(11))
        self.map_saved = Ref("", lambda: self.map_card.repaint())
        self.player_filter = None  # show only games with this player in the side list
        self.embedded_map = None
        self.map_simple = True
        self.drops_info = Ref("", self.map_card.repaint)
        self.map_note = Ref("", self.map_card.repaint)
        self.drops_table = T.Table(self.drops_holder, self.f, [
            {"title": "Landed at", "width": 220, "stretch": True}, {"title": "Games", "width": 70, "anchor": "e"},
            {"title": "Avg chests", "width": 100, "anchor": "e"}, {"title": "Avg elims", "width": 90, "anchor": "e"},
            {"title": "Avg time alive", "width": 120, "anchor": "e"}, {"title": "Best game", "width": 100, "anchor": "e"}],
            on_select=lambda r: None, empty="No landings yet: they show up once the map has read your matches")
        self.drops_table.pack(fill="both", expand=True)
        self._build_side()
        self.info = None  # the players panel, made with the map (it floats over it)
        self._ensure_map()
        return s

    # ---- the side panel next to the map: games (pick one, or tick several to overlay) and events
    SIDE_W = 340

    def _build_side(self):
        f, p = self.f, self.px
        sd = self.side = tk.Frame(self.map_card, bg=T.WELL)
        head = tk.Frame(sd, bg=T.WELL)
        head.pack(side="top", fill="x", padx=p(10), pady=(p(10), p(6)))
        self.side_tabs = T.Tabs(head, [("Games", "games"), ("Events", "events"), ("Key", "key")], "games",
                                self._show_side_tab, f, bg=T.WELL, h=p(34), pad=p(16), radius=p(10))
        self.side_tabs.pack(side="left")
        self.side_filter = T.Button(sd, "Everyone  ▾", self._player_filter_menu, f, bg=T.WELL, h=p(30),
                                    font=f.small_bold)
        self.side_hint = tk.Label(sd, text="", bg=T.WELL, fg=T.MUTED, font=f.small, anchor="w", justify="left",
                                  wraplength=p(self.SIDE_W - 30))
        self.side_hint.pack(side="top", fill="x", padx=p(14), pady=(0, p(6)))
        self.side_foot = tk.Frame(sd, bg=T.PANEL)
        self.side_foot_label = tk.Label(self.side_foot, text="", bg=T.PANEL, fg=T.TEXT, font=f.small_bold)
        self.side_foot_label.pack(side="left", padx=p(12), pady=p(8))
        self.side_clear = T.Button(self.side_foot, "Clear", self._overlay_clear, f, bg=T.PANEL, h=p(30))
        self.side_clear.pack(side="right", padx=p(10), pady=p(8))
        self.games_list = T.ListView(sd, f, on_click=self._game_clicked, on_check=self._game_checked, tiles=True,
                                     empty="No games on the map yet. They show up after each match.")
        self.key_view = KeyView(sd, f, self)
        self.events_list = T.ListView(sd, f, on_click=self._event_clicked, compact=True,
                                      empty="Pick a game to see what happened in it.")
        self.side_tab = "games"
        self.map_sel = None       # the one game shown (None = live / latest)
        self.map_overlay = []     # games ticked to draw together
        self._side_rows_key = None
        self._show_side_tab("games")

    def _show_side_tab(self, tab):
        self.side_tab = tab
        self.side_tabs.set(tab)
        for w in (self.games_list, self.events_list, self.key_view):
            w.pack_forget()
        self.side_foot.pack_forget()
        if tab == "key":
            self.side_hint.configure(text="What the marks on the map mean.")
            self.key_view.pack(side="top", fill="both", expand=True, padx=p_(self, 4), pady=(0, p_(self, 6)))
            self.key_view.draw()
            return
        self.side_filter.pack_forget()
        if tab == "games":
            self.side_hint.configure(text="Click a game (or View) to see it on the map. Tick Overlay on a few to "
                                          "draw them together, each in its own colour.")
            self.side_filter.pack(side="top", anchor="w", padx=p_(self, 12), pady=(0, p_(self, 6)),
                                  before=self.side_hint)
            if self.map_overlay:
                self.side_foot.pack(side="bottom", fill="x")
            self.games_list.pack(side="top", fill="both", expand=True, padx=p_(self, 4), pady=(0, p_(self, 6)))
        else:
            self.side_hint.configure(text="Click an event to jump the replay to that moment.")
            self.events_list.pack(side="top", fill="both", expand=True, padx=p_(self, 4), pady=(0, p_(self, 6)))
            self._fill_events()

    def _player_filter_menu(self):
        items = [("Everyone (all your games)", lambda: self._set_player_filter(None))]
        for name in self._map_players()[:25]:
            items.append((f"Games with {name}", lambda n=name: self._set_player_filter(n)))
        self._menu(items)

    def _set_player_filter(self, name):
        self.player_filter = name
        self.side_filter.configure_text((f"With {name}" if name else "Everyone") + "  ▾")
        if self._snap:
            self._fill_side(self._snap)

    def _fill_side(self, d):
        games = [r for r in d["rows"] if r.get("on_map")]
        if self.player_filter:
            pf = self.player_filter
            games = [r for r in games if any(pl.get("name") == pf for pl in
                                             (d["maps"].get(r["replay"]) or {}).get("map", {}).get("players", []))]
        rows = [{"title": "Live / latest game", "sub": "You live; console teammates after the match", "checked": None,
                 "data": "__live__",
                 "right": ""},
                {"header": True, "title": "Overlay",
                 "right": f"Games with {self.player_filter} ({len(games)})" if self.player_filter else
                 f"Your games ({len(games)})"}]
        for r in games:
            ms = r.get("map") or {}
            squad = ", ".join(q["name"] for q in r["squad"][:2]) or "solo"
            f_ = r["replay"]
            rows.append({"title": M.fmt_when(r["started"], d["now"]),
                         "sub": " · ".join(x for x in (ms.get("landed_at"), squad) if x),
                         "right": f"{r['chests']}", "checked": f_ in self.map_overlay, "data": f_,
                         "check_color": self._overlay_color(f_)})
        sel = next((i for i, row in enumerate(rows) if row.get("data") == (self.map_sel or "__live__")), None)
        if self.map_overlay:
            sel = None
        self.games_list.set_rows(rows, selected=sel)
        self._update_side_foot()

    def _overlay_color(self, file):
        """The colour a ticked game is drawn in on the map (the map window's compare colours, in tick order)."""
        if file not in self.map_overlay or len(self.map_overlay) < 2:
            return None
        cols = compare_colors()
        return cols[self.map_overlay.index(file) % len(cols)]

    def _update_side_foot(self):
        n = len(self.map_overlay)
        if n:
            self.side_foot_label.configure(text=f"Overlaying {plural(n, 'game')}" if n > 1 else "1 game ticked: tick another to overlay")
            if self.side_tab == "games" and not self.side_foot.winfo_ismapped():
                self.side_foot.pack(side="bottom", fill="x", before=self.games_list)
        else:
            self.side_foot.pack_forget()

    def _game_clicked(self, data):
        mw = self._ensure_map()
        self.map_overlay = []
        if data == "__live__" or data is None:
            self.map_sel = None
            if mw is not None:
                mw.show_view("match")
        else:
            self.map_sel = data
            if mw is not None:
                try:
                    mw.show_match(data, player=self.player_filter) if self.player_filter else mw.show_match(data)
                except TypeError:  # an older map window
                    mw.show_match(data)
        if self._snap:
            self._fill_side(self._snap)
        self._fill_events()

    def _game_checked(self, data, checked):
        if data in (None, "__live__"):
            return
        if checked and data not in self.map_overlay:
            self.map_overlay.append(data)
        elif not checked and data in self.map_overlay:
            self.map_overlay.remove(data)
        mw = self._ensure_map()
        if mw is not None:
            if len(self.map_overlay) >= 2:
                mw.compare(self.map_overlay[:12])
            elif len(self.map_overlay) == 1:
                self.map_sel = self.map_overlay[0]
                mw.show_match(self.map_sel)
            elif self.map_sel:
                mw.show_match(self.map_sel)
            else:
                mw.show_view("match")
        self._update_side_foot()
        self._fill_events()
        gl = self.games_list
        for i, row in enumerate(gl.rows):  # just the tiles whose tick or colour changed
            f_ = row.get("data")
            if row.get("checked") is None or row.get("header"):
                continue
            want = (f_ in self.map_overlay, self._overlay_color(f_))
            if (bool(row.get("checked")), row.get("check_color")) != want:
                gl.update_row(i, checked=want[0], check_color=want[1])
        if self.side_tab == "key":
            self.key_view.draw()

    def _overlay_clear(self):
        self.map_overlay = []
        self._game_clicked(self.map_sel or "__live__")

    def _fill_events(self):
        d = self._snap
        if d is None:
            return
        if len(self.map_overlay) >= 2:
            self.events_list.empty = "Events show for one game at a time. Click a game in the Games tab to see its events."
            self.events_list.set_rows([])
            return
        recs = d["maps"]
        rec = recs.get(self.map_sel) if self.map_sel else max(recs.values(), key=lambda r: r.get("start") or 0,
                                                              default=None)
        if rec is None:
            self.events_list.empty = "Pick a game to see what happened in it."
            self.events_list.set_rows([])
            return
        try:
            import map_tracker

            events = map_tracker.match_events(rec)
            me = next((pl for pl in rec["map"].get("players", []) if M.is_me(pl.get("name"), pl.get("recorder"), d["names"])),
                      None)
            st = map_tracker.player_stats(rec, me, self._pois()) if me else None
        except Exception:
            log.exception("events")
            events, me, st = [], None, None
        my_name = (me or {}).get("name")
        rows = [{"header": True, "title": time.strftime("%b %d, %I:%M %p", time.localtime(rec.get("start") or 0))}]
        if st:
            first = "-" if st.get("first_chest") is None else f"{st['first_chest']:.0f} s"
            for line in (f"Landed at {st['landed_at']}", f"Alive {M.fmt_length(st['alive'])} · {st['kills']} eliminations",
                         f"{st['opened']} opened, {st['broken']} broken · {st['per_min']:.1f} a minute",
                         f"First chest {first} after landing",
                         f"{st['walk_m'] / 1000:.1f} km on foot · {st['fast_m'] / 1000:.1f} km fast"):
                rows.append({"title": line, "title_color": T.TEXT, "data": None})
        rows.append({"header": True, "title": f"Events ({len(events)})"})
        colors = {"chest": T.ACCENT_SOFT, "broken": T.ACCENT_HI, "kill": T.RED, "landing": T.TEXT, "out": T.DIM,
                  "storm": "#d7b0ff"}
        for e in events:
            text = e["text"]
            if my_name and e.get("who") == my_name:
                text = "You" + text[len(my_name):] if text.startswith(my_name) else text
            rows.append({"title": f"{M.fmt_length(e['t'])}   {text}", "title_color": colors.get(e["kind"], T.TEXT),
                         "data": ("t", e["t"])})
        self.events_list.set_rows(rows)

    def _event_clicked(self, data):
        mw = self.embedded_map
        if mw is not None and isinstance(data, tuple) and data[0] == "t" and hasattr(mw, "jump"):
            mw.jump(data[1])

    INFO_W = 250  # the "this game" panel left of the map

    def _map_mode(self) -> str:
        if getattr(self, "plan_on", False):
            return "plan"
        mw = self.embedded_map
        try:
            return "heat" if mw is not None and mw.view.get() == "heat" else "match"
        except Exception:
            return "match"

    def _game_label(self):
        """What's on the map, for the game picker: "Latest game", "Yesterday 10:25 PM · 25 chests", "3 games"."""
        mw = self.embedded_map
        try:
            if mw.compare_files:
                return f"{len(mw.compare_files)} games overlaid", True
            rec = mw._shown_rec
            if rec is None or mw.match_box.get() in ("", "Live / latest match"):
                return "Latest game", False
            d = self._snap or {}
            row = next((r for r in d.get("rows", []) if r.get("replay") == rec.get("file")), None)
            when = M.fmt_when(rec.get("start") or 0, time.time())
            n = (row["chests"] or row.get("squad_total")) if row else None  # (someone else's game: the squad's)
            return (f"{when}  ·  {n} chests" if n else when), True
        except Exception:
            return "Latest game", False

    def _game_menu(self):
        d = self._snap or self.snapshot()
        games = [r for r in d["rows"] if r.get("on_map")][:25]
        items = [("Latest game (follows you live)", lambda: self._game_clicked("__live__"))]
        for r in games:
            where = (r.get("map") or {}).get("landed_at") or ""
            items.append((f"{M.fmt_when(r['started'], d['now'])}  ·  {r['chests']} chests" + (f"  ·  {where}" if where else ""),
                          lambda f_=r["replay"]: self._game_clicked(f_)))
        items.append(("Overlay several games…  (tick them in the games list)", self._open_overlay_list))
        self._menu(items)

    def _open_overlay_list(self):
        if not self.show_list:
            self._toggle_view("show_list")
        self._show_side_tab("games")

    def _toggle_chests(self):
        """Shows / hides the known chest locations on the map (remembered)."""
        mw = self.embedded_map
        if mw is None or not hasattr(mw, "show_spots"):
            return
        mw.show_spots.set(not mw.show_spots.get())
        self.app.cfg["map_spots"] = bool(mw.show_spots.get())
        mw._changed()
        self.map_card.repaint()

    def _options_menu(self):
        mw = self.embedded_map
        items = [(("✓  " if self.show_players else "     ") + "Players panel", lambda: self._toggle_view("show_players")),
                 (("✓  " if self.show_lanes else "     ") + "Detailed timeline (a row per player)",
                  lambda: self._toggle_view("show_lanes")),
                 (("✓  " if self.show_list else "     ") + "Games list", lambda: self._toggle_view("show_list"))]
        if mw is not None and hasattr(mw, "show_panel"):
            def flip():
                mw.show_panel.set(not mw.show_panel.get())
                mw._changed()

            items.append((("✓  " if mw.show_panel.get() else "     ") + "The map's own stats panel", flip))
        if mw is not None and hasattr(mw, "open_browser"):
            items.append(("     All matches and drop spots (list)…", mw.open_browser))
        if mw is not None and hasattr(mw, "set_simple"):
            items.append((("✓  " if not self.map_simple else "     ") + "Every map control (advanced)", self._toggle_simple))
        items.append(("     Pop out the map for a second screen  ↗", self._pop_out))
        self._menu(items)

    def _paint_map_card(self, c):
        f, p = self.f, self.px
        mw = self.embedded_map
        top = p(64)
        holder, other = (self.map_holder, self.drops_holder) if self.map_tab == "map" else (self.drops_holder, self.map_holder)
        bar_h = 0
        if self.map_tab == "map" and getattr(self, "playbar", None) is not None and self.playbar._shown:
            bar_h = self.playbar.want_h
        view = "drops" if self.map_tab == "drops" else self._map_mode()
        self.view_tabs.set(view)
        for wdg in (self.plan_slider, self.plan_tools):
            if view != "plan" and wdg in c._puts:
                c.itemconfigure(c._puts[wdg][0], state="hidden")
        c.put(self.view_tabs, p(18), p(16))
        x = p(18) + int(self.view_tabs.cget("width")) + p(16)
        icons = bool(f.icons)
        # right side first (so the left side knows how much room it has)
        rx = c.W - p(18)
        if self.map_tab == "map" and mw is not None:
            right = [("Options  ▾", self._options_menu, "ghost", None),
                     ("Save image", self._save_map_image, "ghost", "\uE74E"),
                     ("Whole island", lambda: mw.whole_island(), "ghost", "\uE740")]
            narrow = c.W < p(1150)
            for label, cmd, kind, glyph in right:
                text = "" if (narrow and glyph) else label
                x0, *_ = T.pill(c, rx, p(17), text or " ", cmd, kind=kind, anchor="ne", h=p(34), pad=p(12 if text else 9),
                                font=f.body, icon=glyph if icons else None, tag=f"rt_{label[:4]}")
                rx = x0 - p(8)
            if hasattr(mw, "show_spots"):  # the chest locations: always one click away, lit up when shown
                on = bool(mw.show_spots.get())
                x0, *_ = T.pill(c, rx - p(6), p(17), "Chests  \u2713" if on else "Chests", self._toggle_chests,
                                kind="primary" if on else "ghost", anchor="ne", h=p(34), pad=p(14), font=f.bold,
                                icon="\uE7BF" if icons else None, tag="rt_chests")
                rx = x0 - p(14)
            if self.map_saved.text:
                c.text(rx - p(6), p(34), self.map_saved.text, font=f.small, fill=T.GREEN, anchor="e")
        if self.map_tab == "map":
            if mw is not None and hasattr(mw, "set_simple"):
                if view == "match":
                    label, old = self._game_label()
                    x0_, _y, x1, _ = T.pill(c, x, p(17), label + "  ▾", self._game_menu, h=p(34), pad=p(14), font=f.bold,
                                           icon="\uE787" if icons else None, kind="soft")
                    x = x1 + p(8)
                    if old:  # back to the game being played / the latest one
                        T.pill(c, x, p(17), "Latest", lambda: self._game_clicked("__live__"), h=p(34), pad=p(12),
                               font=f.body, icon="\uE72B" if icons else None)
                elif view == "plan":  # what a click does; how many chests; route options
                    if hasattr(mw, "set_plan_tool"):
                        try:
                            self.plan_tools.set(mw.plan_tool.get())
                        except Exception:
                            pass
                        c.put(self.plan_tools, x, p(17))
                        c.itemconfigure(c._puts[self.plan_tools][0], state="normal")
                        x += int(self.plan_tools.cget("width")) + p(14)
                    if x + p(260) < rx:
                        c.text(x, p(34), "Chests", font=f.small, fill=T.MUTED, anchor="w")
                        c.put(self.plan_slider, x + p(50), p(19))
                        c.itemconfigure(c._puts[self.plan_slider][0], state="normal")
                        c.text(x + p(50) + int(self.plan_slider.cget("width")) + p(8), p(34),
                               f"{int(round(self.plan_slider.v))}", font=f.bold, fill=T.ACCENT_SOFT, anchor="w")
                        x += p(50) + int(self.plan_slider.cget("width")) + p(40)
                    T.pill(c, x, p(17), "Route  ▾", self._plan_menu, h=p(34), pad=p(12), font=f.body)
                else:  # heatmap filters, each a clear dropdown
                    for label, cmd in [(self._heat_label() + "  ▾", self._heat_menu),
                                       (self._who_label() + "  ▾", self._who_menu),
                                       (self._period_label() + "  ▾", self._period_menu)]:
                        _x0, _y0, x1, _ = T.pill(c, x, p(17), label, cmd, h=p(34), pad=p(14), font=f.body)
                        x = x1 + p(8)
            else:
                c.text(x, p(34), self.map_note.text, font=f.small, fill=T.DIM, anchor="w")
        else:
            for label, cmd, kind in [("Show routes from here", self._drop_routes, "primary"),
                                     ("Suggested loot path", self._drop_loot, "ghost"),
                                     ("Open best game", self._drop_best, "ghost")]:
                _x0, _y0, x1, _ = T.pill(c, x, p(17), label, cmd, kind=kind, h=p(34), pad=p(14), font=f.body)
                x = x1 + p(8)
            c.text(c.W - p(22), p(34), self.drops_info.text, font=f.small,
                   fill=T.ACCENT_SOFT if self.drops_info.fg == "warn" else T.DIM, anchor="e")
        T.well(c, p(12), top, c.W - p(12), c.H - p(12))
        side_w = p(self.SIDE_W) if (self.map_tab == "map" and self.show_list) else 0
        body_h = c.H - top - p(20) - (bar_h + p(6) if bar_h else 0)  # the map and the side panel; the bar under both
        if not side_w and self.side in c._puts:
            c.itemconfigure(c._puts[self.side][0], state="hidden")
        if side_w:
            c.put(self.side, c.W - p(16) - side_w, top + p(4), width=side_w, height=body_h, tint=False)
            c.itemconfigure(c._puts[self.side][0], state="normal")
            c.line(c.W - p(20) - side_w, top + p(14), c.W - p(20) - side_w, top + body_h, fill=T.WELL_LINE)
        bar = getattr(self, "playbar", None)
        if bar is not None:
            if bar_h:
                c.put(bar, p(16), top + p(4) + body_h + p(6), width=c.W - p(32), height=bar_h, tint=False)
                c.itemconfigure(c._puts[bar][0], state="normal")
                c.line(p(24), top + p(4) + body_h + p(3), c.W - p(24), top + p(4) + body_h + p(3), fill=T.WELL_LINE)
            elif bar in c._puts:
                c.itemconfigure(c._puts[bar][0], state="hidden")
        left = p(16)
        c.put(holder, left, top + p(4), width=c.W - left - p(16) - (side_w + p(8) if side_w else 0),
              height=body_h, tint=False)
        c.itemconfigure(c._puts[holder][0], state="normal")
        if other in c._puts:
            c.itemconfigure(c._puts[other][0], state="hidden")

    # ---- heatmap filters ---------------------------------------------------------------------------
    def _heat_label(self):
        mw = self.embedded_map
        try:
            return {v: l for l, v in HEAT_MENU}.get(mw._kind_key(), mw.kind.get())
        except Exception:
            return "Heatmap"

    def _who_label(self):
        try:
            who = self.embedded_map.who.get()
            return {"Me": "Just me", "Squad": "Whole squad"}.get(who, who)
        except Exception:
            return "Just me"

    def _period_label(self):
        try:
            return self.embedded_map.period.get()
        except Exception:
            return "All time"

    def _set_map_mode(self, mode):
        if mode == "heat":
            self._open_map(True, kind=getattr(self.embedded_map, "_kind_key", lambda: "chests")() if self.embedded_map else "chests")
        else:
            self._game_clicked(self.map_sel or "__live__")
        self.map_card.repaint()

    def _who_menu(self):
        items = [("Just me", lambda: self._set_who("Me")), ("Whole squad", lambda: self._set_who("Squad"))]
        for name in self._map_players()[:15]:
            items.append((name, lambda n=name: self._set_who(n)))
        self._menu(items)

    def _period_menu(self):
        mw = self.embedded_map

        def pick(v):
            mw.period.set(v)
            mw._picked()
            self.map_card.repaint()

        self._menu([(v, lambda v=v: pick(v)) for v in ("All time", "Last 30 days", "Last 7 days", "Today")])

    def _save_map_image(self):
        mw = self.embedded_map
        if mw is None or not hasattr(mw, "save_image"):
            return
        try:
            mw.save_image()
            note = getattr(mw, "_saved_note", None)
            name = os.path.basename(note[1]) if note else "the picture"
            self.map_saved.configure(text=f"Saved to Pictures \\ Chest Tracker  ({name})")
            self.win.after(6000, lambda: self.map_saved.configure(text=""))
        except Exception:
            log.exception("saving the map picture")
            self.map_saved.configure(text="Couldn't save the picture (see tracker.log)")

    def _map_players(self) -> list[str]:
        """Your squadmates seen in your games on the map (tracker and console teammates, never random
        fills), most games first."""
        d = self._snap or {}
        names = d.get("names") or set()
        mw = self.embedded_map
        squad = getattr(mw, "squad_member", None) if mw is not None else None
        counts: dict = {}
        for rec in (d.get("maps") or {}).values():
            for pl in rec.get("map", {}).get("players", []):
                n = pl.get("name")
                if n and not M.is_me(n, pl.get("recorder"), names) and (squad is None or squad(n)):
                    counts[n] = counts.get(n, 0) + 1
        return sorted(counts, key=lambda n: (-counts[n], n.lower()))

    def _map_more_menu(self):
        mw = self.embedded_map
        if mw is None:
            return
        items = []
        if hasattr(mw, "show_panel"):
            def flip():
                mw.show_panel.set(not mw.show_panel.get())
                mw._changed()

            items.append(("Hide the map's own stats panel" if mw.show_panel.get() else "Show the map's own stats panel",
                          flip))
        if hasattr(mw, "save_image"):
            items.append(("Save the map as a picture", mw.save_image))
        if hasattr(mw, "open_browser"):
            items.append(("All matches and drop spots (list)...", mw.open_browser))
        if hasattr(mw, "set_simple"):
            items.append(("Show every map control (advanced)" if self.map_simple else "Hide the advanced map controls",
                          self._toggle_simple))
        for label, attr in (("the games list", "show_list"), ("the detailed timeline", "show_lanes"),
                            ("the players panel", "show_players")):
            items.append((("Hide " if getattr(self, attr) else "Show ") + label, lambda a=attr: self._toggle_view(a)))
        items.append(("Pop out the map for a second screen  ↗", self._pop_out))
        self._menu(items or [("Nothing more here", lambda: None)])

    def _toggle_view(self, attr):
        """Shows / hides the games list, the detailed timeline or the players panel (remembered)."""
        setattr(self, attr, not getattr(self, attr))
        self.app.cfg[{"show_list": "app_map_list", "show_lanes": "app_map_lanes",
                      "show_players": "app_map_players"}[attr]] = getattr(self, attr)
        if attr == "show_lanes" and getattr(self, "playbar", None) is not None:
            self.playbar._lanes_key = None
            self.playbar.refresh(force=True)
        if attr == "show_players" and self.info is not None:
            self.info.refresh(force=True)
        self.map_card.repaint()

    def _set_who(self, who):
        mw = self.embedded_map
        if mw is not None and hasattr(mw, "set_who"):
            mw.set_who(who)
        self.map_card.repaint()
        if self.info is not None:
            self.info.refresh(force=True)

    def _toggle_simple(self):
        self.map_simple = not self.map_simple
        mw = self.embedded_map
        if mw is not None and hasattr(mw, "set_simple"):
            try:
                mw.set_simple(self.map_simple)
            except Exception:
                log.exception("map simple mode")
        self.map_card.repaint()

    def _menu(self, items):
        m = tk.Menu(self.win, tearoff=0, bg=T.PANEL2, fg=T.TEXT, activebackground=T.ROW_SELECT, activeforeground=T.TEXT,
                    font=self.f.body, bd=0, relief="flat")
        for label, cmd in items:
            m.add_command(label=label, command=cmd)
        x, y = self.win.winfo_pointerxy()
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _pick_game_menu(self):
        d = self._snap or self.snapshot()
        games = [r for r in d["rows"] if r.get("on_map")][:20]
        items = [(f"{M.fmt_when(r['started'], d['now'])}  ·  {r['chests']} chests"
                  + (f"  ·  {r['map']['landed_at']}" if r.get("map") else ""),
                  lambda r=r: self._open_map(False, r["replay"])) for r in games]
        self._menu(items or [("No games on the map yet", lambda: None)])

    def _heat_menu(self):
        self._menu([(label, lambda k=k: self._open_map(True, kind=k)) for label, k in HEAT_MENU])

    def _ensure_map(self):
        """Builds the map view into the page once the map data is there."""
        if getattr(self, "embedded_map", None) is not None or getattr(self, "map_holder", None) is None:
            return getattr(self, "embedded_map", None)
        mgr = getattr(self.app, "map", None)
        if mgr is None:
            if not self.map_holder.winfo_children():
                tk.Label(self.map_holder, text="The map isn't available on this PC (see tracker.log).", bg=T.WELL,
                         fg=T.DIM, font=self.f.body).pack(expand=True)
            return None
        for ch in self.map_holder.winfo_children():
            ch.destroy()
        try:
            from map_window import MapWindow

            mw = MapWindow(self.app, mgr, lambda: self.ct.save_json(self.ct.CONFIG_PATH, self.app.cfg),
                           parent=self.map_holder, on_show=self._map_shown)
            mw.win.pack(fill="both", expand=True)
            self.embedded_map = mw
            if hasattr(mw, "set_simple"):
                mw.set_simple(self.map_simple)
            if hasattr(mw, "set_key"):  # the app's Key tab explains the marks instead
                mw.set_key(False)
            if hasattr(mw, "set_colors"):  # the frame around the map in the theme's colours
                mw.set_colors(bg=T.BASE, panel=T.WELL, text=T.TEXT, select=T.ROW_SELECT)
            if getattr(self, "playbar", None) is None:
                self.playbar = PlaybackBar(self.map_card, self)  # the app's own replay controls (full width)
            self.info = GameInfo(self.map_holder, self)  # the players panel, over the map's top-left, see-through
            if hasattr(mw, "set_timeline"):
                mw.set_timeline(False)
            elif hasattr(mw, "timeline"):
                # (until the map window has set_timeline: its own timeline row can't be shown in the app)
                mw.timeline.pack_forget()
                mw.timeline.pack = mw.timeline.pack_configure = lambda *a, **k: None
            if hasattr(mw, "show_panel"):  # the app's side panel shows the games and events instead
                mw.show_panel.set(False)
                mw._changed()
            # Space plays / pauses the replay (a frame only gets keys when focused, so the window listens)
            self.win.bind("<space>", self._space, add="+")
        except Exception:
            log.exception("map view")
            tk.Label(self.map_holder, text="The map couldn't be shown (see tracker.log).", bg=T.WELL, fg=T.DIM,
                     font=self.f.body).pack(expand=True)
        return self.embedded_map

    def _space(self, e):
        mw = self.embedded_map
        if mw is None or self.page != "map" or self.map_tab != "map":
            return None
        if e.widget.winfo_class() in ("Entry", "TEntry", "TCombobox", "Text", "Listbox"):
            return None  # typing / picking: leave Space alone
        mw.toggle_play()
        return "break"

    def _map_shown(self):
        """The map view asks to be seen (a match, a comparison, routes...): bring its page up and lay it
        out now, so it measures its real size (e.g. "whole island" right after)."""
        self.go("map")
        self._show_map_tab("map")
        self.win.update_idletasks()

    def _pick_view(self, v):
        """Game | Heatmap | Plan route | Where to land."""
        self._set_plan(v == "plan")
        if v == "drops":
            self._show_map_tab("drops")
            return
        if self.map_tab != "map":
            self._show_map_tab("map")
        if v == "plan":
            mw = self.embedded_map
            if mw is not None and mw.view.get() == "heat":
                self._set_map_mode("match")
            if mw is not None and hasattr(mw, "whole_island"):
                mw.whole_island()
        elif v != self._map_mode():
            self._set_map_mode(v)
        self.map_card.repaint()

    # ---- the loot planner (the map window plans; these are its controls) -----------------------------
    def _set_plan(self, on):
        mw = self.embedded_map
        if on == self.plan_on:
            return
        self.plan_on = on
        if mw is not None and hasattr(mw, "set_plan_mode"):
            try:
                mw.set_plan_mode(on)
                if on and hasattr(mw, "plan_count"):
                    self.plan_slider.v = float(mw.plan_count.get())
            except Exception:
                log.exception("loot planner")
        if self.info is not None:
            self.info.refresh(force=True)
        if getattr(self, "playbar", None) is not None:
            self.playbar.refresh(force=True)  # (hidden while planning)

    def _plan_count_changed(self, value):
        n = int(round(float(value)))
        mw = self.embedded_map
        if mw is not None and hasattr(mw, "set_plan_count") and (not hasattr(mw, "plan_count") or mw.plan_count.get() != n):
            mw.set_plan_count(n)
            self.app.cfg["map_plan_count"] = n
        self.map_card.repaint()

    def _plan_tool(self, tool):
        mw = self.embedded_map
        if mw is not None and hasattr(mw, "set_plan_tool"):
            mw.set_plan_tool(tool)
        if self.info is not None:
            self.info.refresh(force=True)

    def _plan_menu(self):
        mw = self.embedded_map
        if mw is None:
            return
        items = []
        if hasattr(mw, "avoid_middle"):
            def flip():
                mw.avoid_middle.set(not mw.avoid_middle.get())
                if hasattr(mw, "_marks_changed"):
                    mw._marks_changed()

            items.append((("✓  " if mw.avoid_middle.get() else "     ") + "Stay out of the middle of the island", flip))
        if hasattr(mw, "set_plan_spread"):  # how many chests from one place before moving on
            try:
                import map_window as MWmod

                spreads = list(MWmod.SPREADS)
            except Exception:
                spreads = [("Clear towns (no limit)", None), ("Wide spread (3 per place)", 3)]
            cur = mw.plan_per_place() if hasattr(mw, "plan_per_place") else None
            for label, v in spreads:
                items.append((("✓  " if v == cur else "     ") + label, lambda v=v: mw.set_plan_spread(v)))
        if hasattr(mw, "clear_plan_marks"):
            items.append(("     Remove all no-go zones and checkpoints", mw.clear_plan_marks))
        items.append(("     Clear the route", self._plan_clear))
        self._menu(items)

    def _plan_clear(self):
        mw = self.embedded_map
        if mw is not None:
            mw.set_loot(None)
        if self.info is not None:
            self.info.refresh(force=True)

    def _show_map_tab(self, tab):
        self.map_tab = tab
        self.map_tabs.set(tab)
        if tab == "map":
            if self.embedded_map is not None:
                self.embedded_map._last_key = None  # draw now
        elif self._snap:
            self._fill_drops(self._snap)
        self.map_card.repaint()

    def _pop_out(self):
        try:
            self.app.open_map()  # the map in a window of its own (a second screen during matches)
        except Exception:
            log.exception("map window")

    def _map_view(self):
        """The map view in the page (built on first use), or the map window when the page can't have one."""
        if "map" not in self._built:
            self.go("map")
            self.win.update_idletasks()
        mw = self._ensure_map()
        if mw is None:
            self.app.open_map()
            mw = getattr(self.app, "_map_window", None)
        return mw

    def _open_map(self, heat: bool, match_file: str | None = None, kind: str | None = None):
        """Shows the map: the live / latest match, one past match (match_file), or a heatmap (kind)."""
        try:
            mw = self._map_view()
            if mw is None:
                return
            if hasattr(mw, "show_view"):
                mw.show_view("heat" if heat else "match", kind=kind if heat else None,
                             match_file=None if heat else match_file)
                if heat and hasattr(mw, "whole_island"):
                    mw.whole_island()
                if getattr(self, "map_card", None) is not None and mw is self.embedded_map:
                    self.map_card.repaint()
            elif hasattr(mw, "view"):  # an older map window
                mw.view.set("heat" if heat else "match")
                for fn in ("_layout_bar", "_changed"):
                    if hasattr(mw, fn):
                        getattr(mw, fn)()
        except Exception:
            log.exception("opening the map")

    def _compare(self, files):
        try:
            mw = self._map_view()
            if mw is not None and hasattr(mw, "compare"):
                mw.compare(files)
        except Exception:
            log.exception("comparing matches")

    def _fill_map(self, d):
        mw = self._ensure_map()
        mgr = getattr(self.app, "map", None)
        if mgr is not None:
            backlog = getattr(mgr, "backlog", 0)
            self.map_note.configure(text=f"{len(d['maps'])} matches on the map" + (f", {backlog} more being read" if backlog else "")
                                    + " · wheel zooms, drag moves, double-click follows you")
        if mw is not None and self.map_tab == "map":
            mw._last_key = None  # draw now (it was hidden)
        key = (len(d["maps"]), getattr(mgr, "version", 0), len(d["rows"]), time.strftime("%Y%m%d%H"))
        if key != self._side_rows_key:
            self._side_rows_key = key
            self._fill_side(d)
            if self.side_tab == "events":
                self._fill_events()
        if self.map_tab == "drops":
            self._fill_drops(d)

    def _drops(self, d) -> list[dict]:
        mgr = getattr(self.app, "map", None)
        if mgr is None:
            return []
        key = (len(d["maps"]), getattr(mgr, "version", 0), tuple(sorted(d["names"])))
        if getattr(self, "_drops_key", None) != key:
            try:
                self._drops_cache = M.drop_spots(list(d["maps"].values()), d["names"], self._pois())
            except Exception:
                log.exception("drop spots")
                self._drops_cache = []
            self._drops_key = key
        return self._drops_cache

    def _pois(self):
        mgr = getattr(self.app, "map", None)
        key = str(getattr(mgr, "map_path", None))
        if getattr(self, "_pois_key", None) != key:
            try:
                self._pois_cache = mgr.pois() if mgr is not None else []
            except Exception:
                self._pois_cache = []
            self._pois_key = key
        return self._pois_cache

    def _fill_drops(self, d):
        drops = self._drops(d)
        top = next((x for x in drops if x["games"] >= 3), None)
        rows = []
        for x in drops:
            rows.append({"values": [x["place"], x["games"], f"{x['avg_chests']:.1f}", f"{x['avg_kills']:.1f}",
                                    M.fmt_length(x["avg_alive"]), x["best"]],
                         "sort": [x["place"].lower(), x["games"], x["avg_chests"], x["avg_kills"], x["avg_alive"], x["best"]],
                         "colors": {2: T.ACCENT_SOFT, 3: T.TEXT if x["avg_kills"] else T.DIM, 4: T.MUTED}, "bold": (0, 2),
                         "data": x, "accent": T.ACCENT if x is top else None})
        self.drops_table.set_rows(rows)
        n = sum(x["games"] for x in drops)
        self.drops_info.configure(text=f"{len(drops)} places from {plural(n, 'landing')}. Pick a place, then an action.",
                                  fg="")

    def _picked_drop(self):
        x = self.drops_table.selected_data()
        if x is None:
            self.drops_info.configure(text="Pick a place in the list first.", fg="warn")
        return x

    def _drop_routes(self):
        x, mw = self._picked_drop(), self._ensure_map()
        if x and mw is not None:
            mw._refresh_choices()
            mw.show_routes_from(x["place"])
            self._show_map_tab("map")

    def _drop_best(self):
        x, mw = self._picked_drop(), self._ensure_map()
        if x and x.get("best_file") and mw is not None:
            mw.show_match(x["best_file"])
            self._show_map_tab("map")

    def _drop_loot(self):
        """Plans a loot route from where you usually land at the picked place (the map's planner)."""
        x, mw = self._picked_drop(), self._ensure_map()
        if not x or mw is None:
            return
        try:
            import map_tracker

            pts = [pt for pt in x.get("landings") or [] if pt]
            if not pts:
                return
            start = (sum(px for px, _ in pts) / len(pts), sum(py for _, py in pts) / len(pts))  # where you usually land
            if hasattr(mw, "plan_loot_from"):
                self._pick_view("plan")
                mx, my = map_tracker.world_to_map(*start, mw.m.tf)
                mw.plan_loot_from(mx, my)
            else:  # an older map window
                season = mw._season_key() or mw.m.season()
                spots = mw.m.spots.spots(season)
                path = map_tracker.loot_path(spots, start, rates=map_tracker.spot_hit_rates(mw.m.records(), spots))
                path["place"] = x["place"]
                mw.set_loot(path)
                self._show_map_tab("map")
        except Exception:
            log.exception("loot path")

    def _drop_clear(self):
        mw = self._ensure_map()
        if mw is not None:
            mw.set_loot(None)

    # ==========================================================================================
    # Settings
    # ==========================================================================================
    def _build_settings(self):
        sp, s = self._scroll_page("settings", 2)
        self.settings_panel = make_settings_panel(self.ct)(self, s)
        return sp

    def _fill_settings(self, d):
        pass  # the panel keeps itself up to date (SettingsWindow._refresh)


def make_settings_panel(ct):
    """The settings page: the same fields and save logic as chest_tracker.SettingsWindow (its methods are
    inherited), laid out on glass cards. Only the layout lives here."""

    class SettingsPanel(ct.SettingsWindow):
        def __init__(self, owner: AppWindow, s):  # noqa: C901 (layout)
            self.app = app = owner.app
            self.owner = owner
            f, p = owner.f, owner.px
            cfg = app.cfg
            self._size_pending = None
            self.win = s  # SettingsWindow's methods use self.win for after() / winfo_exists()

            # --- overlay
            self.op = T.Slider(s, 0, 100, None)
            self.size = T.Slider(s, 60, 250, None)
            self.op_text, self.size_text = Ref(""), Ref("")
            for slider, txt, val, cmd in ((self.op, self.op_text, float(cfg.get("bg_opacity", 0.85)) * 100, self._opacity),
                                          (self.size, self.size_text, float(cfg.get("overlay_scale") or 1.0) * 100,
                                           self._size_moved)):
                slider.v = max(slider.lo, min(slider.hi, val))  # start value without saving anything
                txt.text = f"{slider.v:.0f}%"
                slider.command = cmd
            self.lock = tk.BooleanVar(value=bool(cfg.get("lock_in_match", True)))
            self.team_end = tk.BooleanVar(value=bool(cfg.get("show_team_at_end", True)))
            self.lock_sw = T.Switch(s, self.lock, self._lock_changed)
            self.glass = tk.BooleanVar(value=cfg.get("overlay_style", "glass") != "classic")
            self.glass_sw = T.Switch(s, self.glass, self._overlay_style_changed)
            self.full = tk.BooleanVar(value=bool(cfg.get("overlay_always_full", False)))
            self.full_sw = T.Switch(s, self.full, self._overlay_full_changed)
            self.team_end_sw = T.Switch(s, self.team_end, self._team_end_changed)

            def paint_overlay(c):
                c.text(p(24), p(20), "In-game overlay", font=f.h2)
                c.text(p(24), p(44), "The small counter over Fortnite. Drag it to move it; click it to see everyone.",
                       font=f.small, fill=T.DIM, width=c.W - p(48))
                y = p(84)
                for label, slider, txt in (("Background", self.op, self.op_text), ("Size", self.size, self.size_text)):
                    c.text(p(24), y + p(13), label, font=f.bold, anchor="w")
                    c.put(slider, p(140), y, width=max(p(120), c.W - p(230)))
                    c.text(c.W - p(24), y + p(13), txt.text, font=f.body, fill=T.MUTED, anchor="e")
                    y += p(40)
                for sw, label in ((self.glass_sw, "Glass look (like this window). Off = the classic look"),
                                  (self.full_sw, "Always show everyone's chests (keep the full overlay open)"),
                                  (self.lock_sw, "Let clicks go through it during matches"),
                                  (self.team_end_sw, "Show everyone's chests when your squad is out")):
                    c.put(sw, p(24), y + p(2))
                    c.text(p(80), y + p(14), label, font=f.body, anchor="w")
                    y += p(36)
                c.text(p(24), y + p(8), "Keys: F9 shows everyone's chests, F11 opens this window.", font=f.small,
                       fill=T.DIM)

            # --- colour theme
            def paint_theme(c):
                c.text(p(24), p(20), "Colour theme", font=f.h2)
                c.text(p(24), p(44), "Pick the colours for this window. The whole app switches straight away.",
                       font=f.small, fill=T.DIM)
                n = len(T.THEME_ORDER)
                gap = p(14)
                sw = int(min(p(180), (c.W - p(48) - gap * (n - 1)) / n))
                sh = int(sw * 0.6)
                current = app.cfg.get("app_theme") or "arctic"
                for i, key in enumerate(T.THEME_ORDER):
                    x, y = p(24) + i * (sw + gap), p(78)
                    tag = f"theme_{key}"
                    if key == current:
                        ring, _ = T.pill_image(sw + p(8), sh + p(8), T.acc(0.3, 255), T.acc(-0.1, 255), radius=p(17))
                        c.image(x - p(4), y - p(4), ring, tags=(tag,))
                    c.image(x, y, T.theme_swatch(key, sw, sh), tags=(tag,))
                    name = T.THEMES[key]["name"]
                    c.text(x + sw / 2, y + sh + p(14), name + ("  ✓" if key == current else ""), font=f.bold if key == current
                           else f.body, fill=T.TEXT if key == current else T.MUTED, anchor="center", tags=(tag,))
                    c.on_click(tag, lambda k=key: owner.win.after(10, lambda: owner.switch_theme(k)))
                c.fit(p(78) + sh + p(30), p(10))

            owner._card(s, paint_theme, 1, 0, colspan=2, height=200)
            c1 = owner._card(s, paint_overlay, 2, 0, height=372)
            for t in (self.op_text, self.size_text):
                t.on_change = c1.repaint

            # --- counting
            self.live_replay = tk.BooleanVar(value=bool(cfg.get("live_replay", True)))
            self.live_sw = T.Switch(s, self.live_replay, self._live_replay_changed)
            self.record = tk.BooleanVar(value=bool(cfg.get("record_matches", False)))
            self.record_sw = T.Switch(s, self.record, self._record_changed)
            self.record_test = T.Button(s, "Test recording", self._record_test, f, bg=T.PANEL, h=p(30))
            self.record_note = ""

            def paint_counting(c):
                c.text(p(24), p(20), "Counting", font=f.h2)
                c.put(self.live_sw, p(24), p(62))
                c.text(p(80), p(74), "Read the replay as soon as Fortnite unlocks it", font=f.body, anchor="w")
                c.text(p(80), p(94), "Fortnite keeps a match's replay locked until the match ends, so broken chests, "
                       "console teammates and treasures are added right after it.", font=f.small,
                       fill=T.DIM, width=c.W - p(110))
                c.put(self.record_sw, p(24), p(150))
                c.text(p(80), p(162), "Record every match with OBS", font=f.body, anchor="w")
                c.text(p(80), p(182), "OBS must be open. Keeps your last 5 matches, and keeps the whole video "
                       "of any match with a real treasure find (not sprite gizmos) for good.", font=f.small,
                       fill=T.DIM, width=c.W - p(110))
                c.put(self.record_test, p(80), p(228), width=p(140), height=p(30))
                if self.record_note:
                    c.text(p(80), p(270), self.record_note, font=f.small, fill=T.MUTED, width=c.W - p(110))

            self.counting_card = owner._card(s, paint_counting, 2, 1, height=330)

            # --- you
            def box(c, entry, x, y, w):
                T.well(c, x - p(4), y - p(4), x + w + p(4), y + p(36), radius=p(10))
                c.put(entry, x + p(8), y + p(3), width=w - p(16), height=p(26), tint=False)

            def field(c, y, label, entry, note=None, note_ref=None):
                c.text(p(24), y + p(16), label, font=f.bold, anchor="w")
                box(c, entry, p(190), y, max(p(140), c.W - p(214)))
                ny = y + p(42)
                text = note_ref.text if note_ref is not None and note_ref.text else note
                if text:
                    ok = note_ref is not None and bool(note_ref.text) and note_ref.fg == "#2a7a3a"
                    item = c.text(p(190), ny, text, font=f.small, fill=T.GREEN if ok else T.DIM, width=c.W - p(214))
                    bb = c.bbox(item)
                    ny = bb[3] if bb else ny + p(30)
                return ny + p(18)

            self.name = tk.StringVar(value=cfg.get("name", ""))
            self.fn = tk.StringVar(value=", ".join(cfg.get("fortnite_names") or []))
            self.name_e = T.entry(s, self.name, fonts=f)
            self.fn_e = T.entry(s, self.fn, fonts=f)
            self.fn_note = Ref("")

            def paint_you(c):
                c.text(p(24), p(20), "You", font=f.h2)
                y = field(c, p(60), "Your name", self.name_e, "What your team sees.")
                y = field(c, y, "Your Fortnite names", self.fn_e,
                          "Only these players' chests count for you. Add your console name too (e.g. your PlayStation "
                          "name), separated by commas.", self.fn_note)
                c.fit(y, p(8))

            c3 = owner._card(s, paint_you, 3, 0, height=250)
            self.fn_note.on_change = c3.repaint
            self.name_e.bind("<Return>", lambda ev: self._save_name())
            self.name_e.bind("<FocusOut>", lambda ev: self._save_name())
            self.fn_e.bind("<Return>", lambda ev: self._save_fortnite_names())
            self.fn_e.bind("<FocusOut>", lambda ev: self._save_fortnite_names())

            # --- team
            self.code = tk.StringVar(value=cfg.get("team_code", ""))
            self.guests = tk.StringVar(value=", ".join(cfg.get("console_teammates") or []))
            self.code_e = T.entry(s, self.code, fonts=f)
            self.guests_e = T.entry(s, self.guests, fonts=f)
            self.g_note = Ref("")
            self.status = Ref("")

            def paint_team(c):
                c.text(p(24), p(20), "Team", font=f.h2)
                c.text(p(24), p(44), "Friends who enter the same team code share their chest counts live. Everything "
                       "is locked with the code before it leaves your PC, so a long code is a strong lock.",
                       font=f.small, fill=T.DIM, width=c.W - p(48))
                y = p(92)
                c.text(p(24), y + p(16), "Team code", font=f.bold, anchor="w")
                box(c, self.code_e, p(190), y, max(p(120), c.W - p(390)))
                x0, *_ = T.pill(c, c.W - p(24), y - p(1), "Join", self._join, kind="primary", anchor="ne", h=p(34))
                T.pill(c, x0 - p(8), y - p(1), "Make a code", self._make_code, anchor="ne", h=p(34))
                y += p(52)
                strength = ct.teamcrypt.code_strength(self.code.get()) if self.code.get().strip() else ""
                if strength == "weak":
                    c.text(p(190), y - p(8), "Short codes are easy to guess. Try Make a code.", font=f.small, fill=T.AMBER)
                    y += p(16)
                y = field(c, y, "Console teammates", self.guests_e,
                          "Fortnite names of teammates on a console without a tracker, separated by commas. Their "
                          "chests in your games count for the team.", self.g_note)
                if self.status.text:
                    item = c.text(p(24), y, self.status.text, font=f.small, fill=T.GREEN, width=c.W - p(48))
                    y = (c.bbox(item) or (0, 0, 0, y + p(30)))[3]
                c.fit(y, p(20))

            c4 = owner._card(s, paint_team, 3, 1, height=330)
            self.team_card = c4
            self.g_note.on_change = c4.repaint
            self.status.on_change = c4.repaint
            self.code_e.bind("<Return>", lambda ev: self._join())
            self.code_e.bind("<KeyRelease>", lambda ev: c4.repaint(), add="+")
            self.guests_e.bind("<Return>", lambda ev: self._save_guests())
            self.guests_e.bind("<FocusOut>", lambda ev: self._save_guests())

            # --- privacy
            self.offline = tk.BooleanVar(value=bool(cfg.get("offline")))
            self.offline_sw = T.Switch(s, self.offline, self._offline_changed)
            ed = ct.edition.load()
            self.can_report = bool(ed.get("report_key"))
            choice = cfg.get("send_reports")
            self.reports = tk.BooleanVar(value=choice if isinstance(choice, bool) else bool(ed.get("reports")))
            self.reports_sw = T.Switch(s, self.reports, self._reports_changed)

            def paint_privacy(c):
                c.text(p(24), p(20), "Privacy", font=f.h2)
                c.put(self.offline_sw, p(24), p(62))
                c.text(p(80), p(74), "Offline mode", font=f.body, anchor="w")
                c.text(p(80), p(94), "Nothing leaves this PC and nothing is downloaded: no squad sync, no update checks, "
                       "no problem reports, no map download (the map works only if it was downloaded before). "
                       "Counting your own chests works the same.", font=f.small, fill=T.DIM, width=c.W - p(110))
                y = p(160)
                if self.can_report:
                    c.put(self.reports_sw, p(24), y)
                    c.text(p(80), y + p(12), "Send problem reports", font=f.body, anchor="w")
                    c.text(p(80), y + p(32), "If Chest Tracker has trouble starting, the launcher sends a short report "
                           "(errors and the log, with your Windows name and folders blanked out), sealed so only the "
                           "publisher can read it.", font=f.small, fill=T.DIM, width=c.W - p(110))
                    y += p(90)
                c.text(p(24), y, "Other players' names never leave this PC: only your squad's own numbers are shared.",
                       font=f.small, fill=T.MUTED, width=c.W - p(48))

            self.privacy_card = owner._card(s, paint_privacy, 4, 0, colspan=2, height=280 if self.can_report else 200)

            # --- app
            def paint_app(c):
                c.text(p(24), p(20), "Chest Tracker", font=f.h2)
                c.text(p(24), p(44), f"Version {getattr(ct, 'APP_VERSION', '?')}", font=f.small, fill=T.DIM)
                x = c.W - p(24)
                for label, cmd in reversed([("Show the intro again", owner._show_intro),
                                            ("Reset this game", self._reset), ("Open data folder", self._open_folder),
                                            ("Quit Chest Tracker", app.quit)]):
                    x0, *_ = T.pill(c, x, p(24), label, cmd, anchor="ne", h=p(36))
                    x = x0 - p(8)

            owner._card(s, paint_app, 5, 0, colspan=2, height=90)
            tk.Frame(s, bg=T.BASE, height=p(10)).grid(row=6, column=0)
            self._refresh()

        def _make_code(self):
            self.code.set(ct.teamcrypt.make_code())
            try:
                self.team_card.repaint()
            except Exception:
                pass
            self.app.toast("New code made. Click Join, then send the same code to your squad.", 8)

        def _offline_changed(self):
            on = bool(self.offline.get())
            self.app.cfg["offline"] = on
            ct.save_json(ct.CONFIG_PATH, self.app.cfg)
            net = getattr(self.app, "net", None)
            if net is not None:
                net.wake.set()
            self.app.toast("Offline mode on: nothing leaves this PC" if on else "Offline mode off", 5)
            self._refresh(once=True)

        def _reports_changed(self):
            self.app.cfg["send_reports"] = bool(self.reports.get())
            ct.save_json(ct.CONFIG_PATH, self.app.cfg)

        def _overlay_full_changed(self):
            self.app.cfg["overlay_always_full"] = bool(self.full.get())
            ct.save_json(ct.CONFIG_PATH, self.app.cfg)
            if hasattr(self.app, "redraw"):
                try:
                    self.app.redraw(force=True)
                except Exception:
                    log.exception("overlay redraw")

        def _overlay_style_changed(self):
            self.app.cfg["overlay_style"] = "glass" if self.glass.get() else "classic"
            ct.save_json(ct.CONFIG_PATH, self.app.cfg)
            self.owner.refresh_overlay()

        def _live_replay_changed(self):
            self.app.cfg["live_replay"] = bool(self.live_replay.get())
            ct.save_json(ct.CONFIG_PATH, self.app.cfg)

        def _record_changed(self):
            self.app.cfg["record_matches"] = bool(self.record.get())
            ct.save_json(ct.CONFIG_PATH, self.app.cfg)

        def _record_test(self):
            rec = getattr(self.app, "recorder", None)
            if rec is None:
                self._record_said("Recording isn't available (see the log)")
                return
            self._record_said("Recording 10 s with OBS...")
            rec.test_clip(lambda msg: self.owner.win.after(0, lambda: self._record_said(msg)))

        def _record_said(self, msg: str):
            self.record_note = msg
            try:
                self.counting_card.repaint()
            except Exception:
                pass

        def _save_name(self):
            name = self.name.get().strip()[:20]
            if name and name != self.app.cfg.get("name"):
                self.app.cfg["name"] = name
                ct.save_json(ct.CONFIG_PATH, self.app.cfg)
                try:
                    self.owner.me_card.repaint()
                except Exception:
                    pass

        def _reset(self):
            self.app.stats.new_game("reset in settings")
            self.app.toast("New game started", 4)

        def _open_folder(self):
            try:
                os.startfile(str(ct.DATA_DIR))  # noqa: S606 (opens Explorer on our own folder)
            except Exception:
                log.exception("open data folder")

        def save_all(self):
            self._save_fortnite_names()
            self._save_guests()
            self._save_name()

        def close(self):  # the page lives as long as the window
            self.save_all()

    return SettingsPanel
