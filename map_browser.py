"""
Match browser for the map window (map_window.py): every match the map has read, in two tabs.

  * Matches: date, where you landed, chests opened and broken, eliminations, time alive, squad.
    Click a column to sort. Double-click (or Open) shows the match on the map with its timeline;
    select several (Ctrl / Shift click) and Compare draws them all at once.
  * Drop spots: for every place you landed, games, average chests / eliminations / time alive and
    the best game. Show routes draws every game from there; Loot path suggests a short walk through
    the known chests around it.

It uses the map window's filters (player, season, period, games, played with), so the lists match
what the map shows.
"""
from __future__ import annotations

import time

from map_tracker import drop_report, loot_path, player_stats, spot_hit_rates

MATCH_COLS = [("date", "Date", 120), ("landed", "Landed at", 170), ("chests", "Chests", 60), ("broken", "Broken", 60),
              ("kills", "Elims", 50), ("alive", "Alive", 60), ("squad", "Squad", 220)]
DROP_COLS = [("place", "Landed at", 190), ("games", "Games", 60), ("chests", "Avg chests", 80), ("kills", "Avg elims", 75),
             ("alive", "Avg alive", 75), ("best", "Best game", 75)]


def fmt_min(s: float) -> str:
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


class MatchBrowser:
    def __init__(self, mw):
        import tkinter as tk
        from tkinter import ttk

        self.mw = mw
        app = mw.app
        w = self.win = tk.Toplevel(mw.win)
        w.title("Chest Tracker - matches")
        w.minsize(520, 300)
        geo = app.cfg.get("map_browser_geometry")
        w.geometry(geo if isinstance(geo, str) and mw._geometry_on_screen(geo) else "860x520")
        w.protocol("WM_DELETE_WINDOW", self.hide)
        w.bind("<Configure>", lambda e: e.widget is w and app.cfg.__setitem__("map_browser_geometry", w.geometry()))

        self.note = ttk.Notebook(w)
        self.note.pack(fill="both", expand=True, padx=6, pady=6)
        # matches
        f1 = ttk.Frame(self.note)
        self.note.add(f1, text="Matches")
        self.matches = self._table(f1, MATCH_COLS, "extended")
        self.matches.bind("<Double-1>", lambda e: self.open_selected())
        b = ttk.Frame(f1)
        b.pack(side="bottom", fill="x", pady=4)
        ttk.Button(b, text="Open on the map", command=self.open_selected).pack(side="left")
        ttk.Button(b, text="Compare selected", command=self.compare_selected).pack(side="left", padx=6)
        ttk.Button(b, text="Select top 10 by chests", command=self.select_top).pack(side="left")
        self.match_info = ttk.Label(b, foreground="#666")
        self.match_info.pack(side="right")
        # drop spots
        f2 = ttk.Frame(self.note)
        self.note.add(f2, text="Drop spots")
        self.drops = self._table(f2, DROP_COLS, "browse")
        self.drops.bind("<Double-1>", lambda e: self.show_routes())
        b2 = ttk.Frame(f2)
        b2.pack(side="bottom", fill="x", pady=4)
        ttk.Button(b2, text="Show routes from here", command=self.show_routes).pack(side="left")
        ttk.Button(b2, text="Suggested loot path", command=self.show_loot).pack(side="left", padx=6)
        ttk.Button(b2, text="Clear loot path", command=self.clear_loot).pack(side="left")
        ttk.Button(b2, text="Open best game", command=self.open_best).pack(side="left", padx=6)
        self.drop_info = ttk.Label(b2, foreground="#666")
        self.drop_info.pack(side="right")

        self._sort = {"matches": ("date", True), "drops": ("chests", True)}
        self._rows: dict[str, dict] = {}
        self._drops: dict[str, dict] = {}
        self._key = None
        self._tick()

    def _table(self, parent, cols, mode):
        from tkinter import ttk

        frame = ttk.Frame(parent)
        frame.pack(side="top", fill="both", expand=True)
        tv = ttk.Treeview(frame, columns=[c for c, _, _ in cols], show="headings", selectmode=mode)
        for c, title, width in cols:
            tv.heading(c, text=title, command=lambda c=c, tv=tv: self._sort_by(tv, c))
            tv.column(c, width=width, anchor="w" if c in ("date", "landed", "squad", "place") else "e")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return tv

    # ---- showing --------------------------------------------------------------------------------
    def show(self):
        self.win.deiconify()
        self.win.lift()
        self._key = None

    def hide(self):
        self.win.withdraw()

    def _tick(self):
        try:
            if self.win.state() == "normal":
                self.refresh()
        except Exception:
            import logging

            logging.getLogger("chest.map").exception("match browser")
        self.win.after(1000, self._tick)

    def refresh(self):
        mw = self.mw
        recs = mw._filtered(ignore_place=False)
        key = (len(mw.m.records()), tuple(r["file"] for r in recs), mw._heat_filters())
        if key == self._key:
            return
        self._key = key
        pois = mw.m.pois()
        pick = mw._picker()
        self._rows = {}
        for r in recs:
            players = [p for p in r["map"].get("players", []) if pick(p.get("name"), p.get("recorder"))]
            if not players:
                continue
            stats = [player_stats(r, p, pois) for p in players]
            me = stats[0]
            self._rows[r["file"]] = {
                "date": r.get("start") or 0, "landed": me["landed_at"],
                "chests": sum(s["opened"] for s in stats), "broken": sum(s["broken"] for s in stats),
                "kills": sum(s["kills"] for s in stats), "alive": max(s["alive"] for s in stats),
                "squad": ", ".join(p.get("name") or "?" for p in r["map"].get("players", [])),
            }
        self._fill("matches")
        n = len(self._rows)
        self.match_info.configure(text=f"{n} match{'es' * (n != 1)} with the map's filters")
        # drop spots: every place (the "landed at" filter would leave just one)
        drop_recs = mw._filtered(ignore_place=True)
        self._drops = {d["place"]: d for d in drop_report(drop_recs, pick, pois)}
        self._fill("drops")
        self.drop_info.configure(text=f"{len(self._drops)} places from {len(drop_recs)} matches")

    def _fill(self, which):
        tv = self.matches if which == "matches" else self.drops
        col, desc = self._sort[which]
        sel = set(tv.selection())
        tv.delete(*tv.get_children())
        if which == "matches":
            items = sorted(self._rows.items(), key=lambda kv: (kv[1][col] if col != "squad" else kv[1][col].lower()),
                           reverse=desc)
            for f, r in items:
                tv.insert("", "end", iid=f, values=(time.strftime("%b %d %H:%M", time.localtime(r["date"])), r["landed"],
                                                    r["chests"], r["broken"], r["kills"], fmt_min(r["alive"]), r["squad"]))
        else:
            field = {"place": "place", "games": "games", "chests": "avg_chests", "kills": "avg_kills",
                     "alive": "avg_alive", "best": "best"}[col]
            items = sorted(self._drops.items(), key=lambda kv: kv[1][field] if field != "place" else kv[0].lower(),
                           reverse=desc)
            for place, d in items:
                tv.insert("", "end", iid=place, values=(place, d["games"], f"{d['avg_chests']:.1f}", f"{d['avg_kills']:.1f}",
                                                        fmt_min(d["avg_alive"]), d["best"]))
        keep = [i for i in sel if tv.exists(i)]
        if keep:
            tv.selection_set(keep)

    def _sort_by(self, tv, col):
        which = "matches" if tv is self.matches else "drops"
        cur, desc = self._sort[which]
        self._sort[which] = (col, not desc if cur == col else col not in ("date", "landed", "squad", "place"))
        self._fill(which)

    # ---- actions --------------------------------------------------------------------------------
    def open_selected(self):
        sel = self.matches.selection()
        if sel:
            self.mw.show_match(sel[0])

    def compare_selected(self):
        sel = list(self.matches.selection())
        if len(sel) >= 2:
            self.mw.compare(sel)
        elif sel:
            self.mw.show_match(sel[0])

    def select_top(self):
        top = sorted(self._rows, key=lambda f: -self._rows[f]["chests"])[:10]
        self.matches.selection_set(top)

    def _drop(self):
        sel = self.drops.selection()
        return self._drops.get(sel[0]) if sel else None

    def show_routes(self):
        d = self._drop()
        if d is None:
            return
        self.mw.show_routes_from(d["place"])

    def open_best(self):
        d = self._drop()
        if d and d.get("best_file"):
            self.mw.show_match(d["best_file"])

    def show_loot(self):
        d = self._drop()
        if d is None or not d["landings"]:
            return
        pts = [p for p in d["landings"] if p]
        start = (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))  # where you usually land
        season = self.mw._season_key() or self.mw.m.season()
        spots = self.mw.m.spots.spots(season)
        rates = spot_hit_rates(self.mw.m.records(), spots)
        path = loot_path(spots, start, rates=rates)
        path["place"] = d["place"]
        self.mw.set_loot(path)

    def clear_loot(self):
        self.mw.set_loot(None)

