"""Map window: minimap matching, world/map conversion, chest spots, heatmaps, squad messages.

The matcher is tested on a made-up island (blurred noise with a flat sea), cut and scaled the way the
game draws its minimap, with the storm's purple tint and lines drawn over it. Real-match accuracy
(1.1 m median over 479 minimap captures, Nov 19, 2026) was measured with tools/capture_minimap.py.
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
import map_tracker as mt  # noqa: E402

_isolate.check(ct)

import cv2  # noqa: E402
import numpy as np  # noqa: E402


def fake_island(seed=1):
    """2048 px BGR 'map': textured land in the middle, flat sea around it."""
    rng = np.random.default_rng(seed)
    img = np.zeros((mt.MAP_SIZE, mt.MAP_SIZE, 3), np.float32)
    for sigma, amp in ((40, 90), (12, 50), (4, 30), (1.5, 15)):
        for c in range(3):
            n = cv2.GaussianBlur(rng.standard_normal((mt.MAP_SIZE, mt.MAP_SIZE)).astype(np.float32), (0, 0), sigma)
            img[:, :, c] += n / (n.std() + 1e-6) * amp
    img += np.array([90, 150, 120], np.float32)
    yy, xx = np.mgrid[0:mt.MAP_SIZE, 0:mt.MAP_SIZE]
    land = ((xx - 1024) ** 2 + (yy - 1024) ** 2) < 600 ** 2
    sea = np.array([170, 110, 30], np.float32)
    img[~land] = sea
    return np.clip(img, 0, 255).astype(np.uint8)


ISLAND = fake_island()


def minimap_at(x, y, win_h=1032, storm=0.0, lines=False):
    """What the game's minimap would show with the player at map (x, y), in a window win_h high."""
    half = mt.MINIMAP_MAP_PX / 2
    crop = ISLAND[int(round(y - half)):int(round(y + half)), int(round(x - half)):int(round(x + half))]
    s = round(mt.MM_SIZE * win_h)
    mm = cv2.resize(crop, (s, s), interpolation=cv2.INTER_AREA)
    if storm:
        purple = np.zeros_like(mm)
        purple[:] = (200, 40, 140)  # BGR
        cut = int(s * (1 - storm))
        mm[:, cut:] = cv2.addWeighted(mm[:, cut:], 0.35, purple[:, cut:], 0.65, 0)
    if lines:
        cv2.line(mm, (s // 2, s // 2), (5, s - 30), (255, 255, 255), 2)
        cv2.circle(mm, (s // 2, s // 2), s // 3, (255, 255, 255), 1)
    cv2.putText(mm, "PLACE NAME", (4, s - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return mm


MATCHER = mt.MinimapMatcher(ISLAND)


def corner_with_minimap(side, gap, top, at=(1000, 900), win_h=1080, seed=3):
    """The top-right corner of a win_h-high window (as find_minimap gets it): a dim, slightly busy game
    scene with the minimap (side px, gap px from the right edge, top px down) pasted in."""
    rh = round(mt.FIND_REGION * win_h)
    rng = np.random.default_rng(seed)
    noise = cv2.GaussianBlur(rng.standard_normal((rh, rh, 3)).astype(np.float32), (0, 0), 3) * 25
    region = np.clip(noise + np.array([110, 40, 90], np.float32), 0, 255).astype(np.uint8)  # purple-ish
    if side:
        half = mt.MINIMAP_MAP_PX / 2
        crop = ISLAND[int(at[1] - half):int(at[1] + half), int(at[0] - half):int(at[0] + half)]
        mm = cv2.resize(crop, (side, side), interpolation=cv2.INTER_AREA)
        cv2.putText(mm, "PLACE", (4, side - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        region[top:top + side, rh - gap - side:rh - gap] = mm
    return region


class FindMinimapTests(unittest.TestCase):
    """Fortnite's HUD scale changes the minimap's size: Nov 19, 2026, 251 px on one 1080 px high screen
    and 189 px on another (where the fixed box matched nothing: score 0.10, the right one 0.81)."""

    def test_finds_a_smaller_minimap_exactly(self):
        region = corner_with_minimap(189, 26, 26)
        l, t, s, score = mt.find_minimap(region, MATCHER)
        self.assertEqual((region.shape[1] - l - s, t, s), (26, 26, 189))
        self.assertGreater(score, 0.6)

    def test_finds_the_default_size_too(self):
        region = corner_with_minimap(252, 33, 32, at=(1150, 1100))
        l, t, s, _ = mt.find_minimap(region, MATCHER)
        self.assertEqual((region.shape[1] - l - s, t, s), (33, 32, 252))

    def test_nothing_found_without_a_minimap(self):
        self.assertIsNone(mt.find_minimap(corner_with_minimap(0, 0, 0), MATCHER))


class MinimapTests(unittest.TestCase):
    def test_island_box_finds_the_land(self):
        # the made-up island is a circle of radius 600 around (1024, 1024) in a flat sea
        x0, y0, x1, y1 = mt.island_box(ISLAND)
        for got, want in ((x0, 424), (y0, 424), (x1, 1624), (y1, 1624)):
            self.assertLess(abs(got - want), 40)  # a disc's tips are thin; the outermost 0.2% is trimmed
        shifted = np.roll(ISLAND, (100, 60), axis=(0, 1))  # art off centre: the box follows the land
        x0, y0, x1, y1 = mt.island_box(shifted)
        self.assertLess(abs((x0 + x1) / 2 - 1084), 15)
        self.assertLess(abs((y0 + y1) / 2 - 1124), 15)

    def test_minimap_box_matches_measured_screens(self):
        # measured: 2560x1032 -> x 2288, y 30, 239 px; 1280x720 -> x 1090, y 19, 168 px
        for (w, h), (x, y, s) in {(2560, 1032): (2288, 30, 239), (1280, 720): (1090, 19, 168)}.items():
            l, t, size = mt.minimap_box(w, h)
            self.assertLessEqual(abs(l - x), 3)
            self.assertLessEqual(abs(t - y), 3)
            self.assertLessEqual(abs(size - s), 2)

    def test_whole_map_search_finds_the_player(self):
        for (x, y), h in (((900, 1100), 1032), ((1250, 820), 720), ((1050, 1300), 1440)):
            fix = MATCHER.locate(minimap_at(x, y, win_h=h), None)
            self.assertTrue(fix.ok, fix)
            self.assertLess(np.hypot(fix.x - x, fix.y - y), 2.5, (x, y, fix))

    def test_storm_tint_and_lines(self):
        fix = MATCHER.locate(minimap_at(1100, 1000, storm=0.8, lines=True), None)
        self.assertTrue(fix.ok, fix)
        self.assertLess(np.hypot(fix.x - 1100, fix.y - 1000), 3)
        self.assertGreater(fix.storm, 0.4)

    def test_open_sea_is_not_trusted(self):
        fix = MATCHER.locate(minimap_at(200, 200), None)
        self.assertTrue(fix is None or not fix.ok, fix)

    def test_dark_menu_over_the_corner_is_ignored(self):
        # measured: Fortnite's settings menu over the minimap corner scored 0.63 on edges alone
        menu = (minimap_at(1000, 1000) * 0.3).astype(np.uint8)
        self.assertFalse(mt.minimap_visible(menu))
        self.assertIsNone(MATCHER.locate(menu, None))
        self.assertTrue(mt.minimap_visible(minimap_at(1000, 1000)))

    def test_tracker_holds_last_position_on_a_bad_frame(self):
        tr = mt.Tracker(MATCHER)
        tr.update(minimap_at(1000, 1000), 0.0)
        self.assertLess(np.hypot(tr.pos[0] - 1000, tr.pos[1] - 1000), 2.5)
        tr.update(minimap_at(1006, 1003), 1.0)  # local search
        self.assertLess(np.hypot(tr.pos[0] - 1006, tr.pos[1] - 1003), 2.5)
        flat = np.full_like(minimap_at(1000, 1000), 128)  # a menu or the full map in front
        fix = tr.update(flat, 2.0)
        self.assertTrue(fix is None or not fix.ok)
        self.assertLess(np.hypot(tr.pos[0] - 1006, tr.pos[1] - 1003), 2.5)

    def test_tracker_finds_a_far_jump_again(self):
        tr = mt.Tracker(MATCHER)
        tr.update(minimap_at(900, 900), 0.0)
        for i in range(1, 6):  # respawned far away: local search fails, then a whole-map search
            tr.update(minimap_at(1300, 1250), float(i))
        self.assertLess(np.hypot(tr.pos[0] - 1300, tr.pos[1] - 1250), 2.5)


class TransformTests(unittest.TestCase):
    def test_round_trip(self):
        x, y = mt.map_to_world(*mt.world_to_map(35983, 5196))
        self.assertAlmostEqual(x, 35983, places=3)
        self.assertAlmostEqual(y, 5196, places=3)

    def test_measured_point(self):
        # Nov 19 match: replay said (35983, 5196); the minimap match said map px (1211.2, 1051.6)
        px, py = mt.world_to_map(35983, 5196)
        self.assertLess(np.hypot(px - 1211.2, py - 1051.6), 1.5)

    def test_fit_recovers_transform_despite_outliers(self):
        rng = np.random.default_rng(3)
        tf = {"scale": 0.0071, "ox": 950.0, "oy": 1030.0}
        pairs = []
        for _ in range(200):
            X, Y = rng.uniform(-90000, 90000, 2)
            px, py = mt.world_to_map(X, Y, tf)
            pairs.append(((X, Y), (px + rng.normal(0, 0.7), py + rng.normal(0, 0.7))))
        for i in range(0, 200, 10):  # fast travel: screen and replay clocks disagree
            (X, Y), (px, py) = pairs[i]
            pairs[i] = ((X, Y), (px + 18, py - 12))
        got, residual, n = mt.fit_transform(pairs)
        self.assertAlmostEqual(got["scale"], 0.0071, places=5)
        self.assertLess(abs(got["ox"] - 950), 1.0)
        self.assertLess(abs(got["oy"] - 1030), 1.0)
        self.assertLess(residual, 1.5)

    def test_fit_from_match_finds_the_clock_offset(self):
        tf = mt.DEFAULT_TRANSFORM
        route = [[t, 20000 + 400 * t, -5000 + 150 * t, 300] for t in range(0, 200)]  # running east-southeast
        start = 1_800_000_000.0
        live = []
        for t in range(10, 190):
            X, Y = mt.route_lookup(route, t + 1.0)  # the screen is 1 s ahead of the replay clock
            px, py = mt.world_to_map(X, Y, tf)
            live.append((start + t, px, py))
        got, residual, n = mt.fit_from_match(live, start, route)
        self.assertLess(residual, 0.2)
        self.assertAlmostEqual(got["scale"], tf["scale"], places=6)

    def test_route_lookup(self):
        route = [[0, 0, 0, 0], [1, 100, 0, 0], [10, 900, 0, 0]]
        self.assertEqual(mt.route_lookup(route, 0.5), (50.0, 0.0))
        self.assertIsNone(mt.route_lookup(route, 5))   # a 9 s gap: unknown
        self.assertIsNone(mt.route_lookup(route, 20))  # after the end


class SpotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = mt.SpotDB(self.tmp / "spots.json")

    def test_season_of(self):
        self.assertEqual(mt.season_of("++Fortnite+Release-43.10"), "43")
        self.assertEqual(mt.season_of(None), "unknown")

    def test_opener_positions_average_and_actor_wins(self):
        self.db.add("43", "Tiered_Chest_UAID_1", "Tiered_Chest", 1000, 1000, "opener")
        self.db.add("43", "Tiered_Chest_UAID_1", "Tiered_Chest", 1200, 1000, "opener")
        self.db.add("43", "Tiered_Chest_UAID_1", "Tiered_Chest", 9000, 9000, "opener")  # far off: ignored
        s = self.db.spots("43")["Tiered_Chest_UAID_1"]
        self.assertEqual((s["x"], s["y"], s["n"]), (1100, 1000, 2))
        self.db.add("43", "Tiered_Chest_UAID_1", "Tiered_Chest", 1150, 990, "actor")
        self.db.add("43", "Tiered_Chest_UAID_1", "Tiered_Chest", 5000, 5000, "opener")  # exact position kept
        s = self.db.spots("43")["Tiered_Chest_UAID_1"]
        self.assertEqual((s["x"], s["y"], s["actor"]), (1150, 990, True))
        self.db.save()
        again = mt.SpotDB(self.tmp / "spots.json")
        self.assertIn("Tiered_Chest_UAID_1", again.spots("43"))

    def test_nearest_within_a_few_meters(self):
        self.db.add("43", "a", "Tiered_Chest", 0, 0, "actor")
        self.db.add("43", "b", "Tiered_Chest", 2000, 0, "actor")
        self.assertEqual(self.db.nearest("43", 300, 100)[0], "a")
        self.assertIsNone(self.db.nearest("43", 1000, 0, max_dist=600))
        self.assertIsNone(self.db.nearest("42", 0, 0))


def fake_record(season="43", landing=(0, 0)):
    route = [[t, 1000 * t, 0, 1000 if t > 2 else 60000] for t in range(0, 30)]
    return {"file": f"r{season}.replay", "start": 1.0e9, "length": 30, "season": season, "map": {
        "players": [{"name": "RunninRobin", "recorder": True, "route": route, "landing": [3, *landing],
                     "kills": [{"t": 10, "x": 5000, "y": 0, "vx": 5600, "vy": 300, "victim": "Enemy1"}],
                     "end": [29, 29000, 0]},
                    {"name": "Falcon", "recorder": False, "route": route, "landing": [4, 20000, 20000],
                     "kills": [], "end": [29, 29000, 0]}],
        "chests": [{"t": 5, "who": "RunninRobin", "id": "c1", "x": 5000, "y": 0, "source": "opener"},
                   {"t": 6, "who": "Falcon", "id": None, "x": 7000, "y": 0, "source": "search"}],
        "spots": []}}


class SearchChestTests(unittest.TestCase):
    """A teammate's far-away searches are real opens (only the spot is rough); a chest seen opened late that is
    one of their earlier searches counts once."""

    def test_searches_count_and_late_duplicates_drop(self):
        rec = fake_record()
        fyn = rec["map"]["players"][1]
        rec["map"]["chests"] = [
            {"t": 5, "who": "RunninRobin", "id": "c1", "x": 5000, "y": 0, "source": "opener"},
            {"t": 100, "who": "Falcon", "id": None, "x": 50000, "y": 0, "source": "search"},
            {"t": 200, "who": "Falcon", "id": None, "x": 60000, "y": 0, "source": "search"},
            {"t": 250, "who": "Falcon", "id": None, "x": 65000, "y": 0, "source": "search"},  # no chest data
            # chest at 50000,0 reaches the recorder at t=470, listed where Falcon stood then: the t=100 search
            {"t": 470, "who": "Falcon", "id": "far1", "x": 90000, "y": 0, "source": "opener"},
            # seen 2 s after the search at the same chest
            {"t": 202, "who": "Falcon", "id": "near2", "x": 60100, "y": 0, "source": "opener"},
            # a chest the search flag missed (spot unknown, no search just before): kept
            {"t": 300, "who": "Falcon", "id": "new3", "x": 70000, "y": 0, "source": "opener"},
            # late, not one of their searches: kept, moved to its known spot
            {"t": 471, "who": "Falcon", "id": "far4", "x": 90000, "y": 0, "source": "opener"},
        ]
        spots = {"far1": (50200, 0), "near2": {"x": 60100, "y": 0}, "far4": (80000, 0)}
        mt.tidy_chests(rec, spots)
        mine = [c for c in rec["map"]["chests"] if c["who"] == "Falcon"]
        self.assertEqual([(c["t"], c.get("id"), c["source"]) for c in mine],
                         [(100, "far1", "opener"), (200, "near2", "opener"), (250, None, "search"),
                          (300, "new3", "opener"), (471, "far4", "opener")])
        self.assertEqual((mine[0]["x"], mine[0].get("paired")), (50200, True))  # its time, its real spot
        self.assertEqual((mine[4]["x"], mine[4].get("late")), (80000, True))
        self.assertEqual(rec["_tidy"], 2)
        mt.tidy_chests(rec, spots)  # once only
        self.assertEqual(len([c for c in rec["map"]["chests"] if c["who"] == "Falcon"]), 5)
        self.assertEqual(mt.player_stats(rec, fyn)["opened"], 5)  # far-away searches count
        rows = [e["text"] for e in mt.match_events(rec) if e["kind"] == "chest" and e["who"] == "Falcon"]
        self.assertEqual(len(rows), 5)
        self.assertIn("far from the recorder", rows[2])
        everyone = lambda name, rec: True  # noqa: E731
        self.assertEqual(mt.match_summary(rec, everyone)["chests"], 6)
        self.assertEqual(mt.SAME_CHEST_M, 6.0)


class HeatmapTests(unittest.TestCase):
    def test_points_by_kind_player_and_season(self):
        recs = [fake_record("43"), fake_record("42", landing=(10000, 0))]
        me = lambda name, rec: bool(rec)  # noqa: E731
        squad = lambda name, rec: True  # noqa: E731
        self.assertEqual(len(mt.heat_points(recs, "landing", me, None)), 2)
        self.assertEqual(len(mt.heat_points(recs, "landing", squad, "43")), 2)
        kills = mt.heat_points(recs, "kills", me, "43")
        self.assertEqual(len(kills), 1)
        self.assertAlmostEqual(kills[0][0], mt.world_to_map(5600, 300)[0])  # the victim's spot
        recs[0]["map"]["players"][1]["kills"] = [{"t": 20, "x": 1, "y": 1, "vx": None, "vy": None, "victim": None,
                                                  "approx": True}]
        self.assertEqual(len(mt.heat_points(recs, "kills", squad, "43")), 1)  # batched teammate counts left out
        old_form = mt.kill_info([12.0, 100, 200])
        self.assertEqual((old_form["at"], old_form["victim_at"]), ((100, 200), None))
        self.assertEqual(len(mt.heat_points(recs, "chests", squad, "43")), 1)  # far-away searches: counted, but not in position heatmaps
        time_pts = mt.heat_points(recs, "time", me, "43")
        self.assertEqual(len(time_pts), 26)  # the bus part (high up) is left out
        img = mt.render_heat(mt.heat_points(recs, "time", squad, None))
        self.assertEqual(img.size, (mt.MAP_SIZE, mt.MAP_SIZE))
        self.assertEqual(img.mode, "RGBA")
        self.assertGreater(np.asarray(img)[:, :, 3].max(), 100)


class FilterTests(unittest.TestCase):
    def recs(self):
        out = []
        for i in range(10):
            r = fake_record()
            r["file"] = f"r{i}.replay"
            r["start"] = time.time() - i * 86400 * 3  # one every 3 days
            r["season"] = "43" if i < 6 else "42"
            r["map"]["chests"] = [{"t": 5, "who": "RunninRobin", "id": f"c{j}", "x": 0, "y": 0, "source": "opener"}
                                  for j in range(i)]  # match i: i chests
            if i % 2:
                r["map"]["players"] = r["map"]["players"][:1]  # Falcon only in even matches
            r["map"]["players"][0]["landing"] = [3, 0, 0] if i < 5 else [3, 90000, 0]
            out.append(r)
        return out

    def test_filters(self):
        me = lambda name, rec: bool(rec)  # noqa: E731
        recs = self.recs()
        self.assertEqual(len(mt.filter_matches(recs, me)), 10)
        self.assertEqual(len(mt.filter_matches(recs, me, season="43")), 6)
        self.assertEqual(len(mt.filter_matches(recs, me, days=7)), 3)
        top = mt.filter_matches(recs, me, games="Top 10% by chests")
        self.assertEqual([r["file"] for r in top], ["r9.replay"])
        bottom = mt.filter_matches(recs, me, games="Bottom 25% by chests")
        self.assertEqual({r["file"] for r in bottom}, {"r0.replay", "r1.replay"})  # round(10 * 0.25) = 2
        self.assertEqual(len(mt.filter_matches(recs, me, with_player="Falcon")), 5)
        self.assertEqual(len(mt.filter_matches(recs, me, landed_at=(1000, 0))), 5)  # within 300 m
        both = mt.filter_matches(recs, me, season="43", games="Top 25% by chests")  # ranked after the others
        self.assertEqual([r["file"] for r in both], ["r5.replay", "r4.replay"])

    def test_route_lines_and_smoothing(self):
        lines = mt.route_lines([fake_record()], lambda n, r: bool(r))
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0]), 27)  # from the landing (t=3) on
        pts = mt.smooth([(0, 0), (10, 0), (10, 10)], 2)
        self.assertEqual(pts[0], (0, 0))
        self.assertEqual(pts[-1], (10, 10))
        self.assertGreater(len(pts), 3)


class ReviewTests(unittest.TestCase):
    """Stats, events, storm, drop spots, chest spot hit rates, loot paths, a sharper map picture."""

    def rec(self):
        r = fake_record()
        r["map"]["players"][0]["route"] = [[t, 1000 * t, 0, 60000 if t < 3 else 300, 4 if 10 <= t < 12 else 1]
                                           for t in range(0, 30)]
        r["map"]["storm"] = [{"t": 1, "px": 0, "py": 0, "pr": 100000, "nx": 10000, "ny": 0, "nr": 50000,
                              "start": 10, "end": 20}]
        r["map"]["seen"] = ["spotA"]
        return r

    def test_player_stats(self):
        r = self.rec()
        st = mt.player_stats(r, r["map"]["players"][0], [("Home", 0, 0)])
        self.assertEqual(st["landed_at"], "Home")
        self.assertEqual(st["opened"], 1)
        self.assertAlmostEqual(st["alive"], 26)
        self.assertEqual(st["first_chest"], 2)  # chest at t=5, landed at t=3
        self.assertAlmostEqual(st["swim_m"], 20)  # 2 s of swimming at 10 m/s
        self.assertGreater(st["fast_m"], 0)       # 10 m/s on the ground counts as fast travel
        self.assertEqual(mt.landing_name(50000, 0, [("Home", 0, 0)]), "near Home")

    def test_events_in_time_order(self):
        ev = mt.match_events(self.rec())
        self.assertEqual([e["t"] for e in ev], sorted(e["t"] for e in ev))
        kinds = {e["kind"] for e in ev}
        self.assertTrue({"landing", "chest", "kill", "storm"} <= kinds)
        self.assertIn("Enemy1", next(e["text"] for e in ev if e["kind"] == "kill"))

    def test_storm_shrinks_between_its_times(self):
        storm = self.rec()["map"]["storm"]
        self.assertIsNone(mt.storm_at(storm, -5))  # before the first circle was known
        self.assertEqual(mt.storm_at(storm, 5)[:3], (0, 0, 100000))
        self.assertEqual(mt.storm_at(storm, 15)[:3], (5000, 0, 75000))
        self.assertEqual(mt.storm_at(storm, 25)[:3], (10000, 0, 50000))

    def test_drop_report(self):
        a, b = self.rec(), self.rec()
        b["file"] = "other.replay"
        b["map"]["chests"].append({"t": 6, "who": "RunninRobin", "id": "c2", "x": 0, "y": 0, "source": "opener"})
        rows = mt.drop_report([a, b], lambda n, r: bool(r), [("Home", 0, 0), ("Far", 900000, 0)])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["place"], rows[0]["games"], rows[0]["avg_chests"], rows[0]["best"]), ("Home", 2, 1.5, 2))
        self.assertEqual(rows[0]["best_file"], "other.replay")

    def test_hit_rates_and_loot_path(self):
        spots = {"spotA": {"x": 5000, "y": 500}, "spotB": {"x": 8000, "y": -500}, "far": {"x": 0, "y": 900000}}
        r1, r2 = self.rec(), self.rec()
        r2["map"]["seen"] = ["spotA", "spotB"]
        rates = mt.spot_hit_rates([r1, r2], spots)
        self.assertEqual(rates["spotA"], (2, 2))
        self.assertEqual(rates["spotB"], (1, 2))
        self.assertNotIn("far", rates)  # never came close: unknown
        many = {f"s{i}": {"x": 1000 * (i % 5), "y": 1000 * (i // 5)} for i in range(15)}
        path = mt.loot_path(many, (0, 0), radius=20000, count=10)
        self.assertEqual(len(path["spots"]), 10)
        self.assertEqual(len({s[0] for s in path["spots"]}), 10)
        self.assertLess(path["metres"], 140)  # a tidy walk, not a zig-zag across the grid
        skip = mt.loot_path(spots, (0, 0), rates={"spotA": (0, 4), "spotB": (3, 4)})
        self.assertEqual([s[0] for s in skip["spots"]], ["spotB"])  # spotA almost never has a chest

    def test_sharper_picture_lines_up(self):
        big = cv2.resize(ISLAND, (4096, 4096), interpolation=cv2.INTER_CUBIC)
        big = cv2.copyMakeBorder(big, 200, 0, 120, 0, cv2.BORDER_REPLICATE)[:4096, :4096]
        A = mt.align_hires(big, ISLAND)
        self.assertIsNotNone(A)
        self.assertAlmostEqual(A[0, 0], 0.5, places=3)
        self.assertAlmostEqual(A[0, 2], -60, delta=1.0)
        self.assertAlmostEqual(A[1, 2], -100, delta=1.0)
        tmp = Path(tempfile.mkdtemp())
        cv2.imwrite(str(tmp / "map.png"), ISLAND)
        (tmp / "hires").mkdir()
        cv2.imwrite(str(tmp / "hires" / "T_UI_MiniMap.png"), big)
        out = mt.prepare_hires(tmp, tmp / "map.png")
        self.assertEqual(cv2.imread(str(out)).shape[:2], (4096, 4096))
        cv2.imwrite(str(tmp / "hires" / "T_UI_MiniMap.png"), np.zeros((512, 512, 3), np.uint8))
        self.assertIsNone(mt.prepare_hires(tmp, tmp / "map.png"))  # a picture that doesn't match is ignored


class SquadOnlyReaderTests(unittest.TestCase):
    """Fair play: what the reader gives for the live map is the recorder's own squad only."""

    def test_squad_option_gives_nothing_about_other_teams(self):
        import os
        import subprocess

        exe = Path(os.environ.get("CHEST_TEST_READER") or Path(__file__).resolve().parents[1] / "reader" / "ChestReplayReader.exe")
        demos = Path(os.environ.get("LOCALAPPDATA") or "") / "FortniteGame" / "Saved" / "Demos"
        replays = sorted(demos.glob("*.replay"), key=lambda f: f.stat().st_mtime)[-3:] if demos.exists() else []
        if not exe.exists() or not replays:
            self.skipTest("needs the reader and a replay on this PC")
        out = subprocess.run([str(exe), "--squad", str(replays[0])], capture_output=True, text=True, encoding="utf-8",
                             timeout=300).stdout
        line = json.loads([ln for ln in out.splitlines() if ln.startswith("{")][-1])
        if "squad" not in line:
            self.skipTest("this reader was built before --squad")
        self.assertIsNone(line.get("map"))  # no kills, chests, storm or seen chests
        names = {line.get("recorder_name")} | {t["name"] for t in line.get("teammates") or []}
        for p in line["squad"] or []:
            self.assertIn(p["name"], names)  # the recorder's team only
            self.assertEqual(set(p), {"name", "recorder", "route"})
            self.assertTrue(all(len(pt) == 3 for pt in p["route"]))


class OuterLootPathTests(unittest.TestCase):
    """Click a landing spot: the best chests round the outside of the island, never through the middle."""

    def ring(self):
        import math

        spots, rates = {}, {}
        for i in range(36):  # a ring of chest spots, and some in the middle
            a = math.radians(i * 10)
            spots[f"out{i}"] = {"x": 70000 * math.cos(a), "y": 70000 * math.sin(a)}
            rates[f"out{i}"] = (3, 4) if i % 3 else (1, 4)  # every third one is rarely there
        for i in range(10):
            spots[f"mid{i}"] = {"x": 3000 * i, "y": 0}
            rates[f"mid{i}"] = (4, 4)  # always there, but in the middle
        return spots, rates

    def test_avoids_the_middle_and_goes_round(self):
        import math

        spots, rates = self.ring()
        path = mt.outer_loot_path(spots, (75000, 0), (0, 0, 80000), rates, count=10)
        ids = [sid for sid, _, _ in path["spots"]]
        self.assertEqual(len(ids), 10)
        self.assertFalse(any(sid.startswith("mid") for sid in ids))       # never the middle
        self.assertFalse(any(rates[sid] == (1, 4) for sid in ids))        # skips rarely-there spots
        r_in = path["inner"][2]
        for (ax, ay), (bx, by) in zip(path["path"][1:], path["path"][2:]):  # no leg cuts through the middle
            self.assertFalse(mt._leg_crosses(ax, ay, bx, by, 0, 0, r_in * 0.9))
        angles = [math.atan2(y, x) for _, x, y in path["spots"]]
        steps = [((b - a) * path["direction"]) % (2 * math.pi) for a, b in zip(angles, angles[1:])]
        self.assertTrue(all(s < math.pi for s in steps))  # one way round, no doubling back
        # no zig-zag: the walk is barely longer than the arc it covers
        legs = sum(math.hypot(bx - ax, by - ay) for (_, ax, ay), (_, bx, by) in zip(path["spots"], path["spots"][1:]))
        self.assertLess(legs, 70000 * sum(steps) * 1.3)
        self.assertGreater(path["metres"], 0)
        self.assertGreater(path["chests"], 6)

    def test_picks_up_chests_on_the_way(self):
        # a reliable chest far away and a row of slightly less reliable ones right on the way there
        spots = {f"near{i}": {"x": 70000, "y": 3000 * i} for i in range(1, 6)}
        spots["far"] = {"x": -70000, "y": 0}
        rates = {**{f"near{i}": (3, 4) for i in range(1, 6)}, "far": (4, 4)}
        path = mt.outer_loot_path(spots, (70000, 0), (0, 0, 80000), rates, count=5)
        self.assertEqual({sid for sid, _, _ in path["spots"]}, {f"near{i}" for i in range(1, 6)})
        self.assertLess(path["metres"], 200)  # 5 chests in about 150 m, not a trip across the island

    def test_count_up_to_80(self):
        import math

        spots = {f"s{i}": {"x": 70000 * math.cos(i / 15), "y": 70000 * math.sin(i / 15)} for i in range(120)}
        path = mt.outer_loot_path(spots, (75000, 0), (0, 0, 80000), None, count=80)
        self.assertEqual(len(path["spots"]), 80)

    def test_no_go_circles_and_checkpoints(self):
        import math

        spots, rates = self.ring()
        no_go = (70000, 0, 15000)  # round the landing-side stretch of the ring
        path = mt.outer_loot_path(spots, (60000, -40000), (0, 0, 80000), rates, count=12, avoid=[no_go])
        for sid, x, y in path["spots"]:
            self.assertGreater(math.hypot(x - no_go[0], y - no_go[1]), no_go[2])  # nothing planned inside
        for (ax, ay), (bx, by) in zip(path["path"][1:], path["path"][2:]):
            self.assertFalse(mt._leg_crosses(ax, ay, bx, by, no_go[0], no_go[1], no_go[2] * 0.9))  # walks round it
        self.assertEqual(path["avoid"], [no_go])

        cps = [(0, 70000), (-70000, 0)]  # north, then west
        path = mt.outer_loot_path(spots, (70000, 0), (0, 0, 80000), rates, count=12, checkpoints=cps)
        kinds = [st[0] for st in path["stops"]]
        self.assertEqual(kinds.count("checkpoint"), 2)
        cp_stops = [st for st in path["stops"] if st[0] == "checkpoint"]
        self.assertEqual([(st[2], st[3]) for st in cp_stops], cps)  # in the order given
        self.assertEqual(len(path["spots"]), 12)
        self.assertEqual(path["checkpoints"], cps)

    def test_spread_caps_chests_per_place(self):
        import collections
        import math

        # a dense town right by the landing spot, and single chests further round the ring
        spots = {f"town{i}": {"x": 70000 + 500 * (i % 5), "y": 500 * (i // 5)} for i in range(20)}
        for i in range(1, 30):
            a = math.radians(i * 11)
            spots[f"ring{i}"] = {"x": 70000 * math.cos(a), "y": 70000 * math.sin(a)}
        pois = [("Town", 71000, 1000)]
        dense = mt.outer_loot_path(spots, (70000, 0), (0, 0, 80000), None, count=15, pois=pois)
        self.assertGreaterEqual(sum(sid.startswith("town") for sid, _, _ in dense["spots"]), 12)  # clears the town
        self.assertIsNone(dense["max_per_place"])
        spread = mt.outer_loot_path(spots, (70000, 0), (0, 0, 80000), None, count=15, pois=pois, max_per_place=5)
        self.assertEqual(len(spread["spots"]), 15)
        self.assertEqual(sum(sid.startswith("town") for sid, _, _ in spread["spots"]), 5)
        per = collections.Counter(mt.loot_place(x, y, pois) for _, x, y in spread["spots"])
        self.assertLessEqual(max(per.values()), 5)  # the unnamed squares are capped too
        self.assertEqual(spread["max_per_place"], 5)

    def test_middle_can_be_allowed(self):
        spots, rates = self.ring()
        path = mt.outer_loot_path(spots, (5000, 0), (0, 0, 80000), rates, count=5, avoid_middle=False)
        self.assertTrue(any(sid.startswith("mid") for sid, _, _ in path["spots"]))
        self.assertIsNone(path["inner"])

    def test_detours_round_the_middle(self):
        spots = {"east": {"x": 70000, "y": 0}, "west": {"x": -70000, "y": 0}}
        path = mt.outer_loot_path(spots, (75000, 1000), (0, 0, 80000), None, count=2)
        self.assertGreater(len(path["path"]), 3)  # extra points to go round, not across
        for (ax, ay), (bx, by) in zip(path["path"][1:], path["path"][2:]):
            self.assertFalse(mt._leg_crosses(ax, ay, bx, by, 0, 0, path["inner"][2] * 0.9))


class FakeApp:
    def __init__(self):
        self.cfg = {"name": "Robin", "map_share": True}
        self.stats = type("S", (), {"game": 0, "id": "aaaa1111"})()
        self.team = type("T", (), {"key": "teamkey"})()
        self.in_match = True

    def match_in_progress(self):
        return self.in_match


class ManagerTests(unittest.TestCase):
    def make(self):
        tmp = Path(tempfile.mkdtemp())
        app = FakeApp()
        m = mt.MapManager(app, tmp, tmp / "demos", tmp / "reader.exe", window_rect=lambda: None, in_front=lambda: True)
        m.matcher = MATCHER
        m.tracker = mt.Tracker(MATCHER)
        return app, m

    def test_chest_dot_snaps_to_known_spot_and_undo_removes_it(self):
        app, m = self.make()
        m._start_live(time.time())
        m.tracker.pos = (1000.0, 1000.0)
        wx, wy = mt.map_to_world(1002.0, 1001.0)
        m.spots.add("unknown", "Tiered_Chest_UAID_9", "Tiered_Chest", wx, wy, "actor")
        m._chest(None, 1, time.time())
        c = m.live["chests"][0]
        self.assertEqual(c["spot"], "Tiered_Chest_UAID_9")
        self.assertAlmostEqual(c["x"], 1002.0, places=0)
        m.tracker.pos = (1300.0, 1000.0)
        m._chest(None, 1, time.time())
        self.assertIsNone(m.live["chests"][1]["spot"])  # no known chest near: stays where the player is
        m._unchest(1)
        self.assertEqual(len(m.live["chests"]), 1)

    def test_live_legend_lists_me_and_sharing_teammates(self):
        try:
            import tkinter as tk

            root = tk.Tk()
        except Exception as e:
            self.skipTest(f"no Tk: {e}")
        root.withdraw()
        import map_window as mw

        app, m = self.make()
        app.root = root
        app.cfg.update({"fortnite_names": ["RunninRobin"], "map_geometry": "500x500+-7000+0"})
        app.replays = type("R", (), {"my_names": lambda self: {"runninrobin"}})()
        m._start_live(time.time())
        m.live["route"].append([time.time(), 1000, 1000, 0])
        m.live["chests"].append({"t": 1, "x": 1000, "y": 1000, "spot": None})
        m.team["bbbb"] = {"name": "Falcon", "route": [(1100, 1000)], "chests": [(1100, 1000)] * 3,
                          "pos": (1100, 1000), "t": time.time()}
        # console squadmates from the replay being recorded; Falcon also shares over the relay: shown once
        m.take_live_squad([{"name": "RunninRobin", "recorder": True, "route": [[10, 0, 0]]},
                           {"name": "jFalcon", "recorder": False, "route": [[10, 0, 0], [20, 5000, 0]]},
                           {"name": "Falcon", "recorder": False, "route": [[20, 9000, 0]]},
                           {"name": "Stranger1", "recorder": False, "route": [[20, 12000, 0]]}],
                          [{"name": "jFalcon", "opened": 4}, {"name": "Falcon", "opened": 3},
                           {"name": "Stranger1", "opened": 5, "broken": 1}], time.time() - 25)
        self.assertEqual(set(m.live_squad), {"jFalcon", "Falcon", "Stranger1"})  # never my own entry
        app.cfg["console_teammates"] = ["jFalcon"]  # a known console teammate; Stranger1 is a random fill
        before = (app.stats.game, dict(app.cfg))
        win = mw.MapWindow(app, m, lambda: None)
        win.win.deiconify()
        try:
            root.update()
            win._render()
            items = [(i[1].split(" (")[0], i[2]) for i in win.legend_items()]
            self.assertEqual(items, [("RunninRobin: 1 chest", "me"), ("Falcon: 3 chests", "player"),
                                     ("jFalcon: 4 chests", "player"), ("Stranger1: 6 chests", "random")])
            self.assertIn("from the replay", win.legend_items()[2][1])
            self.assertIn("random, not saved", win.legend_items()[3][1])
            self.assertEqual(win.legend_items()[3][0], mw.RANDOM)
            # shown but not counted: nothing about the random fill reached my numbers or settings
            self.assertEqual((app.stats.game, app.cfg), before)
            self.assertFalse(any("Stranger1" in str(v) for v in app.cfg.values()))
            # in a real match the replay can't be read until it ends: console teammates come after the match
            m.live_squad = {}
            m.bump()
            win._changed()
            win._render()
            self.assertIn("console teammates show after the match", win.status.cget("text"))
            self.assertEqual([i[2] for i in win.legend_items()], ["me", "player"])
        finally:
            root.destroy()

    def test_player_choices_leave_out_random_fills(self):
        try:
            import tkinter as tk

            root = tk.Tk()
        except Exception as e:
            self.skipTest(f"no Tk: {e}")
        root.withdraw()
        import map_window as mw

        app, m = self.make()
        app.root = root
        app.cfg.update({"fortnite_names": ["RunninRobin"], "map_geometry": "500x500+-7000+0",
                        "console_teammates": ["Falcon"]})
        app.replays = type("R", (), {"my_names": lambda self: {"runninrobin"}})()
        for i in range(3):  # Falcon (console teammate) and a random fill, in three games
            rec = fake_record()
            rec["file"], rec["start"] = f"g{i}.replay", 1.0e9 + i
            rec["map"]["players"].append({"name": "Stranger1", "recorder": False, "route": [[5, 0, 0, 1000]],
                                          "landing": [5, 0, 0], "kills": [], "end": [5, 0, 0]})
            m.replay_maps[rec["file"]] = rec
        m.bump()
        win = mw.MapWindow(app, m, lambda: None)
        try:
            win._refresh_choices()
            self.assertEqual(list(win.who_box["values"]), ["Me", "Squad", "Falcon"])
            self.assertTrue(win.squad_member("Falcon"))
            self.assertFalse(win.squad_member("Stranger1"))
            self.assertFalse(win.squad_member("RunninRobin"))
            win.who.set("Squad")
            pick = win._picker()
            self.assertTrue(pick("RunninRobin", True) and pick("Falcon", False))
            self.assertFalse(pick("Stranger1", False))  # the squad heatmap leaves random fills out
        finally:
            root.destroy()

    def test_drag_and_zoom_redraw_at_once(self):
        try:
            import tkinter as tk

            root = tk.Tk()
        except Exception as e:
            self.skipTest(f"no Tk: {e}")
        root.withdraw()
        import map_window as mw

        app, m = self.make()
        app.root = root
        app.cfg.update({"fortnite_names": ["RunninRobin"], "map_geometry": "500x400+-7000+0"})
        app.replays = type("R", (), {"my_names": lambda self: {"runninrobin"}})()
        win = mw.MapWindow(app, m, lambda: None)
        win.win.deiconify()
        try:
            root.update()
            win._render()
            before = win.render_count
            win.zoom_by(1.25, (250, 200))  # a wheel step: drawn now, not at the next 300 ms tick
            self.assertIsNotNone(win._soon)
            self.assertTrue(win._quick())  # a quick draft while zooming
            root.after(5, lambda: None)
            for _ in range(20):
                root.update()
                if win.render_count > before:
                    break
                time.sleep(0.005)
            self.assertGreater(win.render_count, before)
            win._quick_until = 0.0
            self.assertFalse(win._quick())  # still again: the sharp picture
            # drawing straight onto the canvas fails: falls back to the Tk image for good
            win._dib_ok, win._dib = True, None
            import PIL.ImageWin as iw
            real = getattr(iw, "Dib", None)
            iw.Dib = lambda *a, **k: (_ for _ in ()).throw(OSError("no"))
            try:
                win._changed()
                win._render()
            finally:
                if real is not None:
                    iw.Dib = real
            self.assertFalse(win._dib_ok)
            self.assertIsNotNone(win._photo)
        finally:
            root.destroy()

    def test_whole_game_counts_come_from_the_replay_log(self):
        app, m = self.make()
        rec = fake_record()  # markers: RunninRobin 1 opened, Falcon 1 far-away search
        m.replay_maps[rec["file"]] = rec
        db = type("DB", (), {})()
        db.version, db.files = 1, {rec["file"]: {"recorder": "RunninRobin", "raw": {"opened": 40, "broken": 3},
                                                 "teammates": [{"name": "Falcon", "opened": 25, "broken": 0}]}}
        app.replays = type("R", (), {"my_names": lambda self: {"runninrobin"}, "db": db})()
        m.records()
        fyn = rec["map"]["players"][1]
        self.assertEqual(mt.player_stats(rec, fyn)["opened"], 25)  # the reader's whole-game number
        self.assertEqual(mt.chests_so_far(rec, "Falcon", 10), (1, 0))  # scrubbing: markers so far
        self.assertEqual(mt.chests_so_far(rec, "RunninRobin", None), (40, 3))
        everyone = lambda name, r: True  # noqa: E731
        self.assertEqual(mt.match_summary(rec, everyone)["chests"], 65)
        db.files[rec["file"]]["teammates"][0]["opened"] = 0  # capped: never more than the whole game's
        db.version = 2
        m.records()
        self.assertEqual(mt.chests_so_far(rec, "Falcon", 10), (0, 0))
        db.files.clear()
        db.version = 3
        m.records()  # no log entry: the markers
        self.assertEqual(mt.player_stats(rec, fyn)["opened"], 1)

    def test_live_route_only_on_the_ground(self):
        app, m = self.make()
        self.assertTrue(m.tracking_ok())  # no game log (tests): as before
        gl = type("GL", (), {})()
        gl.state, gl.view, gl.view_name = None, "bus", ""
        gl.match_state = lambda: gl.state
        gl.tracking_ok = lambda: gl.state == "match" and gl.view == "self"
        app.gamelog = gl
        self.assertTrue(m.tracking_ok())  # the log can't tell yet: as before
        gl.state = "match"
        self.assertFalse(m.tracking_ok())
        self.assertEqual(m.camera_note(), "on the bus")
        m._start_live(time.time())
        m.live["route"].append([time.time(), 1000.0, 900.0, 0])
        gl.view, gl.view_name = "spectating", "DontGetCaught"
        self.assertEqual(m.camera_note(), "spectating DontGetCaught")
        self.assertIsNone(m.team_message())  # not where I am: nothing sent to the squad
        gl.view = "self"
        self.assertTrue(m.tracking_ok())
        self.assertEqual(m.camera_note(), "")
        self.assertIsNotNone(m.team_message())

    def test_reboot_starts_a_new_route_piece(self):
        app, m = self.make()
        gl = type("GL", (), {})()
        gl.view, gl.view_name = "self", ""
        gl.match_state = lambda: "match"
        gl.tracking_ok = lambda: gl.view == "self"
        app.gamelog = gl
        app.stats.game = 0
        Fix = type("Fix", (), {})
        tr = type("Tr", (), {})()
        tr.pos, tr.resets, tr.at = None, 0, (1000.0, 1000.0)

        def update(mm, now):
            f = Fix()
            f.ok, (f.x, f.y) = True, tr.at
            tr.pos = tr.at
            return f

        def reset():
            tr.pos, tr.resets = None, tr.resets + 1
        tr.update, tr.reset = update, reset
        m.tracker = tr
        m._grab = lambda sct: "minimap"
        m.in_front = lambda: True
        t0 = time.time()
        m._start_live(t0)
        m._was_tracking = True
        clock = [t0]

        def step(view, at=None, chests=0):
            gl.view = view
            if at:
                tr.at = at
            app.stats.game += chests
            clock[0] += 1.5
            m._live_tick(None, clock[0])

        step("self", (1000.0, 1000.0))
        step("self", (1003.0, 1000.0), chests=1)  # a chest while on my feet
        step("dead", (1003.0, 1000.0))
        step("spectating", (1500.0, 400.0), chests=1)  # a teammate's screen: no route, no chest
        step("spectating", (1510.0, 400.0))
        step("self", (1200.0, 1300.0))  # rebooted, somewhere else
        step("self", (1203.0, 1300.0), chests=1)
        route = m.live["route"]
        xs = [(p[1], p[2], p[3]) for p in route]
        self.assertNotIn(1500.0, [x for x, _, _ in xs])  # nothing recorded while out / spectating
        reboot = next(i for i, p in enumerate(route) if p[1] == 1200.0)
        self.assertEqual(route[reboot][3], 1)  # a new piece: no line from where I went down
        self.assertTrue(all(p[3] == 0 for p in route[reboot + 1:]))
        self.assertGreaterEqual(tr.resets, 1)  # whole-map search after coming back
        self.assertEqual(len(m.live["chests"]), 2)  # mine only: before going down and after the reboot
        self.assertEqual(m._last_game, 3)
        gl.view = "dead"
        self.assertIsNone(m.team_message())
        gl.view = "self"
        self.assertIsNotNone(m.team_message())  # sending again after the reboot

    def test_safe_save_retries_a_locked_file(self):
        import os
        from unittest import mock

        app, m = self.make()
        target = m.maps_dir / "index.json"
        real = os.replace
        calls = []

        def flaky(a, b):  # refused twice (antivirus holding the file), then fine
            calls.append(1)
            if len(calls) <= 2:
                raise PermissionError(13, "Permission denied")
            return real(a, b)
        with mock.patch("os.replace", side_effect=flaky):
            self.assertTrue(mt.save_atomic(target, '{"a": 1}'))
        self.assertEqual(json.loads(target.read_text()), {"a": 1})
        self.assertEqual(len(calls), 3)
        with mock.patch("os.replace", side_effect=PermissionError(13, "Permission denied")):
            self.assertFalse(mt.save_atomic(target, '{"a": 2}'))  # gave up: logged, not raised
        self.assertEqual(json.loads(target.read_text()), {"a": 1})
        self.assertEqual(list(target.parent.glob("*.tmp")), [])  # no temp files left behind
        m._index["x.replay"] = {"key": "1:1", "reader": 1}
        with mock.patch("os.replace", side_effect=PermissionError(13, "Permission denied")):
            m._save_index()  # the map thread carries on
        m._save_index()
        self.assertIn("x.replay", m._load_index())
        m.spots.add("43", "s1", "Tiered_Chest", 100, 200, "actor")
        m.spots.save()
        self.assertIn("s1", json.loads(m.spots.path.read_text())["43"])

    def test_last_game_message_for_the_phone_page(self):
        app, m = self.make()
        app.team.key = "k"
        app.stats.game, app.stats.game_started = 0, 1000.0
        playing = [True]
        app.match_in_progress = lambda: playing[0]
        m._start_live(time.time())
        m.live["route"].append([time.time(), 1000.0, 900.0, 0])
        m.live["chests"].append({"t": time.time(), "x": 1000.0, "y": 900.0, "spot": None})
        app.stats.game = 63
        self.assertIsNone(m.last_message())  # never mid-match
        playing[0] = False
        m._end_live()
        msg = json.loads(m.last_message())
        self.assertEqual((msg["app"], msg["type"], msg["team"], msg["game"]), ("chesttracker-map", "last", "k", 63))
        self.assertEqual(msg["match"], m.live["id"])
        self.assertEqual(len(msg["hist"]["route"]), 1)
        self.assertIsNone(m.last_message())  # once
        app.stats.game = 94  # the replay recounted this game
        self.assertEqual(json.loads(m.last_message())["game"], 94)
        self.assertIsNone(m.last_message())
        app.stats.game, app.stats.game_started = 1, 2000.0  # the counter moved on to a new game: not this one
        self.assertIsNone(m.last_message())
        self.assertNotEqual(msg["app"], "chesttracker")  # Team.update (old trackers too) only takes "chesttracker"

    def test_team_message_round_trip(self):
        app, m = self.make()
        m._start_live(time.time())
        self.assertIsNone(m.team_message())  # no position yet: nothing to send
        m.live["route"].append([time.time(), 1000.4, 900.6, 0])
        m.live["chests"].append({"t": 1, "x": 990.0, "y": 905.0, "spot": None})
        raw = m.team_message()
        self.assertLess(len(raw), 400)
        msg = json.loads(raw)
        self.assertEqual(msg["app"], "chesttracker-map")  # older versions ignore it
        self.assertIsNone(m.team_message())  # rate limited

        other_app, other = self.make()
        other_app.stats.id = "bbbb2222"
        other._start_live(time.time())
        self.assertTrue(other.on_team_message(msg))
        p = next(iter(other.team.values()))
        self.assertEqual(p["pos"], (1000.0, 901.0))
        self.assertEqual(p["chests"], [(990.0, 905.0)])
        self.assertFalse(m.on_team_message(msg))  # my own message
        self.assertFalse(other.on_team_message({**msg, "team": "other"}))
        self.assertFalse(other.on_team_message({**msg, "id": "cccc", "pos": [99999, 0]}))

    def test_teammate_count_is_their_real_count_not_the_last_40_marks(self):
        app, m = self.make()
        m._start_live(time.time())
        other_app, other = self.make()
        other_app.stats.id = "bbbb2222"
        other._start_live(time.time())
        app.stats.game = 56
        for i in range(50):
            m.live["chests"].append({"t": i, "x": 100.0 + i * 10, "y": 500.0, "spot": None})
            if i in (5, 49):  # two messages: marks 0-5, then the last 40 of 50
                m.live["route"].append([time.time(), 1000, 900, 0])
                m._last_team_send = 0
                msg = json.loads(m.team_message())
                self.assertLessEqual(len(msg["chests"]), 40)
                self.assertTrue(other.on_team_message(msg))
        self.assertEqual(msg["game"], 56)
        p = next(iter(other.team.values()))
        self.assertEqual(len(p["chests"]), 46)  # 0-5 kept from the first message, 10-49 from the second
        self.assertEqual(p["game"], 56)
        self.assertTrue(other.on_team_message({**msg, "match": "next-match", "chests": [[1, 2]], "game": 1}))
        self.assertEqual((len(p["chests"]), p["game"]), (1, 1))  # a new match starts over
        old = {k: v for k, v in msg.items() if k != "game"}  # a tracker from before 1.20
        self.assertTrue(other.on_team_message({**old, "match": "x", "id": "cccc"}))
        self.assertNotIn("game", other.team["cccc"])

    def test_live_view_during_a_match_even_without_my_position(self):
        """No minimap fix this match (Falcon, Nov 19): show this match and the squad, not the last game."""
        try:
            import tkinter as tk

            root = tk.Tk()
        except Exception as e:
            self.skipTest(f"no Tk: {e}")
        root.withdraw()
        import map_window as mw

        app, m = self.make()
        app.root = root
        app.cfg.update({"fortnite_names": ["Falcon"], "map_geometry": "500x500+-7000+0"})
        app.replays = type("R", (), {"my_names": lambda self: set()})()
        m._start_live(time.time() - 600)
        m.live["route"].append([time.time() - 300, 1000, 1000, 0])
        m._end_live()  # the previous game
        m._start_live(time.time())  # this match: no position of my own at all
        self.assertIsNotNone(m.last_live)
        app.stats.game = 5
        m.team["aaaa"] = {"name": "Robin", "route": [(1100, 1000)], "chests": [], "pos": (1100, 1000),
                          "t": time.time(), "game": 7}
        win = mw.MapWindow(app, m, lambda: None)
        try:
            live, rec = win._match_data()
            self.assertIs(live, m.live)
            self.assertIsNone(rec)
            root.update()
            win._render()
            items = [i[1] for i in win.legend_items()]
            self.assertTrue(items[0].startswith("Falcon: 5 chests"), items)
            self.assertTrue(items[1].startswith("Robin: 7 chests"), items)
            app.in_match = False  # after the match: back to the latest game as before
            live, rec = win._match_data()
            self.assertIs(live, m.last_live)
        finally:
            win.win.destroy()
            root.destroy()

    def test_looks_for_the_minimap_when_it_keeps_missing_and_remembers_it(self):
        app, m = self.make()
        self.assertEqual(m.box(1920, 1080), mt.minimap_box(1920, 1080))  # nothing found yet: the default
        saved = []
        m.save_cfg = lambda: saved.append(dict(app.cfg))
        m.window_rect = lambda: {"left": 0, "top": 0, "width": 1920, "height": 1080}
        region = corner_with_minimap(189, 26, 26)

        class Shot:
            def grab(self, box):
                self.box = box
                return np.dstack([region, np.full(region.shape[:2], 255, np.uint8)])  # BGRA, like mss

        sct, t0 = Shot(), 1000.0
        m._maybe_find_box(sct, t0, False)
        m._maybe_find_box(sct, t0 + mt.FIND_AFTER - 1, False)
        self.assertEqual(m._finds, 0)  # not yet: a few misses are normal (storm, a menu)
        m._maybe_find_box(sct, t0 + mt.FIND_AFTER + 1, False)
        self.assertEqual(m._finds, 1)
        self.assertEqual(sct.box, {"left": 1920 - 540, "top": 0, "width": 540, "height": 540})
        self.assertEqual(app.cfg["minimap_boxes"]["1920x1080"], [26, 26, 189])
        self.assertEqual(m.box(1920, 1080), (1920 - 26 - 189, 26, 189))
        self.assertEqual(len(saved), 1)
        self.assertEqual(m.box(2560, 1080), mt.minimap_box(2560, 1080))  # per window size

        # not found: wait longer each time (FIND_EVERY, then doubling), never every second
        region[:] = 60
        m._miss_since, m._next_find = None, 0.0
        m._maybe_find_box(sct, 5000.0, False)
        m._maybe_find_box(sct, 5000.0 + mt.FIND_AFTER + 1, False)
        self.assertEqual(m._finds, 2)
        m._maybe_find_box(sct, 5000.0 + mt.FIND_AFTER + 60, False)
        self.assertEqual(m._finds, 2)
        self.assertEqual(m._find_wait, 2 * mt.FIND_EVERY)
        self.assertEqual(app.cfg["minimap_boxes"]["1920x1080"], [26, 26, 189])  # a failed look keeps the last
        m._maybe_find_box(sct, 6000.0, True)  # a match: the miss clock stops
        self.assertIsNone(m._miss_since)

    def test_health_says_why_nothing_is_shared(self):
        app, m = self.make()
        h = m.health()
        self.assertFalse(h["live"])
        self.assertTrue(h["loaded"])
        m._start_live(time.time())
        m.live["route"].append([time.time() - 30, 1000, 900, 0])
        h = m.health()
        self.assertEqual((h["live"], h["fixes"]), (True, 1))
        self.assertGreaterEqual(h["fix_age"], 29)
        self.assertLess(len(json.dumps(h)), 300)

    def test_team_message_carries_console_squadmates_only(self):
        app, m = self.make()
        app.cfg["console_teammates"] = ["Brave-Boots"]
        m._start_live(time.time())
        m.live["route"].append([time.time(), 1000, 900, 0])
        m.take_live_squad([{"name": "Brave-Boots", "recorder": False, "route": [[20, 5000, 0]]},
                           {"name": "Stranger1", "recorder": False, "route": [[20, 9000, 0]]}],
                          [], time.time() - 25)
        msg = json.loads(m.team_message())
        self.assertEqual([p["name"] for p in msg["squad"]], ["Brave-Boots"])  # never a random fill
        x, y = mt.world_to_map(5000, 0)
        self.assertEqual(msg["squad"][0]["pos"], [round(x), round(y)])
        # a teammate sharing their own position over the relay is not repeated
        m.team["bbbb"] = {"name": "Brave-Boots", "route": [], "chests": [], "pos": (1, 1), "t": time.time()}
        self.assertEqual(m.guest_positions(time.time()), [])

    def test_team_message_carries_the_match_so_far_now_and_then(self):
        app, m = self.make()
        start = time.time() - 600
        m._start_live(start)
        m.live["started"] = start
        for i in range(600):  # a position every second for 10 minutes
            m.live["route"].append([start + i, 500 + i, 900, 0])
        m.live["chests"].append({"t": start + 30, "x": 530.0, "y": 900.0, "spot": "Tiered_Chest_UAID_1"})
        raw = m.team_message()
        hist = json.loads(raw)["hist"]
        self.assertEqual(hist["chests"], [[30, 530, 900]])  # where and when, never the chest spot's id
        self.assertNotIn("UAID", raw.decode())
        self.assertLessEqual(len(hist["route"]), 300)
        self.assertEqual(hist["route"][0], [0, 500, 900])
        self.assertEqual(hist["route"][-1], [599, 1099, 900])
        self.assertLess(len(raw), 3000)
        m._last_team_send = 0
        self.assertNotIn("hist", json.loads(m.team_message()))  # only every HIST_EVERY seconds

    def test_nothing_sent_outside_matches_or_when_off(self):
        app, m = self.make()
        m._start_live(time.time())
        m.live["route"].append([time.time(), 1000, 900, 0])
        app.in_match = False
        self.assertIsNone(m.team_message())
        app.in_match, app.cfg["map_share"] = True, False
        self.assertIsNone(m.team_message())

    def test_replay_read_by_the_chest_counter_is_not_read_again(self):
        app, m = self.make()
        demos = m.demos
        demos.mkdir(parents=True, exist_ok=True)
        m.maps_dir.mkdir(parents=True, exist_ok=True)
        m.reader.write_text("exe")
        f = demos / "UnsavedReplay-2026.11.19-00.35.22.replay"
        f.write_bytes(b"x" * 100)
        old = time.time() - 120
        import os

        os.utime(f, (old, old))
        stamp = m.reader.stat().st_mtime
        self.assertEqual(len(m._todo(m._index, stamp)), 1)
        rec = fake_record()
        out = {"start_utc": "2026-11-19T07:35:22Z", "length_ms": 30000, "branch": "++Fortnite+Release-43.10",
               "map": rec["map"], "complete": True}
        app.in_match = True
        m.take_reader_output(f, out)  # never during a match (fair play: no storm or routes live)
        self.assertNotIn(f.name, m.replay_maps)
        app.in_match = False
        m.take_reader_output(f, out)
        self.assertEqual(m._todo(m._index, stamp), [])
        self.assertIn(f.name, m.replay_maps)
        self.assertEqual(m.replay_maps[f.name]["season"], "43")
        again = mt.MapManager(app, m.dir.parent, demos, m.reader, window_rect=lambda: None, in_front=lambda: True)
        self.assertEqual(again._todo(again._index, stamp), [])  # remembered on disk

    def test_game_ended_by_queueing_the_next_match_still_gets_its_map(self):
        """Nov 20, 2026: the 7:29 PM game was read while the next match was on (queued from the results
        screen), its map data was dropped, and the map reader waited for a lobby that never came."""
        app, m = self.make()
        m.demos.mkdir(parents=True, exist_ok=True)
        m.maps_dir.mkdir(parents=True, exist_ok=True)
        m.reader.write_text("exe")
        f = m.demos / "UnsavedReplay-2026.11.20-19.29.36.replay"
        f.write_bytes(b"x" * 100)
        out = {"start_utc": "2026-11-21T02:29:36Z", "length_ms": 30000, "branch": "++Fortnite+Release-43.10",
               "map": fake_record()["map"], "complete": True}
        app.in_match = True
        m.take_reader_output(f, out, finished=True)  # a finished recording: a past match, fine to keep
        self.assertIn(f.name, m.replay_maps)

        # the map's own reader keeps going during a match, one at a time, in the background
        self.assertEqual(m._workers(), 1)
        calls = []
        m._run_reader = lambda path, background=False: calls.append((path.name, background)) or {"live": True, "map": None}
        self.assertEqual(m._read_one(f, m._playing()), "live")  # (the match being played is never read)
        self.assertEqual(calls, [(f.name, True)])

    def test_live_record_links_to_its_replay(self):
        app, m = self.make()
        m._start_live(1.0e9 + 5)  # the log says "match joined" about when the replay starts recording
        m.live["route"].append([1.0e9 + 20, 1, 1, 0])
        rec = fake_record()
        m.replay_maps[rec["file"]] = rec
        self.assertIs(m.latest_replay_for_live(m.live), rec)
        self.assertIs(m.live_for(rec), m.live)


class WindowTests(unittest.TestCase):
    """The map window draws every view and plays a replay back (off screen)."""

    def test_views_and_timeline(self):
        try:
            import tkinter as tk

            root = tk.Tk()
        except Exception as e:  # no display
            self.skipTest(f"no Tk: {e}")
        root.withdraw()
        import map_window as mw

        tmp = Path(tempfile.mkdtemp())
        cv2.imwrite(str(tmp / "map.png"), ISLAND)
        app = FakeApp()
        app.in_match = False
        app.root = root
        app.cfg["map_geometry"] = "640x700+-7000+0"
        app.replays = type("R", (), {"my_names": lambda self: {"runninrobin"}})()
        m = mt.MapManager(app, tmp, tmp, tmp / "reader.exe", window_rect=lambda: None, in_front=lambda: False)
        m.map_path = tmp / "map.png"
        rec = fake_record()
        rec["start"] = time.time() - 3600
        m.replay_maps[rec["file"]] = rec
        win = mw.MapWindow(app, m, lambda: None)
        win.win.deiconify()
        try:
            root.update()
            win._render()
            self.assertIn("Replay", win.status.cget("text"))  # no live match: the latest replay
            label = next(k for k, v in win._match_choices.items() if v == rec["file"])
            win.match_box.set(label)
            win._picked()
            win.toggle_play()
            self.assertEqual(win.t, 0.0)
            time.sleep(0.2)
            win._tick()
            self.assertGreater(win.t, 0.0)
            win._scrubbed(0.5)
            win._render()
            self.assertIn("at 0:1", win.status.cget("text"))
            win.view.set("heat")
            for kind in mt.HEAT_KINDS.values():
                win.kind.set(kind)
                win._changed()
                win._tick()
                self.assertIn(kind, win.status.cget("text"))
            win.games.set("Top 10% by chests")
            win._changed()
            started = time.perf_counter()
            win._tick()
            self.assertLess(time.perf_counter() - started, 0.5)  # the layer is built in the background
            for _ in range(300):  # until the background layer is ready (slower while other tests run)
                if "drawing" not in win.status.cget("text"):
                    break
                time.sleep(0.03)
                win._tick()
            self.assertIn("from 1 matches", win.status.cget("text"))
            # reviewing: browser, one match with its panel and events, comparing, loot path, saving
            rec2 = fake_record()
            rec2["file"], rec2["start"] = "second.replay", time.time() - 7200
            m.replay_maps[rec2["file"]] = rec2
            win.games.set("All games")
            win.open_browser()
            root.update()
            win._browser.refresh()
            self.assertEqual(len(win._browser.matches.get_children()), 2)
            order = [w for w in win.win.pack_slaves() if w in (win.bar, win.filters, win.bar2)]
            win.set_simple(True)
            self.assertFalse(any(r.winfo_manager() for r in (win.bar, win.filters, win.bar2)))
            win._layout_bar()  # must not bring the rows back while simple
            self.assertFalse(win.filters.winfo_manager())
            win.show_view("heat", kind="chests", who="Squad")
            self.assertEqual((win.view.get(), win.who.get()), ("heat", "Squad"))
            win.set_simple(False)
            self.assertEqual([w for w in win.win.pack_slaves() if w in (win.bar, win.filters, win.bar2)], order)
            self.assertIs(win.win.pack_slaves()[0], win.bar)
            win.set_who("Me")
            win.show_view("heat", kind="landing")
            self.assertEqual((win.view.get(), win.kind.get()), ("heat", mt.HEAT_KINDS["landing"]))
            win.show_view("match", match_file=rec["file"])
            self.assertEqual(win._match_choices.get(win.match_box.get()), rec["file"])
            # watching a teammate: picker, follow, stats first
            win.show_match(rec["file"], player="Falcon")
            win._tick()
            self.assertEqual(win.watch_var.get(), "Watch: Falcon")
            self.assertIn("Watch: Falcon", win.watch_box["values"])
            before = win.render_count
            win._changed()
            win._render()
            self.assertGreater(win.render_count, before)
            items = win.legend_items()
            # Falcon isn't a console teammate in settings and has no tracker here: a random fill
            self.assertEqual([(i[1], i[2]) for i in items],
                             [("Falcon: 1 chest (random, not saved)", "watched"), ("RunninRobin: 1 chest", "player")])
            app.cfg["console_teammates"] = ["Falcon"]
            win._changed()
            win._render()
            items = win.legend_items()
            self.assertEqual([(i[1], i[2]) for i in items], [("Falcon: 1 chest", "watched"), ("RunninRobin: 1 chest", "player")])
            version = win.legend_version
            win.jump(12)
            win._render()  # a new frame, same people: the legend didn't change
            self.assertEqual(win.legend_version, version)
            self.assertEqual(items[1][0], mw.ME)
            self.assertTrue(win.follow.get())
            fyn = next(p for p in rec["map"]["players"] if p["name"] == "Falcon")
            win.jump(10)
            pt = mw.at_time(fyn["route"], 10)
            self.assertEqual(win._my_pos(None, rec), mt.world_to_map(pt[1], pt[2], m.tf))
            win.show_panel.set(True)
            win._update_panel(rec)
            self.assertTrue(win.stats_label.cget("text").startswith("Falcon"))
            # a host with its own controls: no timeline row, public playback API
            win.set_timeline(False)
            win._tick()
            self.assertFalse(win.timeline.winfo_manager())
            pb = win.playback()
            self.assertEqual((pb["watched"], pb["compare"], pb["speeds"][0]), ("Falcon", False, "1x"))
            self.assertEqual(set(pb["players"]), {"RunninRobin", "Falcon"})
            self.assertTrue(pb["events"])  # still there for the host's tick marks and next_event()
            win.set_speed("10x")
            self.assertEqual(win.playback()["speed"], "10x")
            win.toggle_play()
            win.seek(5)
            self.assertEqual((win.playback()["t"], win.playback()["playing"]), (5.0, True))  # seeking keeps playing
            win.toggle_play()
            win.watch("RunninRobin")
            self.assertEqual(win.playback()["watched"], "RunninRobin")
            win.set_timeline(True)
            win._tick()
            self.assertTrue(win.timeline.winfo_manager())
            win.who.set("Falcon")
            win.show_match(rec["file"])  # no player given: the teammate picked under who
            win._tick()
            self.assertEqual(win.watch_var.get(), "Watch: Falcon")
            win.who.set("Me")
            win.show_match(rec["file"])
            win._tick()
            self.assertEqual(win.watch_var.get(), "Watch: RunninRobin")
            win.show_match(rec["file"])
            win._tick()
            self.assertTrue(win._events)
            win.jump(0)
            win.next_event(1)
            self.assertGreater(win.t, 0)
            win.compare([rec["file"], rec2["file"]])
            win._tick()
            self.assertIn("Comparing 2 matches", win.status.cget("text"))
            self.assertEqual([i[2] for i in win.legend_items()], ["game", "game"])
            self.assertEqual(win.legend_items()[0][0], mw.COMPARE[0])
            win.set_key(False)
            win.set_colors(bg="#101820", panel="#111111", text="#eeeeee", select="#333333")
            win._tick()
            self.assertEqual(win._bg, (16, 24, 32))
            self.assertEqual(win.canvas.cget("bg"), "#101820")
            self.assertEqual(win.events_list.cget("selectbackground"), "#333333")
            win.show_match(rec["file"])
            win._tick()
            self.assertNotIn("Circle opened", win.status.cget("text"))
            win.set_key(True)
            win._tick()
            self.assertIn("Circle opened", win.status.cget("text"))
            win.compare([rec["file"], rec2["file"]])
            win._tick()
            m.island = (424, 424, 1624, 1624)
            m.spots.add(m.season(), "ring1", "Tiered_Chest", *mt.map_to_world(1500, 1000), "actor")
            m.spots.add(m.season(), "ring2", "Tiered_Chest", *mt.map_to_world(1000, 1500), "actor")
            m.spots.add(m.season(), "mid", "Tiered_Chest", *mt.map_to_world(1024, 1024), "actor")
            win.set_plan_mode(True)
            win.set_plan_count(80)
            self.assertEqual(win.plan_count.get(), 80)
            win.set_plan_spread(3)
            self.assertEqual(win.plan_per_place(), 3)
            self.assertEqual(app.cfg["map_plan_per_place"], 3)
            self.assertEqual(win.plan_loot_from(1550, 1024)["max_per_place"], 3)
            win.set_plan_spread(None)
            self.assertIsNone(app.cfg["map_plan_per_place"])
            planned = win.plan_loot_from(1550, 1024)
            self.assertEqual({sid for sid, _, _ in planned["spots"]}, {"ring1", "ring2"})  # not the middle one
            win._changed()
            win._tick()
            self.assertIn("Loot path from", win.status.cget("text"))
            # marks: drag a no-go circle, click a checkpoint, right-click to remove; saved in the settings
            Ev = type("Ev", (), {})

            def ev(x, y):
                e = Ev()
                e.x, e.y = x, y
                return e
            win.set_plan_tool("avoid")
            win._press(ev(300, 300))
            win._move(ev(340, 300))
            win._released(ev(340, 300))
            self.assertEqual(len(win.avoid_circles), 1)
            self.assertAlmostEqual(win.avoid_circles[0][2], 40 / win.zoom, places=3)
            win.set_plan_tool("checkpoint")
            win._press(ev(500, 200))
            win._released(ev(500, 200))
            self.assertEqual(len(win.checkpoints), 1)
            self.assertEqual(len(app.cfg["map_plan_marks"]["checkpoints"]), 1)
            win._changed()
            win._render()
            self.assertIn("1 checkpoint", win.status.cget("text"))
            win._right_click(ev(500, 200))
            self.assertEqual(win.checkpoints, [])
            win._right_click(ev(305, 300))
            self.assertEqual(win.avoid_circles, [])
            win.add_checkpoint(1200, 900)
            win.clear_plan_marks()
            self.assertEqual(win.plan_marks()["checkpoints"], [])
            win.set_plan_tool("landing")
            win.set_plan_mode(False)
            win.set_loot(mt.loot_path({"a": {"x": 1000, "y": 0}}, (0, 0)))
            win._changed()
            win._tick()
            self.assertIn("Loot path", win.status.cget("text"))
            import os

            home = tempfile.mkdtemp()
            old_profile = os.environ.get("USERPROFILE")
            os.environ["USERPROFILE"] = home
            try:
                win.save_image()
            finally:
                if old_profile is not None:
                    os.environ["USERPROFILE"] = old_profile
            self.assertEqual(len(list((Path(home) / "Pictures" / "Chest Tracker").glob("*.png"))), 1)
            # the map picture always covers the view: zoomed all the way out, dragged or following near an edge
            cw, ch = win.canvas.winfo_width(), win.canvas.winfo_height()
            for _ in range(30):
                win.zoom_by(1 / 1.5)
            self.assertAlmostEqual(win.zoom, max(cw, ch) / mt.MAP_SIZE, places=6)
            win.center = (-500, 5000)
            win._clamp_view()
            x0, y0 = win.center[0] - cw / 2 / win.zoom, win.center[1] - ch / 2 / win.zoom
            x1, y1 = x0 + cw / win.zoom, y0 + ch / win.zoom
            self.assertTrue(x0 >= -1e-6 and y0 >= -1e-6 and x1 <= mt.MAP_SIZE + 1e-6 and y1 <= mt.MAP_SIZE + 1e-6)
            win.whole_island()
            self.assertGreaterEqual(win.zoom, max(cw, ch) / mt.MAP_SIZE)
            self.assertTrue(win._geometry_on_screen("700x700+-1920+0"))
            self.assertFalse(win._geometry_on_screen("nonsense"))
            frame_host = tk.Frame(root)
            embedded = mw.MapWindow(app, m, lambda: None, parent=frame_host)
            frame_host.destroy()  # the host rebuilds: the old map's loop must stop without errors
            embedded._tick()
        finally:
            win.playing = False
            root.destroy()


if __name__ == "__main__":
    unittest.main()
