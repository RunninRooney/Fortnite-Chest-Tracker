"""
The map window: a normal, resizable window (put it on a second screen) showing the island with
  * Match: your route and a dot for every chest you open, live (from the minimap), your squad's
    positions when they run Chest Tracker too, and after the match the replay: exact routes,
    chests (opened, broken, broken with a build), landing spots and eliminations, with a line from
    the eliminating player to where the eliminated player went down. Any past match can be picked,
    and the timeline plays it back from above (play / pause, speed, drag to any moment);
  * Heatmap: routes of many games drawn over each other, movement (time spent), landing spots,
    chests opened or eliminations, for you, the squad or one teammate, filtered by season, period,
    the best / worst games by chests or eliminations, the longest / shortest games, where you
    landed and who you played with.
Known chest spots can be shown faintly, with the ones looted in the match ringed.

Mouse wheel zooms, dragging moves the map (and stops following), double-click follows again.
Space plays / pauses. The window remembers where it was and how big.

Marks are drawn at twice the size and scaled down, so lines and circles come out smooth.
"""
from __future__ import annotations

import math
import os
import sys
import time

from map_tracker import (GAMES, HEAT_KINDS, MAP_SIZE, PERIODS, filter_matches, heat_points, island_circle, kill_info,
                         chests_so_far, landing_name, map_to_world, match_events, outer_loot_path, player_stats,
                         render_heat, route_lines, smooth, spot_hit_rates, storm_at, world_to_map)

ME = (255, 197, 61)
MATES = [(80, 200, 255), (255, 110, 180), (130, 225, 120), (185, 150, 255), (255, 150, 90)]
SPOT = (235, 238, 255)
LOOTED = (96, 214, 120)
KILL = (255, 70, 70)
DARK = (16, 17, 28)
BG = (12, 8, 6)  # outside the map picture: the app's dark brown
LIVE = "Live / latest match"
SPEEDS = {"1x": 1, "2x": 2, "5x": 5, "10x": 10, "30x": 30}
K = 2  # supersampling: marks are drawn this many times bigger, then scaled down smoothly (1 while playing back)
ANYWHERE = "Landed anywhere"
ANYONE = "With anyone"
STORM = (150, 60, 220)
RANDOM = (170, 170, 182)  # random squad fills: shown, never saved or counted
# colours for comparing many matches
COMPARE = [(255, 197, 61), (80, 200, 255), (255, 110, 180), (130, 225, 120), (185, 150, 255), (255, 150, 90),
           (90, 230, 210), (240, 240, 120), (255, 90, 90), (170, 200, 255), (210, 160, 110), (200, 120, 240)]
EVENT_COLORS = {"chest": (255, 197, 61), "broken": (255, 150, 90), "kill": (255, 70, 70), "landing": (235, 238, 255),
                "out": (140, 140, 160), "storm": STORM}
ALIGNS = ["Align by landing", "Align by match start"]


def fmt_time(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 60}:{t % 60:02d}"


def at_time(route, t):
    """Last route point at or before t (route: [[t, x, y, z], ...]), or None."""
    import bisect

    i = bisect.bisect_right([p[0] for p in route], t)
    return route[i - 1] if i > 0 else None


def position_at(route, t):
    """Where a route ([[t, x, y, z...], ...]) was at time t: between two recorded points it moves in a
    straight line (so playback glides instead of jumping a second at a time); across a long gap (standing
    still, riding a vehicle) it stays at the last point. None before the first point."""
    import bisect

    i = bisect.bisect_right([p[0] for p in route], t)
    if i <= 0:
        return None
    a = route[i - 1]
    if i >= len(route):
        return a
    b = route[i]
    if b[0] - a[0] > 6 or b[0] <= a[0]:
        return a
    f = (t - a[0]) / (b[0] - a[0])
    return [t] + [a[k] + (b[k] - a[k]) * f for k in range(1, min(len(a), len(b)))]


def smooth_timed(points, rounds: int = 2):
    """Chaikin smoothing of (t, x, y) points, keeping a time on every point so a smoothed route can be
    cut at any moment."""
    return smooth(points, rounds)


def spot_alpha(there: int, visits: int) -> int:
    """How strongly a known chest spot is drawn: bright when a chest is usually there, dimmer (but still
    clearly visible) when it rarely is; unknown spots in between."""
    if visits < 2:
        return 190
    return round(110 + 145 * there / visits)


def heading_of(points) -> float | None:
    """Direction of travel (radians, screen coordinates) from the last points of a path."""
    if len(points) < 2:
        return None
    x1, y1 = points[-1]
    for x0, y0 in reversed(points[:-1]):
        if abs(x1 - x0) + abs(y1 - y0) > 0.5:
            return math.atan2(y1 - y0, x1 - x0)
    return None


# loot path spread choices: (label, most chests from one place)
SPREADS = [("Clear towns (no limit)", None), ("Some spread (8 per place)", 8), ("Spread out (5 per place)", 5),
           ("Wide spread (3 per place)", 3)]


class MapWindow:
    def __init__(self, app, manager, save_cfg, parent=None, on_show=None):
        """parent: build into this frame (the app window's Map page) instead of a window of its own;
        on_show: called instead of raising a window when something asks to show the map."""
        import tkinter as tk
        from tkinter import ttk

        self.app, self.m, self.save_cfg = app, manager, save_cfg
        self.embedded, self._on_show = parent is not None, on_show
        cfg = app.cfg
        if self.embedded:
            w = self.win = tk.Frame(parent, bg="#0c0806")
        else:
            w = self.win = tk.Toplevel(app.root)
            w.title("Chest Tracker map")
            w.minsize(400, 420)
            w.configure(bg="#0c0806")
            w.protocol("WM_DELETE_WINDOW", self.hide)
            geo = cfg.get("map_geometry")
            if isinstance(geo, str) and self._geometry_on_screen(geo):
                w.geometry(geo)
            else:
                w.geometry("820x860")

        bar = self.bar = ttk.Frame(w, padding=(6, 4))
        bar.pack(side="top", fill="x")
        self.simple = False  # simple mode: the three rows of controls hidden (see set_simple)
        self.view = tk.StringVar(value=cfg.get("map_view") or "match")
        ttk.Radiobutton(bar, text="Match", value="match", variable=self.view, command=self._changed).pack(side="left")
        ttk.Radiobutton(bar, text="Heatmap", value="heat", variable=self.view, command=self._changed).pack(side="left", padx=(6, 10))
        self.match_box = ttk.Combobox(bar, values=[LIVE], width=32, state="readonly")
        self.match_box.set(LIVE)
        kind = cfg.get("map_heat_kind") or "routes"
        self.kind = tk.StringVar(value=HEAT_KINDS.get(kind, HEAT_KINDS["routes"]))
        self.kind_box = ttk.Combobox(bar, textvariable=self.kind, values=list(HEAT_KINDS.values()), width=20, state="readonly")
        self.who = tk.StringVar(value=cfg.get("map_heat_who") or "Me")
        self.who_box = ttk.Combobox(bar, textvariable=self.who, values=["Me", "Squad"], width=14, state="readonly")
        self.season = tk.StringVar(value="All seasons")
        self.season_box = ttk.Combobox(bar, textvariable=self.season, values=["All seasons"], width=11, state="readonly")

        # filters (heatmap view)
        fl = self.filters = ttk.Frame(w, padding=(6, 0, 6, 4))
        self.period = tk.StringVar(value=cfg.get("map_period") if cfg.get("map_period") in PERIODS else "All time")
        self.period_box = ttk.Combobox(fl, textvariable=self.period, values=list(PERIODS), width=11, state="readonly")
        self.period_box.pack(side="left")
        self.games = tk.StringVar(value=cfg.get("map_games") if cfg.get("map_games") in GAMES else GAMES[0])
        self.games_box = ttk.Combobox(fl, textvariable=self.games, values=GAMES, width=24, state="readonly")
        self.games_box.pack(side="left", padx=4)
        self.place = tk.StringVar(value=ANYWHERE)
        self.place_box = ttk.Combobox(fl, textvariable=self.place, values=[ANYWHERE], width=24, state="readonly")
        self.place_box.pack(side="left")
        self.mate = tk.StringVar(value=ANYONE)
        self.mate_box = ttk.Combobox(fl, textvariable=self.mate, values=[ANYONE], width=18, state="readonly")
        self.mate_box.pack(side="left", padx=4)
        self.route_chests = tk.BooleanVar(value=bool(cfg.get("map_route_chests", True)))
        ttk.Checkbutton(fl, text="Chests", variable=self.route_chests, command=self._changed).pack(side="left", padx=4)
        for box in (self.kind_box, self.who_box, self.season_box, self.period_box, self.games_box,
                    self.place_box, self.mate_box):
            box.bind("<<ComboboxSelected>>", lambda e: self._picked())
        self.match_box.bind("<<ComboboxSelected>>", lambda e: (self.compare_files.clear(), self._picked()))

        bar2 = self.bar2 = ttk.Frame(w, padding=(6, 0, 6, 4))
        bar2.pack(side="top", fill="x")
        self.follow = tk.BooleanVar(value=bool(cfg.get("map_follow", True)))
        ttk.Checkbutton(bar2, text="Follow me", variable=self.follow, command=self._changed).pack(side="left")
        self.show_spots = tk.BooleanVar(value=bool(cfg.get("map_spots", True)))
        ttk.Checkbutton(bar2, text="Chest spots", variable=self.show_spots, command=self._changed).pack(side="left", padx=8)
        ttk.Button(bar2, text="Matches...", command=self.open_browser).pack(side="left", padx=(8, 0))
        self.show_panel = tk.BooleanVar(value=bool(cfg.get("map_panel", True)))
        ttk.Checkbutton(bar2, text="Stats", variable=self.show_panel, command=self._changed).pack(side="left", padx=8)
        ttk.Button(bar2, text="Save image", command=self.save_image).pack(side="left")
        self.plan_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar2, text="Plan loot path (click a landing spot)", variable=self.plan_mode,
                        command=self._changed).pack(side="left", padx=8)
        # how many chests the plan goes for (10-80); moving it re-plans from the same landing spot
        self.plan_count = tk.IntVar(value=int(cfg.get("map_plan_count") or 25))
        self.plan_count_label = ttk.Label(bar2, width=9)
        self.plan_count_label.pack(side="left")
        self._plan_scale = ttk.Scale(bar2, from_=10, to=80, length=120, command=self._plan_count_moved)
        self._plan_scale.set(self.plan_count.get())
        self._plan_scale.pack(side="left")
        self._plan_from = None
        # planning tools (shown while planning): what a click does, the middle, clearing marks
        pb = self.plan_bar = ttk.Frame(w, padding=(6, 0, 6, 4))
        self.plan_tool = tk.StringVar(value="landing")
        for value, text in (("landing", "Landing spot"), ("avoid", "No-go circle (drag)"), ("checkpoint", "Checkpoint")):
            ttk.Radiobutton(pb, text=text, value=value, variable=self.plan_tool).pack(side="left", padx=(0, 6))
        self.avoid_middle = tk.BooleanVar(value=bool(cfg.get("map_plan_avoid_middle", True)))
        ttk.Checkbutton(pb, text="Avoid the middle", variable=self.avoid_middle,
                        command=self._marks_changed).pack(side="left", padx=8)
        # spread: clear dense towns, or at most a few chests per place so the route spreads round the island
        ttk.Label(pb, text="Spread:").pack(side="left", padx=(4, 2))
        self.plan_spread = tk.StringVar(value=self._spread_label(cfg.get("map_plan_per_place")))
        spread = ttk.Combobox(pb, textvariable=self.plan_spread, values=[t for t, _ in SPREADS], state="readonly",
                              width=22)
        spread.bind("<<ComboboxSelected>>", lambda e: self._spread_changed())
        spread.pack(side="left", padx=(0, 8))
        ttk.Button(pb, text="Clear marks", command=self.clear_plan_marks).pack(side="left")
        ttk.Label(pb, text="Right-click a circle or checkpoint to remove it", foreground="#8a8fb0").pack(side="left", padx=8)
        marks = cfg.get("map_plan_marks") or {}
        self.avoid_circles = [tuple(c) for c in marks.get("avoid", []) if len(c) == 3]   # (map x, map y, map r)
        self.checkpoints = [tuple(c) for c in marks.get("checkpoints", []) if len(c) == 2]  # (map x, map y), in order
        self._circle_preview = None
        ttk.Button(bar2, text="Whole island", command=self.whole_island).pack(side="right")
        ttk.Button(bar2, text="-", width=3, command=lambda: self.zoom_by(1 / 1.5)).pack(side="right", padx=2)
        ttk.Button(bar2, text="+", width=3, command=lambda: self.zoom_by(1.5)).pack(side="right", padx=2)

        self.status = ttk.Label(w, padding=(6, 2), foreground="#8a8fb0")  # readable on light and dark
        self.status.pack(side="bottom", fill="x")
        # timeline (shown for a replay): play / pause, speed, time slider
        tl = self.timeline = ttk.Frame(w, padding=(6, 2))
        self.play_btn = ttk.Button(tl, text="Play", width=6, command=self.toggle_play)
        self.play_btn.pack(side="left")
        self.speed = tk.StringVar(value=cfg.get("map_speed") or "5x")
        ttk.Combobox(tl, textvariable=self.speed, values=list(SPEEDS), width=4, state="readonly").pack(side="left", padx=4)
        ttk.Button(tl, text="<", width=3, command=lambda: self.next_event(-1)).pack(side="left")
        ttk.Button(tl, text=">", width=3, command=lambda: self.next_event(1)).pack(side="left", padx=(2, 4))
        self.align = tk.StringVar(value=ALIGNS[0])
        self.align_box = ttk.Combobox(tl, textvariable=self.align, values=ALIGNS, width=19, state="readonly")
        self.align_box.bind("<<ComboboxSelected>>", lambda e: self._picked())
        # whose match this is: follow them, draw their route on top, their stats first
        self.watch_var = tk.StringVar(value="")
        self.watch_box = ttk.Combobox(tl, textvariable=self.watch_var, width=20, state="readonly")
        self.watch_box.bind("<<ComboboxSelected>>", lambda e: self._watch_changed())
        self.time_label = ttk.Label(tl, width=12, anchor="e")
        self.time_label.pack(side="right")
        self.slider_box = ttk.Frame(tl)
        self.slider_box.pack(side="left", fill="x", expand=True, padx=6)
        self.slider = ttk.Scale(self.slider_box, from_=0, to=1, command=self._scrubbed)
        self.slider.pack(side="top", fill="x")
        # ticks for chests (gold), eliminations (red), landings (white), storm (purple): click to jump
        self.ticks = tk.Canvas(self.slider_box, height=12, bg="#0c0806", highlightthickness=0)
        self.ticks.pack(side="top", fill="x")
        self.ticks.bind("<Button-1>", self._tick_clicked)
        # side panel: the match's numbers and its events (click one to jump to it)
        pn = self.panel = ttk.Frame(w, padding=(6, 2), width=250)
        self.stats_label = ttk.Label(pn, justify="left", font=("Segoe UI", 9), wraplength=240)
        self.stats_label.pack(side="top", fill="x")
        ttk.Label(pn, text="Events (click to jump)", font=("Segoe UI Semibold", 9)).pack(side="top", anchor="w", pady=(8, 0))
        evf = ttk.Frame(pn)
        evf.pack(side="top", fill="both", expand=True)
        self.events_list = tk.Listbox(evf, width=36, activestyle="none", font=("Segoe UI", 9), bg="#120c09", fg="#f3e9df",
                                      selectbackground="#33200f", highlightthickness=0, bd=0)
        esb = ttk.Scrollbar(evf, orient="vertical", command=self.events_list.yview)
        self.events_list.configure(yscrollcommand=esb.set)
        self.events_list.pack(side="left", fill="both", expand=True)
        esb.pack(side="right", fill="y")
        self.events_list.bind("<<ListboxSelect>>", self._event_clicked)
        self.canvas = tk.Canvas(w, bg="#0c0806", highlightthickness=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        self._img_id = self.canvas.create_image(0, 0, anchor="nw")
        # Windows: frames go straight onto the canvas (Pillow's ImageWin), ~3x faster than a Tk photo image
        # on a big map; Tk repaints the canvas on expose, so the last frame is put back then
        self._dib = None
        self._dib_ok = sys.platform == "win32" and os.environ.get("CHEST_MAP_TK") != "1"
        self.canvas.bind("<Expose>", self._exposed, add="+")
        self._quick_until = 0.0   # while dragging / zooming: a quick draft, the sharp one when the mouse stops
        self._settle_after = None
        self._quick_kind = "drag"
        self._soon = None         # a redraw asked for (coalesced)
        self._in_render = False

        self.zoom = float(cfg.get("map_zoom") or 1.6)   # screen px per map px
        self.center = self.island_center()
        self.t: float | None = None   # timeline position (s into the replay); None = the end
        self.playing = False
        self._shown_rec = None
        self._match_choices: dict[str, str] = {}
        self._places: dict[str, tuple[float, float]] = {}
        self._was_heat = None
        self._base = None
        self._base_path = None
        self._layer_key = None
        self._layer = None
        self._layer_info = ""
        self._last_key = None
        self._drag = None
        self._save_after = None
        self._photo = None
        self._last_tick = time.perf_counter()
        self._setting_slider = False
        self._fonts = {1: self._label_font(14), 2: self._label_font(28)}
        self._font = self._fonts[2]
        self._tag_fonts = {1: self._label_font(15, bold=True), 2: self._label_font(30, bold=True)}  # name tags
        self.show_key = True  # the shape key in the status line and the compare legend (see set_key)
        self.render_count = 0  # goes up every time the map picture is redrawn (every frame while playing)
        self.legend_version = 0  # goes up only when legend_items() would return something different
        self._legend_seen = ()
        self._choices_key = None
        self.show_timeline = True  # the timeline row under the map (see set_timeline)
        self._heat_routes: dict = {}  # smoothed routes for the routes heatmap, per match and player
        self._layer_job = None    # the heatmap key being built in the background
        self._layer_done = None   # (key, layer, info, matches, kind) once it's ready
        self._layer_kind = None
        self._layer_matches = 0
        self._layer_info = ""
        self._base_cache = None  # (key, picture of a bigger area than the view, its left/top in map px)
        self._route_cache: dict = {}  # smoothed replay routes, per match and player
        self._spots_in_base = False
        self._legend: list = []
        self._bg = BG
        self.compare_files: list[str] = []   # several matches drawn at once
        self.loot = None                      # a suggested loot path to draw
        self._events: list[dict] = []
        self._panel_key = None
        self._rates = (None, {})
        self._browser = None
        self._last_image = None

        c = self.canvas
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._move)
        c.bind("<ButtonRelease-1>", self._released)
        c.bind("<ButtonRelease-3>", self._right_click)
        c.bind("<Double-Button-1>", lambda e: (self.follow.set(True), self._changed()))
        w.bind("<Configure>", self._configured)
        w.bind("<space>", lambda e: self.toggle_play())
        self._tick()

    @staticmethod
    def _label_font(size: int, bold: bool = False):
        """A font with the letters people put in Fortnite names (small capitals, symbols...)."""
        from PIL import ImageFont

        names = ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")
        if bold:
            names = ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf") + names
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    # ---- showing / hiding ---------------------------------------------------------------------
    def show(self):
        if self.embedded:
            if self._on_show:
                self._on_show()
            return
        self.win.deiconify()
        self.win.lift()
        self.app.cfg["map_window"] = True
        self.save_cfg()

    def hide(self):
        self._remember()
        self.playing = False
        if self.embedded:
            return
        self.win.withdraw()
        self.app.cfg["map_window"] = False
        self.save_cfg()

    def _visible(self) -> bool:
        try:
            return bool(self.win.winfo_viewable()) if self.embedded else self.win.state() == "normal"
        except Exception:
            return False

    def _geometry_on_screen(self, geo: str) -> bool:
        import re

        # Tk writes a screen left of / above the main one as "+-1920"
        m = re.fullmatch(r"(\d+)x(\d+)([+-])(-?\d+)([+-])(-?\d+)", geo.strip())
        if not m:
            return False
        x, y = int(m.group(4)), int(m.group(6))
        if m.group(3) == "-" or m.group(5) == "-":
            return False  # measured from the right/bottom edge: not something this window saves
        check = getattr(self.app, "_on_screen", None)
        return bool(check(x + 40, y + 10)) if check else True

    def _configured(self, e):
        if e.widget is self.win:
            if self._save_after:
                self.win.after_cancel(self._save_after)
            self._save_after = self.win.after(800, self._remember)

    def _remember(self):
        self._save_after = None
        try:
            if not self.embedded and self._visible():
                self.app.cfg["map_geometry"] = self.win.geometry()
            cfg = self.app.cfg
            cfg["map_view"], cfg["map_follow"], cfg["map_spots"] = self.view.get(), self.follow.get(), self.show_spots.get()
            cfg["map_zoom"] = round(self.zoom, 3)
            cfg["map_heat_kind"] = self._kind_key()
            cfg["map_heat_who"] = self.who.get()
            cfg["map_speed"] = self.speed.get()
            cfg["map_period"], cfg["map_games"] = self.period.get(), self.games.get()
            cfg["map_route_chests"] = self.route_chests.get()
            self.save_cfg()
        except Exception:
            pass

    # ---- controls ------------------------------------------------------------------------------
    def _layout_bar(self):
        heat = self.view.get() == "heat"
        for box in (self.match_box, self.kind_box, self.who_box, self.season_box):
            box.pack_forget()
        if heat:
            self.kind_box.pack(side="left")
        else:
            self.match_box.pack(side="left")
        self.who_box.pack(side="left", padx=4)
        self.season_box.pack(side="left")
        if not self.simple and self.filters.winfo_manager() != "pack":
            self.filters.pack(side="top", fill="x", after=self.bar)

    def _picked(self):
        self.t = None  # a newly picked match shows all of it; Play watches it from the start
        self.playing = False
        self._changed()

    def _timeline_length(self) -> float | None:
        if self.view.get() != "match":
            return None
        if self.compare_files:
            recs = self._compare_recs()
            return max((self._compare_span(r) for r in recs), default=None)
        return self._length(self._shown_rec) if self._shown_rec is not None else None

    def toggle_play(self):
        length = self._timeline_length()
        if length is None and self.view.get() == "match":
            self._render()  # a match picked a moment ago may not be drawn yet
            length = self._timeline_length()
        if length is None:
            return
        if not self.playing and (self.t is None or self.t >= length - 0.5):
            self.t = 0.0  # from the start
        self.playing = not self.playing
        self._last_tick = time.perf_counter()
        self._changed()

    def _scrubbed(self, value):
        length = self._timeline_length()
        if self._setting_slider or length is None:
            return
        t = float(value) * length
        self.t = None if t >= length - 0.5 else t
        self._changed()

    def island_center(self) -> tuple[float, float]:
        box = getattr(self.m, "island", None)
        if box:
            return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        return MAP_SIZE / 2, MAP_SIZE / 2 - 20

    def whole_island(self):
        """Fits the island (not the whole picture: its art is off centre) in the view."""
        w, h = max(50, self.canvas.winfo_width()), max(50, self.canvas.winfo_height())
        self.follow.set(False)
        self.center = self.island_center()
        box = getattr(self.m, "island", None)
        if box:
            self.zoom = min(w / ((box[2] - box[0]) * 1.06), h / ((box[3] - box[1]) * 1.06))
        else:
            self.zoom = min(w, h) / (MAP_SIZE * 0.62)  # the island fills about 62% of the image
        # but never so far out that the view shows past the picture's edges
        self.zoom = max(self.zoom, self._min_zoom(w, h))
        self._clamp_view(w, h)
        self._changed()

    def _min_zoom(self, w=None, h=None) -> float:
        """The furthest out the map can go while the picture still covers the whole view."""
        w = w or max(50, self.canvas.winfo_width())
        h = h or max(50, self.canvas.winfo_height())
        return max(w, h) / MAP_SIZE

    def _clamp_view(self, w=None, h=None):
        """Keeps the view inside the map picture (no empty background at the edges)."""
        w = w or max(50, self.canvas.winfo_width())
        h = h or max(50, self.canvas.winfo_height())
        self.zoom = max(self.zoom, self._min_zoom(w, h))
        hw, hh = w / 2 / self.zoom, h / 2 / self.zoom
        cx = min(max(self.center[0], hw), MAP_SIZE - hw)
        cy = min(max(self.center[1], hh), MAP_SIZE - hh)
        self.center = (cx, cy)

    def zoom_by(self, k: float, at=None):
        old = self.zoom
        self.zoom = max(self._min_zoom(), min(6.0, self.zoom * k))
        if at is not None and not self.follow.get():
            # keep the map point under the mouse where it is
            w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
            mx = self.center[0] + (at[0] - w / 2) / old
            my = self.center[1] + (at[1] - h / 2) / old
            self.center = (mx - (at[0] - w / 2) / self.zoom, my - (at[1] - h / 2) / self.zoom)
        self._clamp_view()
        self._interact("zoom")
        self._changed()

    def _wheel(self, e):
        self.zoom_by(1.25 if e.delta > 0 else 1 / 1.25, (e.x, e.y))

    def _to_map(self, sx, sy):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return self.center[0] + (sx - w / 2) / self.zoom, self.center[1] + (sy - h / 2) / self.zoom

    def _released(self, e):
        """While planning: a click places a landing spot or a checkpoint; a drag with the circle tool draws a
        no-go circle (centre where you pressed, edge where you let go)."""
        if not self.plan_mode.get() or not self._drag:
            return
        x0, y0, _ = self._drag
        tool = self.plan_tool.get()
        if tool == "avoid":
            self._circle_preview = None
            cx, cy = self._to_map(x0, y0)
            ex, ey = self._to_map(e.x, e.y)
            r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
            if r * self.zoom >= 6:
                self.add_avoid_circle(cx, cy, r)
            else:
                self._changed()
            return
        if abs(e.x - x0) + abs(e.y - y0) > 5:
            return  # that was a drag (moving the map)
        mx, my = self._to_map(e.x, e.y)
        if tool == "checkpoint":
            self.add_checkpoint(mx, my)
        else:
            self.plan_loot_from(mx, my)

    def _right_click(self, e):
        """While planning: removes the no-go circle or checkpoint under the mouse."""
        if not self.plan_mode.get():
            return
        mx, my = self._to_map(e.x, e.y)
        near = 14 / self.zoom
        for i, (cx, cy) in enumerate(self.checkpoints):
            if ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5 <= near:
                del self.checkpoints[i]
                self._marks_changed()
                return
        inside = [(r, i) for i, (cx, cy, r) in enumerate(self.avoid_circles) if ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5 <= r]
        if inside:
            del self.avoid_circles[min(inside)[1]]  # the smallest circle under the mouse
            self._marks_changed()

    # ---- planner marks (public, map px) ------------------------------------------------------------------
    def add_avoid_circle(self, map_x: float, map_y: float, map_r: float):
        """A no-go circle: the loot path never plans chests inside and walks round it."""
        self.avoid_circles.append((float(map_x), float(map_y), float(map_r)))
        self._marks_changed()

    def add_checkpoint(self, map_x: float, map_y: float):
        """A checkpoint: the loot path visits checkpoints in the order they were added."""
        self.checkpoints.append((float(map_x), float(map_y)))
        self._marks_changed()

    def clear_plan_marks(self):
        self.avoid_circles.clear()
        self.checkpoints.clear()
        self._marks_changed()

    @staticmethod
    def _spread_label(per_place) -> str:
        return next((t for t, n in SPREADS if n == per_place), SPREADS[0][0])

    def _spread_changed(self):
        self.app.cfg["map_plan_per_place"] = self.plan_per_place()
        self._marks_changed()  # saves and re-plans

    def plan_per_place(self) -> int | None:
        """The most chests a plan takes from one place (None: no limit, clear the towns)."""
        return dict(SPREADS).get(self.plan_spread.get())

    def set_plan_spread(self, per_place: int | None):
        """Spread for planned loot paths: None (clear dense towns) or 8 / 5 / 3 chests per place at most."""
        self.plan_spread.set(self._spread_label(per_place))
        self._spread_changed()

    def set_plan_tool(self, tool: str):
        """What a click does while planning: "landing", "avoid" (drag a circle) or "checkpoint"."""
        if tool in ("landing", "avoid", "checkpoint"):
            self.plan_tool.set(tool)

    def plan_marks(self) -> dict:
        return {"avoid": list(self.avoid_circles), "checkpoints": list(self.checkpoints),
                "avoid_middle": bool(self.avoid_middle.get()), "tool": self.plan_tool.get()}

    def _marks_changed(self):
        self.app.cfg["map_plan_marks"] = {"avoid": [list(c) for c in self.avoid_circles],
                                          "checkpoints": [list(c) for c in self.checkpoints]}
        self.app.cfg["map_plan_avoid_middle"] = bool(self.avoid_middle.get())
        try:
            self.save_cfg()
        except Exception:
            pass
        if self._plan_from is not None:
            self.plan_loot_from(*self._plan_from)  # re-plan with the new marks
        else:
            self._changed()

    def _plan_count_moved(self, value):
        n = int(round(float(value)))
        self.plan_count_label.configure(text=f"{n} chests")
        if n == self.plan_count.get():
            return
        self.plan_count.set(n)
        self.app.cfg["map_plan_count"] = n
        if self._plan_from is not None:  # re-plan from the same spot (after the slider settles)
            if getattr(self, "_replan_after", None):
                self.win.after_cancel(self._replan_after)
            self._replan_after = self.win.after(250, lambda: self.plan_loot_from(*self._plan_from))

    def set_plan_count(self, n: int):
        """How many chests a planned loot path goes for (10-80)."""
        self._plan_scale.set(max(10, min(80, int(n))))

    def set_plan_mode(self, on: bool):
        """On: a click on the map plans a loot path from there (see plan_loot_from)."""
        self.plan_mode.set(bool(on))
        self._changed()

    def plan_loot_from(self, map_x: float, map_y: float, count: int | None = None) -> dict | None:
        """A loot path from a landing spot (map px) through the best `count` chests around the outside of the
        island, never through the middle. Shown on the map; also returned."""
        box = getattr(self.m, "island", None)
        if not box:
            self.status.configure(text="The map isn't ready yet.")
            return None
        tf = self.m.tf
        season = self.m.season()
        spots = self.m.spots.spots(season)
        if not spots:
            self.status.configure(text="No known chest spots yet: they come from your replays.")
            return None
        start = map_to_world(map_x, map_y, tf)
        self._plan_from = (map_x, map_y)
        count = count or self.plan_count.get()
        scale = tf["scale"]
        avoid = [(*map_to_world(x, y, tf), r / scale) for x, y, r in self.avoid_circles]
        checkpoints = [map_to_world(x, y, tf) for x, y in self.checkpoints]
        path = outer_loot_path(spots, start, island_circle(box, tf), self._hit_rates(season, spots), count=count,
                               avoid=avoid, checkpoints=checkpoints, avoid_middle=bool(self.avoid_middle.get()),
                               max_per_place=self.plan_per_place(), pois=self.m.pois())
        path["place"] = landing_name(*start, self.m.pois())
        self.loot = path
        self._changed()
        return path

    def _press(self, e):
        self._drag = (e.x, e.y, self.center)

    def _move(self, e):
        if not self._drag:
            return
        if self.plan_mode.get() and self.plan_tool.get() == "avoid":  # drawing a circle, not moving the map
            x0, y0, _ = self._drag
            self._circle_preview = (*self._to_map(x0, y0), ((e.x - x0) ** 2 + (e.y - y0) ** 2) ** 0.5 / self.zoom)
            self._changed()
            return
        x0, y0, (cx, cy) = self._drag
        self.follow.set(False)
        self.center = (cx - (e.x - x0) / self.zoom, cy - (e.y - y0) / self.zoom)
        self._interact()
        self._changed()

    def _changed(self):
        self._last_key = None
        if getattr(self, "_in_render", True) or getattr(self, "_soon", 1) is not None:
            return  # drawing now, already asked, or still being built
        try:
            self._soon = self.win.after(1, self._render_soon)  # now, not at the next 300 ms tick
        except Exception:
            self._soon = None

    def _render_soon(self):
        self._soon = None
        try:
            if self._visible():
                self._render()
        except Exception:
            import logging

            logging.getLogger("chest.map").exception("map window draw failed")

    def _interact(self, kind: str = "drag"):
        """Dragging or zooming: draw quick drafts until the mouse has been still for a moment."""
        self._quick_until = time.perf_counter() + 0.25
        self._quick_kind = kind
        try:
            if self._settle_after is not None:
                self.win.after_cancel(self._settle_after)
            self._settle_after = self.win.after(300, self._settled)
        except Exception:
            self._settle_after = None

    def _settled(self):
        self._settle_after = None
        self._changed()  # the sharp picture

    def _quick(self) -> bool:
        return not self.playing and time.perf_counter() < self._quick_until

    def _kind_key(self) -> str:
        return next((k for k, v in HEAT_KINDS.items() if v == self.kind.get()), "routes")

    # ---- the loop ------------------------------------------------------------------------------
    def _tick(self):
        try:
            if not self.win.winfo_exists():
                return  # the host destroyed this map (e.g. the app rebuilt its window): stop quietly
        except Exception:
            return
        try:
            now = time.perf_counter()
            length = self._timeline_length() if self.playing else None
            if self.playing and length is not None:
                self.t = (self.t or 0.0) + (now - self._last_tick) * SPEEDS.get(self.speed.get(), 5)
                if self.t >= length:
                    self.t, self.playing = None, False
            self._last_tick = now
            if self._visible():  # a hidden map (another page, window closed) does no work at all
                self._refresh_choices()
                done = self._layer_done
                if done is not None and done[0] != self._layer_key:
                    self._changed()  # a heatmap finished drawing in the background
                self._render()
        except Exception:
            import logging

            logging.getLogger("chest.map").exception("map window draw failed")
        try:
            # playing: aim for 30 fps (leaves the window free for clicks), less the time this frame took;
            # the clock, not the timer, sets the speed
            spent = (time.perf_counter() - self._last_tick) * 1000
            self.win.after(max(1, int(33 - spent)) if self.playing else 300, self._tick)
        except Exception:
            pass  # destroyed while drawing

    @staticmethod
    def _set_values(box, values):
        if list(box["values"]) != values:
            box["values"] = values

    def _refresh_choices(self):
        heat = self.view.get() == "heat"
        if heat != self._was_heat:
            self._was_heat = heat
            self._layout_bar()
        all_recs = self.m.records()
        mine = self._my_names()
        pois = getattr(self.m, "pois", lambda: [])()
        key = (len(all_recs), max((r.get("start", 0) for r in all_recs), default=0), tuple(sorted(mine)), len(pois),
               getattr(self.m, "totals_version", lambda: 0)(),
               tuple(sorted(self._known_squad())))
        if key == self._choices_key:
            return  # nothing the lists depend on changed (this ran every frame: about 12 ms each)
        self._choices_key = key
        recs = sorted(all_recs, key=lambda r: -r.get("start", 0))
        seasons = sorted({r.get("season") for r in recs if r.get("season")}, reverse=True)
        self._set_values(self.season_box, ["All seasons"] + [f"Season {s}" for s in seasons])
        counts: dict[str, int] = {}
        for r in recs:
            for p in r["map"].get("players", []):
                n = p.get("name")
                if n and n.lower() not in mine and not self._is_random(n):  # squad only, never random fills
                    counts[n] = counts.get(n, 0) + 1
        mates = [n for n, c in sorted(counts.items(), key=lambda kv: -kv[1])][:12]
        self._set_values(self.who_box, ["Me", "Squad"] + mates)
        self._set_values(self.mate_box, [ANYONE] + mates)
        self._places = {f"Landed at {n}": (x, y) for n, x, y in pois}
        self._set_values(self.place_box, [ANYWHERE] + list(self._places))
        choices = {LIVE: ""}
        for r in recs[:200]:
            me = next((p for p in r["map"].get("players", []) if p.get("recorder")), None)
            n = chests_so_far(r, me.get("name"))[0] if me else 0  # the replay log's number when known
            label = f"{time.strftime('%b %d %H:%M', time.localtime(r.get('start') or 0))} - {n} chests, {fmt_time(r.get('length', 0))}"
            choices[label] = r["file"]
        if list(choices) != list(self._match_choices):
            self._match_choices = choices
            self.match_box["values"] = list(choices)

    def _my_names(self) -> set[str]:
        names = set()
        try:
            names = {n.lower() for n in self.app.replays.my_names()}
        except Exception:
            pass
        names |= {str(n).strip().lower() for n in self.app.cfg.get("fortnite_names") or []}
        return names

    def _load_base(self):
        from PIL import Image, ImageEnhance

        path = getattr(self.m, "display_path", None) or self.m.map_path
        if path is None or path == self._base_path:
            return
        img = Image.open(path).convert("RGB")
        self._base = ImageEnhance.Brightness(img).enhance(0.82)  # a bit darker so the marks stand out
        self._base_path = path

    @staticmethod
    def _length(rec) -> float:
        ends = [p["route"][-1][0] for p in rec["map"].get("players", []) if p.get("route")]
        return max([rec.get("length") or 0.0] + ends) or 1.0

    # ---- drawing helpers (coordinates and sizes are already multiplied by K) -------------------------
    def _line(self, d, pts, col, width, alpha=255, rounds=2):
        if len(pts) < 2:
            return
        pts = smooth(pts, rounds) if rounds else pts
        wd = max(1, round(width * K))
        # rounded joints at every point are costly; while playing back the dense smoothed points are enough
        d.line(pts, fill=col + (alpha,), width=wd, joint=None if self.playing else "curve")
        r = wd / 2
        for x, y in (pts[0], pts[-1]):  # round ends
            d.ellipse((x - r, y - r, x + r, y + r), fill=col + (alpha,))

    @staticmethod
    def _dashed(d, a, b, col, width, alpha=210, dash=9, gap=7):
        (x0, y0), (x1, y1) = a, b
        length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if length < 1:
            return
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        pos, wd = 0.0, max(1, round(width * K))
        while pos < length:
            end = min(length, pos + dash * K)
            d.line((x0 + ux * pos, y0 + uy * pos, x0 + ux * end, y0 + uy * end), fill=col + (alpha,), width=wd)
            pos = end + gap * K

    @staticmethod
    def _dot(d, x, y, r, fill, alpha=255, outline=DARK, ow=1.5):
        r *= K
        o = ow * K
        if outline is not None:
            d.ellipse((x - r - o, y - r - o, x + r + o, y + r + o), fill=outline + (alpha,))
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill + (alpha,))

    @staticmethod
    def _xmark(d, x, y, s, col, width, alpha=255):
        s *= K
        wd = max(1, round(width * K))
        d.line((x - s, y - s, x + s, y + s), fill=DARK + (alpha,), width=wd + 2 * K)
        d.line((x - s, y + s, x + s, y - s), fill=DARK + (alpha,), width=wd + 2 * K)
        d.line((x - s, y - s, x + s, y + s), fill=col + (alpha,), width=wd)
        d.line((x - s, y + s, x + s, y - s), fill=col + (alpha,), width=wd)

    def _text(self, d, x, y, text, col=(255, 255, 255), alpha=255):
        if not text:
            return
        l, t, r, b = d.textbbox((x, y), text, font=self._font)
        for _ in range(6):  # step down past labels already drawn here
            hit = next((o for o in self._labels if l < o[2] and o[0] < r and t < o[3] and o[1] < b), None)
            if hit is None:
                break
            shift = hit[3] - t + 2 * K
            y, t, b = y + shift, t + shift, b + shift
        self._labels.append((l, t, r, b))
        d.text((x, y), text, fill=col + (alpha,), font=self._font, stroke_width=K, stroke_fill=DARK + (alpha,))

    def _nametag(self, d, x, bottom, text, col=None, alpha=255, below=None):
        """A name in a small dark pill centred on x, its bottom edge at `bottom` (so it sits above a marker),
        outlined in the player's colour. If that spot is taken by another tag or marker and `below` is given,
        it goes under the marker instead (top edge at `below`); otherwise it steps up past what's there."""
        if not text:
            return
        font = self._tag_fonts[K]
        tb = d.textbbox((0, 0), text, font=font, anchor="mm")
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        pw, ph = tw + 12 * K, th + 6 * K
        l, t, r, b = x - pw / 2, bottom - ph, x + pw / 2, bottom

        def taken(t, b):
            return next((o for o in self._labels if l < o[2] and o[0] < r and t < o[3] and o[1] < b), None)
        if below is not None and taken(t, b) and not taken(below, below + ph):
            t, b = below, below + ph
        for _ in range(6):  # step up past labels already drawn here (stays above the marker)
            hit = next((o for o in self._labels if l < o[2] and o[0] < r and t < o[3] and o[1] < b), None)
            if hit is None:
                break
            shift = b - (hit[1] - 2 * K)
            t, b = t - shift, b - shift
        self._labels.append((l, t, r, b))
        fade = alpha / 255
        d.rounded_rectangle((l, t, r, b), radius=ph / 2, fill=DARK + (round(190 * fade),),
                            outline=(col or (255, 255, 255)) + (round((230 if col else 60) * fade),),
                            width=max(1, round(1.5 * K)) if col else 1)
        d.text((x, (t + b) / 2), text, fill=(255, 255, 255, alpha), font=font, anchor="mm")

    def _player(self, d, x, y, col, heading=None, name=None, alpha=255):
        """A player marker like the in-game minimap's: an arrowhead in the player's colour with a white
        border and a dark edge, pointing where they're going (north when that isn't known)."""
        if heading is None:
            heading = -math.pi / 2
        ca, sa = math.cos(heading), math.sin(heading)

        def dart(grow):
            # (forward, side) in px: tip, back corners and the notch between them, centred on the player
            pts = ((13, 0), (-9, 9.5), (-4, 0), (-9, -9.5))
            return [(x + (f * ca - sd * sa) * grow * K, y + (f * sa + sd * ca) * grow * K) for f, sd in pts]
        d.polygon(dart(1.55), fill=DARK + (round(alpha * 0.85),))
        d.polygon(dart(1.36), fill=(255, 255, 255, alpha))
        d.polygon(dart(1.0), fill=col + (alpha,))
        self._labels.append((x - 18 * K, y - 18 * K, x + 18 * K, y + 18 * K))  # name tags keep off the marker
        if name:  # above the marker (under it when something else is there)
            self._nametag(d, x, y - 23 * K, name, col, alpha, below=y + 23 * K)

    # ---- drawing ---------------------------------------------------------------------------------
    def _render(self):
        self._in_render = True
        try:
            self._render_frame()
        finally:
            self._in_render = False

    def _render_frame(self):
        from PIL import Image, ImageDraw, ImageTk

        planning = self.plan_mode.get() and self.view.get() == "match" and not self.simple
        if planning != bool(self.plan_bar.winfo_manager()):
            if planning:
                self.plan_bar.pack(side="top", fill="x", after=self.bar2)
            else:
                self.plan_bar.pack_forget()

        global K
        # playing back or dragging: draw at 1x for speed (smooth motion matters more); still: 2x for smooth edges
        K = 1 if self.playing or self._quick() else 2
        self._font = self._fonts[K]

        self._load_base()
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 20 or h < 20 or not self._visible():
            return
        view = self.view.get()
        live, rec = self._match_data() if view == "match" else (None, None)
        if rec is not self._shown_rec:
            self._shown_rec = rec
            if rec is None:
                self.t, self.playing = None, False
        self._update_timeline(rec)
        if self.follow.get() and view == "match":
            pos = self._my_pos(live, rec)
            if pos:
                self.center = pos
        self._clamp_view(w, h)  # following someone near the edge, a resize: never show past the picture
        self._update_panel(rec if view == "match" and not self.compare_files else None)
        key = (w, h, view, self.m.version, round(self.zoom, 4), self.center, self.show_spots.get(),
               self._heat_filters(), self._base_path, self.match_box.get(), self.route_chests.get(),
               None if self.t is None else round(self.t, 2), getattr(self.m, "backlog", 0),
               tuple(self.compare_files), id(self.loot), self.align.get(),
               getattr(self.m, "totals_version", lambda: 0)(), getattr(self.m, "camera_note", lambda: "")())
        if key == self._last_key:
            return
        self._last_key = key
        z = self.zoom
        x0, y0 = self.center[0] - w / 2 / z, self.center[1] - h / 2 / z
        x1, y1 = x0 + w / z, y0 + h / z
        if self._base is None:
            img = Image.new("RGB", (w, h), self._bg)
            ImageDraw.Draw(img).text((12, 12), self.m.status or "loading the map...", fill=(220, 220, 240))
            img = img.convert("RGBA")
        else:
            img, x0, y0 = self._base_view(w, h, x0, y0, view, rec)
            x1, y1 = x0 + w / z, y0 + h / z
        overlay = Image.new("RGBA", (w * K, h * K), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        self._labels = []
        self._legend = []  # filled by the drawing below (see legend_items)

        def S(x, y):
            return (x - x0) * z * K, (y - y0) * z * K

        if view == "heat":
            info = self._draw_heat(img, d, S, (x0, y0, x1, y1), w, h)
        elif self.compare_files:
            info = self._draw_compare(d, S)
        elif self.plan_mode.get():  # planning: just the chest spots (in the base picture) and the plan
            info = "" if self.loot else "Click a landing spot on the map to plan a loot path round the outside."
            if not self.loot:
                self._draw_marks(d, S)  # (with a plan, _draw_loot draws them above the middle's disc)
        else:
            info = self._draw_match(d, S, live, rec)
        if self.loot:
            info = (self._draw_loot(d, S) + "   " + info).strip()
        img.alpha_composite(overlay.reduce(K) if K > 1 else overlay)  # 2x2 box average: smooth edges, cheap
        self._last_image = img
        self.render_count += 1
        legend_now = tuple(self._legend)
        if legend_now != self._legend_seen:  # only when what the legend lists changes
            self._legend_seen = legend_now
            self.legend_version += 1
        backlog = getattr(self.m, "backlog", 0)
        if backlog:
            info += f"   (reading old matches for the map: {backlog} left)"
        shown = img.convert("RGB")  # Tk shows RGB faster than RGBA
        if self._show_direct(shown):
            pass
        elif self._photo is not None and (self._photo.width(), self._photo.height()) == img.size:
            self._photo.paste(shown)  # same size: update the shown picture in place
        else:
            self._photo = ImageTk.PhotoImage(shown)
            self.canvas.itemconfigure(self._img_id, image=self._photo)
        if self.status.cget("text") != info:
            self.status.configure(text=info)

    def _show_direct(self, shown) -> bool:
        """Windows: puts the frame straight onto the canvas. False: not available here, use the Tk photo."""
        if not self._dib_ok:
            return False
        try:
            from PIL import ImageWin

            if self._dib is None or self._dib.size != shown.size:
                self._dib = ImageWin.Dib("RGB", shown.size)
            self._dib.paste(shown)
            self._dib.expose(ImageWin.HWND(self.canvas.winfo_id()))
            if self._photo is not None:  # (was using the photo: drop it so Tk doesn't paint the old frame)
                self.canvas.itemconfigure(self._img_id, image="")
                self._photo = None
            return True
        except Exception:
            import logging

            logging.getLogger("chest.map").exception("drawing straight onto the map failed; using a Tk image")
            self._dib_ok, self._dib = False, None
            self._last_key = None
            return False

    def _exposed(self, e=None):
        """Tk repainted the canvas (uncovered, moved, resized): put the last frame back."""
        if self._dib is not None and self._dib_ok:
            try:
                self.win.after_idle(self._reexpose)
            except Exception:
                pass

    def _reexpose(self):
        try:
            from PIL import ImageWin

            if self._dib is not None:
                self._dib.expose(ImageWin.HWND(self.canvas.winfo_id()))
        except Exception:
            pass

    def _update_timeline(self, rec):
        length = self._timeline_length()
        if length is None or not self.show_timeline:
            if self.timeline.winfo_manager():
                self.timeline.pack_forget()
            if length is not None:  # a host draws its own timeline: keep the events for next_event()
                self._events = [] if self.compare_files or rec is None else match_events(rec)
                if rec is not None and not self.compare_files and (self._watched(rec) is None or not self.watch_var.get()):
                    self.watch_var.set(f"Watch: {self._default_watch(rec)}")
            return
        if not self.timeline.winfo_ismapped():
            self.timeline.pack(side="bottom", fill="x", before=self.canvas)
        if bool(self.compare_files) != self.align_box.winfo_ismapped():
            if self.compare_files:
                self.align_box.pack(side="left", padx=4, before=self.slider_box)
            else:
                self.align_box.pack_forget()
        single = rec is not None and not self.compare_files
        if single:
            names = [f"Watch: {n}" for n in self._player_names(rec)]
            if list(self.watch_box["values"]) != names:
                self.watch_box["values"] = names
            if self._watched(rec) is None or not self.watch_var.get():
                self.watch_var.set(f"Watch: {self._default_watch(rec)}")
        if single != self.watch_box.winfo_ismapped():
            if single:
                self.watch_box.pack(side="left", padx=4, before=self.slider_box)
            else:
                self.watch_box.pack_forget()
        events = [] if self.compare_files or rec is None else match_events(rec)
        ekey = (id(rec), len(events), self.ticks.winfo_width(), length)
        if ekey != getattr(self, "_ticks_key", None):
            self._ticks_key = ekey
            self._events = events
            self._draw_ticks(length)
        t = length if self.t is None else self.t
        self._setting_slider = True
        self.slider.set(t / length)
        self._setting_slider = False
        self.time_label.configure(text=f"{fmt_time(t)} / {fmt_time(length)}")
        self.play_btn.configure(text="Pause" if self.playing else "Play")

    def _match_data(self):
        """(live record, replay record) for the Match view: a picked past match, or the latest one."""
        picked = self._match_choices.get(self.match_box.get(), "")
        if picked:
            with self.m.lock:
                return None, self.m.replay_maps.get(picked)
        try:
            in_match = bool(self.app.match_in_progress())
        except Exception:
            in_match = False
        if in_match and self.m.live is not None:
            # this match, even with no position of my own yet (or none at all: a minimap that doesn't match):
            # the squad's live positions are drawn only here, never over the previous game
            return self.m.live, None
        live = self.m.live if self.m.live and (self.m.live["route"] or self.m.live["chests"]) else self.m.last_live
        rec = self.m.latest_replay_for_live(live) if live else None
        if in_match and live is self.m.live:
            rec = None  # a replay of this match can't exist yet; never show an older one
        if live is None:
            recs = self.m.records()
            rec = max(recs, key=lambda r: r.get("start", 0), default=None)
        return live, rec

    def _my_pos(self, live, rec):
        if rec is None and live and live["route"]:
            return live["route"][-1][1], live["route"][-1][2]
        if rec:
            me = self._watched(rec) or next((p for p in rec["map"].get("players", []) if p.get("recorder")), None)
            if me and me.get("route"):
                pt = position_at(me["route"], self.t) if self.t is not None else me["route"][-1]
                if pt:
                    return world_to_map(pt[1], pt[2], self.m.tf)
        return None

    def _draw_spots(self, d, S, looted: set, r: float):
        tf = self.m.tf
        if self._spots_in_base:  # the dots are already in the map picture: only ring the looted ones
            spots = self.m.spots.spots(self._shown_rec.get("season") if self._shown_rec else self.m.season())
            for sid in looted:
                if sid in spots:
                    x, y = S(*world_to_map(spots[sid]["x"], spots[sid]["y"], tf))
                    rr = (r + 3) * K
                    d.ellipse((x - rr, y - rr, x + rr, y + rr), outline=LOOTED + (255,), width=2 * K)
            return
        season = self._shown_rec.get("season") if self._shown_rec else self.m.season()
        spots = self.m.spots.spots(season)
        rates = self._hit_rates(season, spots)
        for sid, s in spots.items():
            x, y = S(*world_to_map(s["x"], s["y"], tf))
            if sid in looted:
                rr = (r + 3) * K
                d.ellipse((x - rr, y - rr, x + rr, y + rr), outline=LOOTED + (255,), width=2 * K)
            else:
                # how often a chest was really there: rarely = dimmer, always = bright (all easy to see)
                alpha = spot_alpha(*rates.get(sid, (0, 0)))
                rr = r * 1.3 * K
                d.ellipse((x - rr - K, y - rr - K, x + rr + K, y + rr + K), fill=DARK + (min(255, alpha + 30),))
                d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=SPOT + (alpha,))

    def _draw_match(self, d, S, live, rec) -> str:
        z = self.zoom
        r = max(4.0, min(8.0, 3.0 * z))
        T = self.t
        looted = set()
        if rec is not None:
            looted = {c.get("id") for c in rec["map"].get("chests", []) if c.get("id") and (T is None or c["t"] <= T)}
        elif live:
            looted = {c.get("spot") for c in live["chests"] if c.get("spot")}
        if rec is not None:
            self._draw_storm(d, S, rec, T)
        if self.show_spots.get():
            self._draw_spots(d, S, looted, max(1.5, r * 0.5))
        if rec is not None:
            return self._draw_replay(d, S, rec, r, self.m.tf, T)
        if not live:
            return self.m.status if self.m.status != "ready" else "No match yet. Your route shows here while you play."
        return self._draw_live(d, S, live, r)

    def _draw_live(self, d, S, live, r) -> str:
        with self.m.lock:
            team = [dict(p) for p in self.m.team.values()]
            route = list(live["route"])
            chests = list(live["chests"])
        now = time.time()
        for i, p in enumerate(team):  # squad first, me on top
            col = MATES[i % len(MATES)]
            fade = 255 if now - p.get("t", 0) < 15 else 120
            pts = [S(x, y) for x, y in p["route"]]
            self._line(d, pts, col, 4, fade)
            for cx, cy in p["chests"]:
                self._dot(d, *S(cx, cy), r, col, fade)
            if p.get("pos"):
                self._player(d, *S(*p["pos"]), col, heading_of(pts), p["name"], fade)
        # console squadmates (and anyone not sharing): from the replay being recorded, 0-20 s behind
        sharing = self.m.relay_names() if hasattr(self.m, "relay_names") else set()
        with self.m.lock:
            delayed = [dict(p) for p in getattr(self.m, "live_squad", {}).values()
                       if str(p.get("name") or "").strip().lower() not in sharing]
        for j, p in enumerate(delayed):
            col = RANDOM if self._is_random(p.get("name")) else MATES[(len(team) + j) % len(MATES)]
            pts = [S(x, y) for x, y in p["route"]]
            self._line(d, pts, col, 3.5, 190)
            age = max(0, int(now - p.get("t", now)))
            self._player(d, *S(*p["pos"]), col, heading_of(pts), f"{p['name']} ({age} s ago)", 215)
        piece, pieces = [], []
        for t, x, y, jump in route:
            if jump:
                pieces.append(piece)
                piece = []
            piece.append(S(x, y))
        pieces.append(piece)
        for pc in pieces:
            self._line(d, pc, ME, 5)
        for c in chests:
            self._dot(d, *S(c["x"], c["y"]), r + 1, ME)
        if route:
            self._player(d, *S(route[-1][1], route[-1][2]), ME, heading_of(pieces[-1]))
        me_name = (list(self.app.cfg.get("fortnite_names") or []) or [self.app.cfg.get("name") or "You"])[0]
        # The real count is the overlay's; marks are only the chests opened while the minimap gave a position
        mine = len(chests)
        try:
            if self.app.match_in_progress():
                mine = max(mine, int(getattr(self.app.stats, "game", 0) or 0))
        except Exception:
            pass
        marked = f" ({len(chests)} on the map)" if mine != len(chests) else ""
        self._legend = [(ME, f"{me_name}: {mine} chest{'s' * (mine != 1)}{marked}", "me")]
        for i, p in enumerate(team):
            n = max(len(p.get("chests") or []), int(p.get("game") or 0))
            stale = "" if now - p.get("t", 0) < 15 else f" (last seen {int(now - p.get('t', 0))} s ago)"
            self._legend.append((MATES[i % len(MATES)], f"{p.get('name') or 'teammate'}: {n} chest{'s' * (n != 1)}{stale}",
                                 "player"))
        for j, p in enumerate(delayed):
            n = p.get("chests")
            count = "" if n is None else f": {n} chest{'s' * (n != 1)}"
            age = max(0, int(now - p.get("t", now)))
            if self._is_random(p.get("name")):
                self._legend.append((RANDOM, f"{p['name']}{count} (random, not saved, {age} s ago)", "random"))
            else:
                self._legend.append((MATES[(len(team) + j) % len(MATES)], f"{p['name']}{count} (from the replay, {age} s ago)",
                                     "player"))
        age = now - route[-1][0] if route else None
        where = (" (no position from the minimap yet)" if age is None else
                 " (position from the minimap)" if age < 5 else f" (last seen {int(age)} s ago)")
        note = self.m.camera_note() if hasattr(self.m, "camera_note") else ""
        if note:  # the route only records my own character on the ground
            where = f" ({note}: route paused)"
        mates = f", {len(team)} teammate{'s' * (len(team) != 1)} sharing" if team else ""
        if delayed:
            mates += f", {len(delayed)} from the replay (up to 20 s behind)"
        elif self.app.cfg.get("console_teammates"):
            # Fortnite only writes the replay's key when the match ends, so the replay can't be read during
            # it: console teammates (no tracker to share their position) appear once the match is over
            mates += ", console teammates show after the match"
        return f"Live:{mine} chest{'s' * (mine != 1)}{where}{mates}"

    def _colors(self, players) -> dict:
        colors, mate = {}, 0
        for p in players:
            if p.get("recorder"):
                colors[p.get("name")] = ME
            else:
                colors[p.get("name")] = MATES[mate % len(MATES)]
                mate += 1
        return colors

    def _draw_chest(self, d, x, y, c, col, rr, alpha=255):
        if c.get("source") == "search":
            q = rr * K
            d.ellipse((x - q, y - q, x + q, y + q), outline=DARK + (alpha,), width=4 * K)
            d.ellipse((x - q, y - q, x + q, y + q), outline=col + (alpha,), width=2 * K)
        elif c.get("by_build"):
            q = (rr + 2) * K
            d.polygon([(x, y - q), (x - q, y + q * 0.8), (x + q, y + q * 0.8)], fill=col + (alpha,), outline=DARK + (alpha,))
        elif c.get("broken"):
            q = rr * K
            d.rectangle((x - q - 1.5 * K, y - q - 1.5 * K, x + q + 1.5 * K, y + q + 1.5 * K), fill=DARK + (alpha,))
            d.rectangle((x - q, y - q, x + q, y + q), fill=col + (alpha,))
        else:
            self._dot(d, x, y, rr + 1, col, alpha)

    def _draw_replay(self, d, S, rec, r, tf, T=None) -> str:
        """The replay as of T seconds into it (None: the whole match)."""
        players = rec["map"].get("players", [])
        colors = self._colors(players)
        watched = self._watched(rec)
        focus = watched.get("name") if watched else next((p.get("name") for p in players if p.get("recorder")), None)
        randoms = {p.get("name") for p in players if not p.get("recorder") and self._is_random(p.get("name"))}
        for name in randoms:
            colors[name] = RANDOM
        self._legend = []
        for p in sorted(players, key=lambda p: p.get("name") != focus):
            name = p.get("name") or "?"
            n = sum(chests_so_far(rec, p.get("name"), T))  # whole game: the replay log's; scrubbing: so far, capped
            label = f"{name}: {n} chest{'s' * (n != 1)}" + (" (random, not saved)" if name in randoms else "")
            kind = "watched" if name == focus else "random" if name in randoms else "player"
            self._legend.append((colors[p.get("name")], label, kind))
        for p in sorted(players, key=lambda p: p.get("name") == focus):  # the watched player last = on top
            col = colors[p.get("name")]
            width = 6 if p.get("name") == focus else 3.5
            route = p.get("route", [])
            cached_pieces, cached_jumps = self._replay_route(rec, p, tf)
            now_at = position_at(route, T) if T is not None else None
            pieces = []
            for piece in cached_pieces:  # smoothed once per match; each frame just cuts it at T
                if T is not None:
                    if piece[0][0] > T:
                        break
                    cut = [q for q in piece if q[0] <= T]
                    if now_at is not None and piece[-1][0] > T and now_at[3] <= 20000:
                        cut.append((T,) + world_to_map(now_at[1], now_at[2], tf))  # up to where they are now
                    piece = cut
                pieces.append([S(x, y) for _, x, y in piece])
            for t_from, a, b in cached_jumps:
                if T is None or t_from <= T:
                    self._dashed(d, S(*a), S(*b), col, 2.5)
            for pc in pieces:
                self._line(d, pc, col, width, rounds=0)
            if p.get("landing") and (T is None or p["landing"][0] <= T):
                x, y = S(*world_to_map(p["landing"][1], p["landing"][2], tf))
                q = 8 * K
                d.polygon([(x, y - q * 1.2), (x - q, y + q * 0.5), (x + q, y + q * 0.5)], fill=DARK + (255,))
                q = 6 * K
                d.polygon([(x, y - q * 1.1), (x - q * 0.8, y + q * 0.4), (x + q * 0.8, y + q * 0.4)], fill=col + (255,))
            for k in p.get("kills", []):
                k = kill_info(k)
                if T is not None and k["t"] > T:
                    continue
                kx, ky = S(*world_to_map(*k["at"], tf))
                if k["approx"]:  # a far-away teammate's late count: only roughly here
                    self._xmark(d, kx, ky, 4, KILL, 2, alpha=140)
                    continue
                vx, vy = S(*world_to_map(*(k["victim_at"] or k["at"]), tf))
                if k["victim_at"]:
                    d.line((kx, ky, vx, vy), fill=KILL + (210,), width=3 * K)
                fresh = T is not None and T - k["t"] < 4
                self._xmark(d, vx, vy, 9 if fresh else 6, KILL, 4 if fresh else 3)
                if fresh and k["victim"]:
                    self._nametag(d, vx, vy - 12 * K, k["victim"], KILL)
            # where they are at T (or where they ended); between recorded points they glide
            pt = position_at(route, T) if T is not None else (route[-1] if route else None)
            alive = T is not None and bool(route) and T <= route[-1][0] + 2
            if pt and pt[3] <= 20000:
                x, y = S(*world_to_map(pt[1], pt[2], tf))
                if alive:
                    self._player(d, x, y, col, heading_of(pieces[-1]) if pieces else None, p.get("name"))
                else:
                    self._xmark(d, x, y, 6, col, 3)
                    self._nametag(d, x, y - 10 * K, p.get("name"), col)
        for c in rec["map"].get("chests", []):  # one marker per chest / far-away search
            if T is not None and c["t"] > T:
                continue
            col = colors.get(c.get("who"), (200, 200, 200))
            x, y = S(*world_to_map(c["x"], c["y"], tf))
            self._draw_chest(d, x, y, c, col, r + 3 if T is not None and T - c["t"] < 3 else r)
        # counts: the replay log's whole-game numbers per player; while scrubbing, markers so far (capped)
        who = {c.get("who") for c in rec["map"].get("chests", [])} | {p.get("name") for p in players}
        per = [chests_so_far(rec, n, T) for n in who if n]
        opened, broken = sum(o for o, _ in per), sum(b for _, b in per)
        kills = sum(1 for p in players for k in p.get("kills", []) if T is None or kill_info(k)["t"] <= T)
        when = time.strftime("%b %d %H:%M", time.localtime(rec.get("start") or 0))
        moment = "" if T is None else f" at {fmt_time(T)}"
        key = (" Circle opened, square broken, triangle build break, ring teammate search far away, X elimination."
               if self.show_key else "")
        return f"Replay {when}{moment}: {opened} opened, {broken} broken, {kills} eliminations by the squad.{key}"

    # ---- heatmaps and route overlays ---------------------------------------------------------------
    def _heat_filters(self):
        return (self.kind.get(), self.who.get(), self.season.get(), self.period.get(), self.games.get(),
                self.place.get(), self.mate.get())

    def _picker(self):
        who = self.who.get()
        mine = self._my_names()

        def pick(name, recorder):
            n = (name or "").lower()
            if who == "Me":
                return n in mine or (recorder and not mine)
            if who == "Squad":  # me and my squad (tracker teammates, console teammates), not random fills
                return n in mine or bool(recorder) or not self._is_random(name)
            return name == who
        return pick

    def _season_key(self):
        season = self.season.get()
        return None if season == "All seasons" else season.replace("Season ", "")

    def _filter_args(self, ignore_place: bool = False) -> dict:
        """The filter settings as plain values (read here, on the Tk thread)."""
        mate = self.mate.get()
        return {"season": self._season_key(), "days": PERIODS.get(self.period.get()), "games": self.games.get(),
                "landed_at": None if ignore_place else self._places.get(self.place.get()),
                "with_player": None if mate == ANYONE else mate}

    def _filtered(self, ignore_place: bool = False):
        return filter_matches(self.m.records(), self._picker(), **self._filter_args(ignore_place))

    def _route_data(self, recs, pick=None, tf=None):
        """The chosen players' smoothed paths (map px) and chests in these matches, with colours.
        No Tk calls: it runs on a background thread (see _draw_heat)."""
        pick = pick or self._picker()
        tf = tf or self.m.tf
        paths, chests = [], []
        for r in recs:
            players = r["map"].get("players", [])
            colors = self._colors(players)
            for p in players:
                if not pick(p.get("name"), p.get("recorder")):
                    continue
                col = colors.get(p.get("name"), ME)
                # smoothed once per match and player, reused when only the filters change
                ckey = (r.get("file"), p.get("name"), tuple(sorted(tf.items())))
                lines = self._heat_routes.get(ckey)
                if lines is None:
                    lines = [smooth(line, 2) for line in route_lines([{"map": {"players": [p]}}], lambda n, rec: True, tf)]
                    self._heat_routes[ckey] = lines
                paths.extend((col, line) for line in lines)
            names = {p.get("name") for p in players if pick(p.get("name"), p.get("recorder"))}
            for c in r["map"].get("chests", []):
                if c.get("who") in names:
                    chests.append((colors.get(c.get("who"), ME), *world_to_map(c["x"], c["y"], tf)))
        return paths, chests

    @staticmethod
    def _paint_routes(paths, chests, with_chests: bool):
        """The routes overlay as pictures on the map's grid: "full" at 2 px per map px and "half" at 1, drawn
        once (in the background) so showing it is only a move and scale, however many routes there are."""
        from PIL import Image, ImageDraw

        scale = 2
        full = Image.new("RGBA", (MAP_SIZE * scale, MAP_SIZE * scale), (0, 0, 0, 0))
        d = ImageDraw.Draw(full)
        many = len(paths) > 30
        alpha, wd = (120, 5) if many else (190, 7)
        for col, line in paths:
            if len(line) > 1:
                d.line([(x * scale, y * scale) for x, y in line], fill=col + (alpha,), width=wd)
        if with_chests:
            r = 4.5
            for col, x, y in chests:
                x, y = x * scale, y * scale
                d.ellipse((x - r - 1.5, y - r - 1.5, x + r + 1.5, y + r + 1.5), fill=DARK + (230,))
                d.ellipse((x - r, y - r, x + r, y + r), fill=col + (235,))
        return {"full": full, "half": full.reduce(2)}

    def _draw_heat(self, img, d, S, box, w, h) -> str:
        from PIL import Image

        kind = self._kind_key()
        records = self.m.records()
        newest = max((r.get("start", 0) for r in records), default=0)
        key = (self._heat_filters(), len(records), newest, tuple(sorted(self.m.tf.items())),
               self.route_chests.get() if kind == "routes" else None)
        done = self._layer_done
        if done is not None and done[0] == key and key != self._layer_key:
            _, self._layer, self._layer_info, self._layer_matches, self._layer_kind = done
            self._layer_key = key
        if key != self._layer_key and key != self._layer_job:
            # filtering, binning and blurring take up to a second: do it in the background and keep
            # showing the previous layer until the new one is ready
            self._layer_job = key
            self._start_layer(key, kind, records, self._picker(), dict(self.m.tf), self._filter_args(),
                              self.route_chests.get())
        busy = key != self._layer_key
        if self._layer is not None and self._layer_kind == kind:
            layer, k = self._layer, 1
            if kind == "routes":  # zoomed out: the 1 px-per-map-px copy keeps thin lines from breaking up
                layer, k = (self._layer["half"], 1) if self.zoom < 1.2 else (self._layer["full"], 2)
            x0, y0, x1, y1 = box
            img.alpha_composite(layer.transform((w, h), Image.EXTENT, (x0 * k, y0 * k, x1 * k, y1 * k),
                                                resample=Image.BILINEAR, fillcolor=(0, 0, 0, 0)))
        if busy and (self._layer is None or self._layer_kind != kind):
            return f"{self.kind.get()} - drawing..."
        filters = [f for f in (self.period.get(), self.games.get(), self.place.get(), self.mate.get())
                   if f not in ("All time", "All games", ANYWHERE, ANYONE)]
        extra = f" ({', '.join(filters)})" if filters else ""
        note = "  (drawing...)" if busy else ""
        return (f"{self.kind.get()} - {self.who.get()}, {self.season.get()}{extra}: {self._layer_info} "
                f"from {self._layer_matches} matches{note}")

    def _start_layer(self, key, kind, records, pick, tf, args, with_chests=True):
        """Builds a heatmap / routes layer on a background thread; _draw_heat picks it up when done."""
        import threading

        def work():
            try:
                recs = filter_matches(records, pick, **args)
                if kind == "routes":
                    paths, chests = self._route_data(recs, pick, tf)
                    layer = self._paint_routes(paths, chests, with_chests)
                    n = sum(1 for r in recs if route_lines([r], pick, tf))
                    info = f"{n} routes, {len(chests)} chests"
                else:
                    pts = heat_points(recs, kind, pick, None, tf)
                    layer = render_heat(pts, blur_px=14 if kind in ("landing", "kills") else 8)
                    count = int(sum(p[2] for p in pts) / 60) if kind == "time" else len(pts)
                    info = f"{count} {'minutes' if kind == 'time' else 'spots'}"
                self._layer_done = (key, layer, info, len(recs), kind)
            except Exception:
                import logging

                logging.getLogger("chest.map").exception("heatmap")
                self._layer_job = None

        threading.Thread(target=work, daemon=True, name="map-heat").start()

    # ---- reviewing: browser, comparing, events, storm, stats, loot path, saving ---------------------
    def open_browser(self):
        if self._browser is None:
            from map_browser import MatchBrowser

            self._browser = MatchBrowser(self)
        self._browser.show()

    def set_simple(self, on: bool):
        """Simple mode: hides the three rows of controls (view and filters, the filter row, the buttons),
        leaving the map, the timeline, the status line and the stats panel. Off puts them back as they were."""
        on = bool(on)
        if on == self.simple:
            return
        self.simple = on
        rows = (self.bar, self.filters, self.bar2)
        if on:
            for row in rows:
                row.pack_forget()
        else:
            first = next((w for w in self.win.pack_slaves() if w not in rows), None)
            self.bar.pack(side="top", fill="x", **({"before": first} if first is not None else {}))
            self.filters.pack(side="top", fill="x", after=self.bar)
            self.bar2.pack(side="top", fill="x", after=self.filters)
            self._layout_bar()
        self._changed()

    def set_who(self, who: str):
        """Whose games the heatmaps, routes and match list show: "Me", "Squad" or a teammate's name."""
        self.who.set(who or "Me")
        self._picked()

    def show_view(self, view: str = "match", kind: str | None = None, match_file: str | None = None,
                  who: str | None = None, player: str | None = None):
        """Public way in for other windows: view "match" or "heat"; kind = a heatmap kind (a HEAT_KINDS key
        like "routes", or its label); match_file = a replay file name to show that match; who = "Me",
        "Squad" or a teammate's name."""
        if who:
            self.who.set(who)
        if match_file:
            self.show_match(match_file, player)
            return
        self.compare_files = []
        self.view.set("heat" if view == "heat" else "match")
        if kind:
            self.kind.set(HEAT_KINDS.get(kind, kind if kind in HEAT_KINDS.values() else self.kind.get()))
        if view != "heat":
            self.match_box.set(LIVE)
        self._layout_bar()
        self._picked()
        self.show()

    def show_match(self, file: str, player: str | None = None):
        """Shows one past match (by replay file name) with its timeline. player: whose match to watch
        (followed, drawn on top, stats first); default: the teammate picked under who, else you."""
        self.compare_files = []
        self.watch_var.set(f"Watch: {player}" if player else "")
        if player or self.who.get() not in ("Me", "Squad"):
            self.follow.set(True)
        self.view.set("match")
        label = next((k for k, v in self._match_choices.items() if v == file), None)
        if label is None:
            self._refresh_choices()
            label = next((k for k, v in self._match_choices.items() if v == file), LIVE)
        self.match_box.set(label)
        self._picked()
        self.show()

    def compare(self, files: list[str]):
        """Draws several matches at once (my route in each, in its own colour), with one timeline."""
        self.compare_files = [f for f in files if f in self.m.replay_maps][:len(COMPARE)]
        self.view.set("match")
        self.follow.set(False)
        self._picked()
        self.show()
        self.whole_island()

    def show_routes_from(self, place: str):
        """Every route from games that landed at a place (heatmap view, routes)."""
        label = f"Landed at {place}"
        self.place.set(label if label in self._places else "Landed anywhere")
        self.view.set("heat")
        self.kind.set(HEAT_KINDS["routes"])
        self._picked()
        self.show()

    def set_loot(self, path):
        self.loot = path
        if path:
            self.follow.set(False)
            sx, sy = world_to_map(*path["start"], self.m.tf)
            self.center = (sx, sy)
            self.zoom = max(self.zoom, 2.5)
        self._changed()

    def save_image(self):
        """Saves what the map shows to Pictures\\Chest Tracker."""
        import os
        from pathlib import Path

        if self._last_image is None:
            return
        folder = Path(os.environ.get("USERPROFILE") or Path.home()) / "Pictures" / "Chest Tracker"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / time.strftime("map %Y-%m-%d %H-%M-%S.png")
        self._last_image.convert("RGB").save(path)
        self.status.configure(text=f"Saved {path}")
        self._saved_note = (time.monotonic(), str(path))

    def jump(self, t: float):
        self.playing = False
        self.t = max(0.0, t)
        self._changed()

    def next_event(self, step: int):
        if not self._events:
            return
        now = self.t if self.t is not None else (self._timeline_length() or 0)
        times = sorted({round(e["t"], 1) for e in self._events})
        later = [t for t in times if t > now + 0.2] if step > 0 else [t for t in times if t < now - 0.2]
        if later:
            self.jump(later[0] if step > 0 else later[-1])

    def _draw_ticks(self, length: float):
        c = self.ticks
        c.delete("all")
        w = max(1, c.winfo_width())
        for e in self._events:
            x = 2 + (w - 4) * min(1.0, e["t"] / length)
            col = "#%02x%02x%02x" % EVENT_COLORS.get(e["kind"], (200, 200, 200))
            top = 0 if e["kind"] in ("kill", "storm", "landing") else 4
            c.create_line(x, top, x, 12, fill=col, width=2)

    def _tick_clicked(self, e):
        length = self._timeline_length()
        if not length or not self._events:
            return
        w = max(1, self.ticks.winfo_width())
        t = (e.x - 2) / max(1, w - 4) * length
        near = min(self._events, key=lambda ev: abs(ev["t"] - t))
        self.jump(near["t"] if abs(near["t"] - t) < length * 0.02 else t)

    def _event_clicked(self, _e):
        sel = self.events_list.curselection()
        if sel and sel[0] < len(self._events):
            self.jump(self._events[sel[0]]["t"])

    def _update_panel(self, rec):
        show = rec is not None and self.show_panel.get()
        if show != self.panel.winfo_ismapped():
            if show:
                self.panel.pack(side="right", fill="y", before=self.canvas)
            else:
                self.panel.pack_forget()
        if not show:
            return
        key = (id(rec), self.who.get(), self.watch_var.get())
        if key == self._panel_key:
            return
        self._panel_key = key
        pois = self.m.pois()
        lines = []
        watched = self._watched(rec)
        players = sorted(rec["map"].get("players", []), key=lambda p: p is not watched)
        for p in players:
            st = player_stats(rec, p, pois)
            first = "-" if st["first_chest"] is None else f"{st['first_chest']:.0f} s after landing"
            tags = ("  (you)" if p.get("recorder") else "") + ("  - watching" if p is watched else "")
            lines.append(f"{st['name']}{tags}\n"
                         f"  landed at {st['landed_at']}, alive {fmt_time(st['alive'])}\n"
                         f"  chests {st['opened']} opened, {st['broken']} broken ({st['per_min']:.1f}/min), first {first}\n"
                         f"  eliminations {st['kills']}\n"
                         f"  on foot {st['walk_m'] / 1000:.1f} km, fast {st['fast_m'] / 1000:.1f} km, "
                         f"swimming {st['swim_m']:.0f} m, in the air {st['air']:.0f} s")
        self.stats_label.configure(text="\n\n".join(lines))
        self._events = match_events(rec)
        self.events_list.delete(0, "end")
        for e in self._events:
            self.events_list.insert("end", f"{fmt_time(e['t'])}  {e['text']}")
            self.events_list.itemconfigure("end", foreground="#%02x%02x%02x" % EVENT_COLORS.get(e["kind"], (220, 220, 220)))

    def _hit_rates(self, season, spots) -> dict:
        key = (season, len(self.m.records()), len(spots))
        if self._rates[0] != key:
            recs = [r for r in self.m.records() if r.get("season") == season]
            self._rates = (key, spot_hit_rates(recs, spots))
        return self._rates[1]

    def _draw_storm(self, d, S, rec, T):
        """The storm at time T: purple outside the safe circle, the next circle in white."""
        storm = rec["map"].get("storm") or []
        if not storm:
            return
        t = T if T is not None else self._length(rec)
        z = storm_at(storm, t)
        if z is None:
            return
        tf = self.m.tf
        cx, cy, r, nx, ny, nr = z
        (x, y), rad = S(*world_to_map(cx, cy, tf)), r * tf["scale"] * self.zoom * K
        if T is None:  # the whole match: just where the last circle was, without tinting the map
            d.ellipse((x - rad, y - rad, x + rad, y + rad), outline=STORM + (230,), width=3 * K)
            return
        W, H = d.im.size
        d.rectangle((0, 0, W, H), fill=STORM + (70,))
        d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=(0, 0, 0, 0))  # clear the safe circle
        d.ellipse((x - rad, y - rad, x + rad, y + rad), outline=STORM + (230,), width=3 * K)
        (x, y), rad = S(*world_to_map(nx, ny, tf)), nr * tf["scale"] * self.zoom * K
        d.ellipse((x - rad, y - rad, x + rad, y + rad), outline=(255, 255, 255, 200), width=2 * K)

    def _compare_recs(self):
        return [self.m.replay_maps[f] for f in self.compare_files if f in self.m.replay_maps]

    def _compare_me(self, rec):
        pick = self._picker()
        return next((p for p in rec["map"].get("players", []) if pick(p.get("name"), p.get("recorder"))), None)

    def _compare_zero(self, rec) -> float:
        """Timeline zero of a match when comparing: its landing, or its start."""
        if self.align.get() == ALIGNS[1]:
            return 0.0
        me = self._compare_me(rec)
        return me["landing"][0] if me and me.get("landing") else 0.0

    def _compare_span(self, rec) -> float:
        me = self._compare_me(rec)
        end = me["route"][-1][0] if me and me.get("route") else self._length(rec)
        return max(1.0, end - self._compare_zero(rec))

    def _draw_compare(self, d, S) -> str:
        recs = self._compare_recs()
        tf = self.m.tf
        r = max(3.5, min(7.0, 2.6 * self.zoom))
        legend = []
        for i, rec in enumerate(recs):
            col = COMPARE[i % len(COMPARE)]
            me = self._compare_me(rec)
            if me is None:
                continue
            zero = self._compare_zero(rec)
            T = None if self.t is None else zero + self.t
            landed = me["landing"][0] if me.get("landing") else None
            pts = [S(*world_to_map(p[1], p[2], tf)) for p in me.get("route", [])
                   if (T is None or p[0] <= T) and (landed is None or p[0] >= landed) and p[3] <= 20000]
            self._line(d, pts, col, 4, 220)
            for c in rec["map"].get("chests", []):
                if c.get("who") == me.get("name") and (T is None or c["t"] <= T):
                    self._dot(d, *S(*world_to_map(c["x"], c["y"], tf)), r * 0.8, col, 235, ow=1.0)
            n = chests_so_far(rec, me.get("name"), T)[0]  # the replay log's number; scrubbing: so far, capped
            for k in me.get("kills", []):
                k = kill_info(k)
                if T is None or k["t"] <= T:
                    self._xmark(d, *S(*world_to_map(*(k["victim_at"] or k["at"]), tf)), 5, col, 3)
            if pts:
                self._player(d, *pts[-1], col, heading_of(pts))
            when = time.strftime("%b %d %H:%M", time.localtime(rec.get("start") or 0))
            legend.append((col, f"{when}: {n} chests"))
        self._legend = [(col, text, "game") for col, text in legend]
        # legend in the top-left corner (unless another window draws its own key)
        y = 8 * K
        for col, text in (legend if self.show_key else []):
            d.rounded_rectangle((8 * K, y + 3 * K, 22 * K, y + 15 * K), radius=3 * K, fill=col + (255,), outline=DARK + (255,))
            self._text(d, 28 * K, y, text)
            y += 20 * K
        how = "since landing" if self.align.get() == ALIGNS[0] else "since the match started"
        moment = "" if self.t is None else f" at {fmt_time(self.t)} {how}"
        hint = "" if getattr(self, "embedded", False) else " Pick a match in the list above to stop comparing."
        return f"Comparing {len(legend)} matches{moment}.{hint}"

    def _draw_loot(self, d, S) -> str:
        """The suggested loot path: numbered stops from the landing spot (with detours round the middle)."""
        path = self.loot
        tf = self.m.tf
        if path.get("inner"):  # the middle it stays out of
            cx, cy, r = path["inner"]
            (x, y), rad = S(*world_to_map(cx, cy, tf)), r * tf["scale"] * self.zoom * K
            d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=(0, 0, 0, 45), outline=(255, 255, 255, 120), width=2 * K)
        self._draw_marks(d, S)  # no-go circles above the middle's disc, below the route
        line = [S(*world_to_map(x, y, tf)) for x, y in path.get("path") or
                ([path["start"]] + [(x, y) for _, x, y in path["spots"]])]
        for a, b in zip(line, line[1:]):
            d.line((a, b), fill=DARK + (230,), width=6 * K)
        for a, b in zip(line, line[1:]):
            d.line((a, b), fill=(255, 255, 255, 235), width=3 * K)
        pts = [S(*world_to_map(*path["start"], tf))] + [S(*world_to_map(x, y, tf)) for _, x, y in path["spots"]]
        self._dot(d, *pts[0], 6, (255, 255, 255))
        for i, (x, y) in enumerate(pts[1:], 1):
            self._dot(d, x, y, 9, LOOTED)
            label = str(i)
            bx = d.textbbox((0, 0), label, font=self._font)
            d.text((x - (bx[2] - bx[0]) / 2, y - (bx[3] - bx[1]) / 2 - bx[1]), label, fill=DARK + (255,), font=self._font)
        for x, y in path.get("checkpoints") or []:  # checkpoints on top of the chest stops
            self._checkpoint_mark(d, *S(*world_to_map(x, y, tf)), path["checkpoints"].index((x, y)) + 1)
        if "chests" in path:
            mins = path["seconds"] / 60
            extras = []
            if path.get("checkpoints"):
                extras.append(f"{len(path['checkpoints'])} checkpoint{'s' * (len(path['checkpoints']) != 1)}")
            if path.get("avoid"):
                extras.append(f"{len(path['avoid'])} no-go circle{'s' * (len(path['avoid']) != 1)}")
            if path.get("max_per_place"):
                extras.append(f"at most {path['max_per_place']} per place")
            how = ", ".join(extras) if extras else "around the outside"
            return (f"Loot path from {path.get('place', '?')}: {len(path['spots'])} chests (about {path['chests']:.0f} "
                    f"usually there), {path['metres'] / 1000:.1f} km, about {mins:.0f} min, {how}."
                    + ("  Click another spot to try again." if self.plan_mode.get() else ""))
        return (f"Loot path at {path.get('place', '?')}: {len(path['spots'])} chests, {path['metres']:.0f} m, "
                f"about {path['seconds']:.0f} s.")

    # ---- watching one player in a replay -------------------------------------------------------------
    @staticmethod
    def _player_names(rec) -> list[str]:
        players = rec["map"].get("players", [])
        return [p.get("name") or "?" for p in sorted(players, key=lambda p: not p.get("recorder"))]

    def _default_watch(self, rec) -> str:
        """The teammate picked under who (if they're in this match), else whoever recorded it (you)."""
        names = self._player_names(rec)
        who = self.who.get()
        if who not in ("Me", "Squad") and who in names:
            return who
        return names[0] if names else ""

    def _watched(self, rec):
        name = self.watch_var.get().removeprefix("Watch: ")
        return next((p for p in rec["map"].get("players", []) if p.get("name") == name), None) if name else None

    def _watch_changed(self):
        self.follow.set(True)
        self._panel_key = None
        self._changed()

    # ---- for a host window: its own key and colours ----------------------------------------------------
    def set_key(self, on: bool):
        """Off: no compare legend on the map and no shape key in the status line (the host shows its own)."""
        self.show_key = bool(on)
        self._changed()

    def set_colors(self, bg: str | None = None, panel: str | None = None, text: str | None = None,
                   select: str | None = None):
        """Background colours only (hex like "#0c0806"): bg = window, map canvas, timeline ticks and the
        area outside the map picture; panel / text / select = the events list. Map marks keep their colours."""
        if bg:
            for widget in (self.win, self.canvas, self.ticks):
                try:
                    widget.configure(bg=bg)
                except Exception:
                    pass
            self._bg = tuple(int(bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        opts = {k: v for k, v in (("bg", panel), ("fg", text), ("selectbackground", select)) if v}
        if opts:
            self.events_list.configure(**opts)
        self._changed()

    def legend_items(self) -> list[tuple[tuple[int, int, int], str, str]]:
        """What the last picture tells apart by colour, for a host's own legend: (rgb, label, kind) with
        kind "game" (comparing; COMPARE order), "watched" (the player being watched, first) or "player"
        (others in a single replay), or on the live map "me" then "player" per teammate sharing their position.
        Empty for heatmaps. render_count goes up on every redraw."""
        return list(self._legend)

    # ---- fast drawing: cached base picture and routes --------------------------------------------------
    def _base_view(self, w, h, x0, y0, view, rec):
        """The map picture (with the chest spot dots) for the view at (x0, y0): cut from a cached picture
        of a bigger area, redrawn only when the zoom changes or the view leaves it. Returns the picture
        and the exact left/top (map px) it starts at."""
        from PIL import Image, ImageDraw

        z = self.zoom
        spots_key = None
        if self.show_spots.get():  # the chest spots toggle works on every view (fainter under a heatmap)
            if view == "match" and not self.compare_files and self._shown_rec:
                season = self._shown_rec.get("season")
            else:
                season = (self._season_key() if view == "heat" else None) or self.m.season()
            spots = self.m.spots.spots(season)
            spots_key = (season, len(spots), self._hit_rates(season, spots) is not None and len(self.m.records()),
                         view == "heat")
        quick = self._quick()
        key = (round(z, 5), self._base_path, self._bg, spots_key, w, h, quick)
        cache = self._base_cache
        if cache is not None and cache[0] == key:
            _, big, bx0, by0 = cache
            ox, oy = round((x0 - bx0) * z), round((y0 - by0) * z)
            if 0 <= ox and 0 <= oy and ox + w <= big.width and oy + h <= big.height:
                return big.crop((ox, oy, ox + w, oy + h)), bx0 + ox / z, by0 + oy / z
        # playing: draw an area twice the view's size, so following someone only moves the cut-out;
        # otherwise just a little bigger than the view, so zooming with the wheel stays quick
        # (zooming: just the view, every step needs a new picture anyway; dragging: room to move)
        grow = 2.0 if self.playing or (quick and self._quick_kind == "drag") else 1.0 if quick else 1.2
        bw, bh = int(w * grow), int(h * grow)
        bx0, by0 = x0 - (bw - w) / 2 / z, y0 - (bh - h) / 2 / z
        k = self._base.width / MAP_SIZE  # 2 with a sharper 4096 px picture
        big = self._base.transform((bw, bh), Image.EXTENT, (bx0 * k, by0 * k, (bx0 + bw / z) * k, (by0 + bh / z) * k),
                                   resample=Image.NEAREST if quick else Image.BILINEAR,  # (sharp once still)
                                   fillcolor=self._bg)
        self._spots_in_base = spots_key is not None
        faint = 0.6 if spots_key is not None and spots_key[-1] else 1.0
        if self._spots_in_base and quick:  # a draft: dots blended straight onto the picture, no extra layer
            d = ImageDraw.Draw(big, "RGBA")
            r = max(4.0, min(8.0, 3.0 * z)) * 0.65
            season = spots_key[0]
            spots = self.m.spots.spots(season)
            rates = self._hit_rates(season, spots)
            tf = self.m.tf
            for sid, sp in spots.items():
                mx, my = world_to_map(sp["x"], sp["y"], tf)
                x, y = (mx - bx0) * z, (my - by0) * z
                if -10 < x < bw + 10 and -10 < y < bh + 10:
                    d.ellipse((x - r, y - r, x + r, y + r),
                              fill=SPOT + (round(spot_alpha(*rates.get(sid, (0, 0))) * faint),), outline=DARK + (200,))
        big = big.convert("RGBA")
        if self._spots_in_base and not quick:
            dots = Image.new("RGBA", (bw * K, bh * K), (0, 0, 0, 0))
            d = ImageDraw.Draw(dots)
            r = max(4.0, min(8.0, 3.0 * z)) * 0.65 * K
            season = spots_key[0]
            spots = self.m.spots.spots(season)
            rates = self._hit_rates(season, spots)
            tf = self.m.tf
            for sid, sp in spots.items():
                mx, my = world_to_map(sp["x"], sp["y"], tf)
                x, y = (mx - bx0) * z * K, (my - by0) * z * K
                alpha = round(spot_alpha(*rates.get(sid, (0, 0))) * faint)
                # a thin dark edge so the dots show on sand and snow too
                d.ellipse((x - r - K, y - r - K, x + r + K, y + r + K), fill=DARK + (min(255, alpha + 30),))
                d.ellipse((x - r, y - r, x + r, y + r), fill=SPOT + (alpha,))
            big.alpha_composite(dots.reduce(K))
        self._base_cache = (key, big, bx0, by0)
        ox, oy = round((x0 - bx0) * z), round((y0 - by0) * z)
        return big.crop((ox, oy, ox + w, oy + h)), bx0 + ox / z, by0 + oy / z

    def _replay_route(self, rec, player, tf):
        """A player's route in a replay, smoothed once: pieces of (t, x, y) in map px (after landing, not the
        bus), and the dashed jumps across long gaps (vehicle rides). Cached per match and player."""
        key = (id(rec), player.get("name"), tuple(sorted(tf.items())))
        got = self._route_cache.get(key)
        if got is not None:
            return got
        landed = player["landing"][0] if player.get("landing") else None
        pieces, piece, prev, jumps = [], [], None, []
        for pt in player.get("route", []):
            if (landed is not None and pt[0] < landed) or pt[3] > 20000:  # the bus and the way down
                prev = None
                continue
            here = (pt[0],) + world_to_map(pt[1], pt[2], tf)
            # no movement recorded for a while: standing still (keep the line), or riding a vehicle
            # (the replay follows the vehicle, not you): a dashed line to where you got off
            if prev is not None and pt[0] - prev[0] > 6 and ((pt[1] - prev[1]) ** 2 + (pt[2] - prev[2]) ** 2) ** 0.5 > 3000:
                if piece:
                    pieces.append(piece)
                    jumps.append((pt[0], piece[-1][1:], here[1:]))
                piece = []
            piece.append(here)
            prev = pt
        if piece:
            pieces.append(piece)
        got = ([smooth_timed(pc, 2) for pc in pieces], jumps)
        if len(self._route_cache) > 64:
            self._route_cache.clear()
        self._route_cache[key] = got
        return got

    @staticmethod
    def _norm(name) -> str:
        import re

        return re.sub(r"\s+", "", str(name or "")).lower()

    def _known_squad(self) -> set[str]:
        """Squadmates by name (normalised): teammates with a tracker (relay or claimed names) and console
        teammates from settings."""
        known = {self._norm(c) for c in self.app.cfg.get("console_teammates") or []}
        try:
            known |= {self._norm(x) for x in self.app.team.claimed_names()}
        except Exception:
            pass
        try:
            known |= {self._norm(x) for x in self.m.relay_names()}
        except Exception:
            pass
        return known

    def _is_random(self, name) -> bool:
        """A random squad fill: not me, not a teammate with a tracker (relay or claimed names) and not a
        console teammate from settings. Shown on the map, never saved as a player or counted."""
        n = self._norm(name)
        if not n or n in {self._norm(x) for x in self._my_names()}:
            return False
        return n not in self._known_squad()

    def squad_member(self, name) -> bool:
        """For lists of players to pick: a squadmate (tracker or console teammate), not me, not a random fill."""
        n = self._norm(name)
        return bool(n) and n not in {self._norm(x) for x in self._my_names()} and n in self._known_squad()

    # ---- playback for a host window with its own controls ---------------------------------------------
    def set_timeline(self, on: bool):
        """Off: the timeline row (play, speed, watch, slider) is never shown; a host draws its own and drives
        playback with playback(), toggle_play(), seek(), set_speed(), watch() and next_event()."""
        self.show_timeline = bool(on)
        if not on and self.timeline.winfo_manager():
            self.timeline.pack_forget()
        self._changed()

    def playback(self) -> dict:
        """Where playback is: length (s, None when there's nothing to play), t (None = the end), playing,
        speed (label) and speeds, events (match_events; [] when comparing), players (names in the shown
        replay; [] when comparing), watched (name or None), compare (bool)."""
        rec = None if self.compare_files else self._shown_rec
        watched = self._watched(rec) if rec is not None else None
        return {"length": self._timeline_length(), "t": self.t, "playing": self.playing,
                "speed": self.speed.get(), "speeds": list(SPEEDS),
                "events": list(self._events) if rec is not None else [],
                "players": self._player_names(rec) if rec is not None else [],
                "watched": watched.get("name") if watched else None, "compare": bool(self.compare_files)}

    def seek(self, t: float):
        """Move to t seconds (clamped to the match); keeps playing if it was playing."""
        length = self._timeline_length()
        if length is None:
            return
        t = max(0.0, min(float(t), length))
        self.t = None if t >= length - 0.5 and not self.playing else t
        self._last_tick = time.perf_counter()
        self._changed()

    def set_speed(self, label: str):
        if label in SPEEDS:
            self.speed.set(label)

    def watch(self, name: str):
        """Follow this player in the shown replay (their route on top, their stats first)."""
        self.watch_var.set(f"Watch: {name}")
        self._watch_changed()

    # ---- drawing the planner marks -----------------------------------------------------------------------------
    def _draw_marks(self, d, S):
        """No-go circles (red), the circle being drawn, and checkpoints that have no plan drawn yet."""
        z = self.zoom
        circles = list(self.avoid_circles) + ([self._circle_preview] if self._circle_preview else [])
        for x, y, r in circles:
            (sx, sy), rad = S(x, y), r * z * K
            d.ellipse((sx - rad, sy - rad, sx + rad, sy + rad), fill=KILL + (95,), outline=(255, 235, 235, 200), width=5 * K)
            d.ellipse((sx - rad, sy - rad, sx + rad, sy + rad), outline=KILL + (240,), width=3 * K)
            d.line((sx - 6 * K, sy - 6 * K, sx + 6 * K, sy + 6 * K), fill=KILL + (220,), width=2 * K)
            d.line((sx - 6 * K, sy + 6 * K, sx + 6 * K, sy - 6 * K), fill=KILL + (220,), width=2 * K)
        if not (self.loot and self.loot.get("checkpoints")):
            for i, (x, y) in enumerate(self.checkpoints, 1):
                self._checkpoint_mark(d, *S(x, y), i)

    def _checkpoint_mark(self, d, x, y, n):
        """A checkpoint: a blue diamond with its number."""
        q = 12 * K
        d.polygon([(x, y - q - 2 * K), (x + q + 2 * K, y), (x, y + q + 2 * K), (x - q - 2 * K, y)], fill=DARK + (255,))
        d.polygon([(x, y - q), (x + q, y), (x, y + q), (x - q, y)], fill=(80, 170, 255, 255))
        label = str(n)
        bx = d.textbbox((0, 0), label, font=self._font)
        d.text((x - (bx[2] - bx[0]) / 2, y - (bx[3] - bx[1]) / 2 - bx[1]), label, fill=(255, 255, 255, 255), font=self._font,
               stroke_width=K, stroke_fill=DARK + (255,))
