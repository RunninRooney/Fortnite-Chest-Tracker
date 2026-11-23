"""The app window: the numbers it shows (app_model.py) and a smoke test of every page (app_window.py).

Run:  python -m unittest discover -s tests
Replay entries are shaped like replays.json and maps/*.map.json on the developer's PC (Nov 19, 2026).
"""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import app_model as M  # noqa: E402
import chest_tracker as ct  # noqa: E402
import treasures as T  # noqa: E402
_isolate.check(ct)

NOW = time.mktime((2026, 11, 19, 15, 0, 0, 0, 0, -1))
ME = {"runninrobin"}

# a solo game read live and confirmed by its replay, which the map also read
SOLO = {"started": NOW - 3600, "ended": NOW - 3000, "chests": 70, "seen": 52, "manual": 0, "adjust": 0,
        "replay": "UnsavedReplay-A.replay", "confirmed": {"opened": 48, "broken": 18}}
# a game imported from a sibling's replay: I was their teammate
IMPORTED = {"started": NOW - 2 * 86400, "ended": NOW - 2 * 86400 + 900, "chests": 16, "seen": 0, "manual": 0,
            "adjust": 0, "replay": "UnsavedReplay-B.replay", "imported": True, "confirmed": {"opened": 9, "broken": 7}}
# someone else played on this PC
ABSENT = {"started": NOW - 5400, "ended": NOW - 4000, "chests": 0, "seen": 0, "manual": 0, "adjust": 0,
          "replay": "UnsavedReplay-C.replay", "confirmed": {"opened": 0, "broken": 0}}
FILES = {
    "UnsavedReplay-A.replay": {"status": "confirmed", "how": "recorder", "recorder": "RunninRobin", "session": "sessA",
                               "started": NOW - 3590, "teammates": [], "raw": {"opened": 48, "broken": 18}},
    "UnsavedReplay-B.replay": {
        "status": "imported", "how": "teammate", "recorder": "SupremeLlama15", "session": "sessB", "started": NOW - 2 * 86400,
        "raw": {"opened": 8, "broken": 3, "recorder_name": "SupremeLlama15", "recorder_platform": "WIN"},
        "teammates": [
            {"name": "RunninRobin", "platform": "WIN", "bot": False, "opened": 9, "broken": 7, "build_breaks": 2},
            {"name": "jFalcon", "platform": "PS5", "bot": False, "opened": 55, "broken": 2, "build_breaks": 0},
            {"name": "Bot123", "platform": "", "bot": True, "opened": 3, "broken": 0}]},
    # the same match recorded twice (another file name): its squad counts once
    "UnsavedReplay-B2.replay": {
        "status": "imported", "how": "teammate", "recorder": "SupremeLlama15", "session": "sessB", "started": NOW - 2 * 86400,
        "raw": {"opened": 8, "broken": 3, "recorder_platform": "WIN"},
        "teammates": [{"name": "jFalcon", "platform": "PS5", "opened": 55, "broken": 2}]},
    "UnsavedReplay-C.replay": {"status": "confirmed", "how": "absent", "recorder": "Falcon", "session": "sessC",
                               "started": NOW - 5400, "teammates": [], "raw": {"opened": 30, "broken": 4}},
}
MAPS = {"UnsavedReplay-A.replay": {
    "file": "UnsavedReplay-A.replay", "length": 867.2, "map": {
        "players": [{"name": "RunninRobin", "recorder": True, "kills": [[96.6, 1, 2], [109.8, 3, 4]], "route": []},
                    {"name": "Other", "recorder": False, "kills": [[5, 1, 1]]}],
        "chests": [{"who": "RunninRobin", "by_build": True, "broken": True, "source": "opener"},
                   {"who": "RunninRobin", "by_build": True, "broken": True, "source": "search"},
                   {"who": "RunninRobin", "by_build": False, "broken": False, "source": "opener"},
                   {"who": "Other", "by_build": True, "broken": True, "source": "opener"}]}}}
FINDS = [{"match": "sessA", "t": 212.0, "prize": "DelilahPrize:dpid_item21", "name": "PPPP", "kind": "jam track",
          "when": NOW - 3600 + 212},
         {"match": "UnsavedReplay-B.replay", "t": 95.0, "prize": T.PIRATE_SHOP, "name": "Pirate Shop Access",
          "kind": "pirate shop", "when": NOW - 2 * 86400 + 95}]


def rows(current=None):
    return M.match_rows([IMPORTED, ABSENT, SOLO], current, FILES, MAPS, FINDS, ME)


class MatchRowTests(unittest.TestCase):
    def test_newest_first_with_the_current_game_on_top(self):
        cur = {"started": NOW - 60, "chests": 3, "seen": 3, "replay": None, "confirmed": None, "finished": None}
        r = rows(cur)
        self.assertTrue(r[0]["current"])
        self.assertEqual(r[0]["source"], "Live (playing)")
        self.assertIsNone(r[0]["broken"])  # not known before the replay is read
        self.assertEqual([x["replay"] for x in r[1:]],
                         ["UnsavedReplay-A.replay", "UnsavedReplay-C.replay", "UnsavedReplay-B.replay"])

    def test_a_finished_empty_current_game_is_left_out(self):
        cur = {"started": NOW - 60, "chests": 0, "seen": 0, "replay": None, "confirmed": None, "finished": NOW - 10}
        self.assertFalse(any(x["current"] for x in rows(cur)))

    def test_opened_broken_and_map_numbers(self):
        a = next(x for x in rows() if x["replay"] == "UnsavedReplay-A.replay")
        # the replay opened 48, but the game counts 70 (pop-ups it missed): the parts add up to the game
        self.assertEqual((a["chests"], a["opened"], a["broken"]), (70, 52, 18))
        self.assertEqual(a["by_build"], 2)  # mine (far-away ones count too), not the other player's
        self.assertEqual(a["kills"], 2)
        self.assertAlmostEqual(a["length"], 867.2)
        self.assertEqual(a["source"], "Live + replay")
        self.assertEqual([t["name"] for t in a["treasures"]], ["PPPP"])  # matched by the replay's session id
        self.assertEqual(a["squad"], [])

    def test_teammate_replay_without_a_map(self):
        b = next(x for x in rows() if x["replay"] == "UnsavedReplay-B.replay")
        self.assertEqual(b["by_build"], 2)  # from my entry in the teammate's replay
        self.assertIsNone(b["kills"])
        self.assertAlmostEqual(b["length"], 900)
        self.assertEqual(b["source"], "Replay")
        self.assertEqual([p["name"] for p in b["squad"]], ["SupremeLlama15", "jFalcon"])  # no bots, not me
        self.assertEqual(b["squad"][1]["platform"], "PlayStation")
        self.assertEqual(b["squad"][1]["chests"], 57)
        self.assertTrue(T.describe(b["treasures"][0]["prize"])["pirate"])  # matched by the replay's file name

    def test_squad_total_per_game(self):
        b = next(x for x in rows() if x["replay"] == "UnsavedReplay-B.replay")
        self.assertEqual(b["squad_total"], 16 + 11 + 57)  # me + SupremeLlama15 (recorder) + jFalcon
        a = next(x for x in rows() if x["replay"] == "UnsavedReplay-A.replay")
        self.assertIsNone(a["squad_total"])  # solo: just my count
        h = {"started": NOW - 100, "chests": 5, "replay": None, "match": "m1", "credited_by": "Falcon", "seen": 3,
             "confirmed": {"opened": 4, "broken": 1}}
        r = M.match_rows([h], None, {}, {}, [], ME)[0]
        self.assertIsNone(r["squad_total"])  # no replay here: my count only, nothing made up
        self.assertEqual(r["source"], "Live + Falcon's replay")
        self.assertEqual(M.summary(rows(), NOW)["squad_chests"], 70 + 84 + 0)

    def test_someone_elses_match(self):
        c = next(x for x in rows() if x["replay"] == "UnsavedReplay-C.replay")
        self.assertTrue(c["not_me"])
        self.assertEqual(c["source"], "Played by Falcon")

    def test_credited_game(self):
        h = {"started": NOW - 100, "chests": 5, "replay": None, "match": "m1", "credited_by": "Falcon",
             "imported": True, "confirmed": {"opened": 4, "broken": 1}}
        r = M.match_rows([h], None, {}, {}, [], ME)
        self.assertEqual(r[0]["source"], "From Falcon's replay")

    def test_no_names_means_the_recorder_is_me(self):
        a = M.match_rows([SOLO], None, FILES, MAPS, [], set())[0]
        self.assertEqual((a["by_build"], a["kills"]), (2, 2))


class SummaryTests(unittest.TestCase):
    def test_totals(self):
        s = M.summary(rows(), NOW)
        self.assertEqual(s["games"], 3)
        self.assertEqual(s["chests"], 86)
        self.assertEqual(s["broken"], 25)
        self.assertEqual(s["by_build"], 4)  # (far-away chests count too)
        self.assertEqual(s["treasures"], 2)
        self.assertEqual(s["best"], 70)
        self.assertEqual(s["today"], 70)
        self.assertEqual(s["today_games"], 2)

    def test_playtime_and_chests_a_minute(self):
        r = rows()
        a = next(x for x in r if x["replay"] == "UnsavedReplay-A.replay")
        self.assertAlmostEqual(a["play"], 867.2)  # the replay's length
        self.assertAlmostEqual(a["per_min"], 70 / (867.2 / 60))
        s = M.summary(r, NOW)
        self.assertAlmostEqual(s["play_s"], 867.2 + 900)  # not someone else's game on this PC
        self.assertAlmostEqual(s["per_min"], 86 / (1767.2 / 60))
        self.assertAlmostEqual(s["today_per_min"], a["per_min"])
        self.assertEqual(s["untimed"], 0)
        self.assertEqual((M.fmt_playtime(1767.2), M.fmt_playtime(3 * 3600 + 5 * 60), M.fmt_playtime(0)),
                         ("29m", "3h 05m", "-"))
        self.assertEqual((M.fmt_rate(4.84), M.fmt_rate(None)), ("4.8", "-"))

    def test_playtime_without_a_replay(self):
        ended = {"started": NOW - 3000, "finished": NOW - 3000 + 600, "ended": NOW - 2000, "chests": 30, "replay": None}
        guess = {"started": NOW - 9000, "ended": NOW - 9000 + 1000, "chests": 20, "replay": None}
        untimed = {"started": NOW - 20000, "ended": NOW - 20000 + 4 * 3600, "chests": 10, "replay": None}  # a gap
        playing = {"started": NOW - 300, "chests": 12, "replay": None, "confirmed": None, "finished": None}
        r = {x["started"]: x for x in M.match_rows([ended, guess, untimed], playing, {}, {}, [], ME, now=NOW)}
        self.assertEqual((r[NOW - 3000]["play"], r[NOW - 3000]["play_est"]), (600, False))  # the match's end
        self.assertEqual((r[NOW - 9000]["play"], r[NOW - 9000]["play_est"]), (1000, True))  # to the next game
        self.assertIsNone(r[NOW - 20000]["play"])
        self.assertEqual(r[NOW - 300]["play"], 300)  # the match being played
        s = M.playtime(list(r.values()))
        self.assertEqual((s["play_s"], s["untimed"], s["play_est"]), (600 + 1000 + 300, 1, True))
        self.assertAlmostEqual(s["per_min"], 62 / (1900 / 60))  # the untimed game's 10 chests left out

    def test_empty(self):
        s = M.summary([], NOW)
        self.assertEqual((s["games"], s["avg"], s["best"], s["best_when"]), (0, 0.0, 0, None))

    def test_filter_by_period(self):
        r = rows()
        self.assertEqual(len(M.filter_rows(r, 1, NOW)), 2)
        self.assertEqual(len(M.filter_rows(r, 7, NOW)), 3)
        self.assertEqual(len(M.filter_rows(r, None, NOW)), 3)

    def test_daily(self):
        days = M.daily(rows(), 14, NOW)
        self.assertEqual(len(days), 14)
        self.assertEqual(days[-1]["chests"], 70)
        self.assertEqual((days[-1]["opened"], days[-1]["broken"]), (52, 18))
        self.assertEqual(days[-3]["chests"], 16)
        self.assertEqual(sum(d["chests"] for d in days), 86)


class PeopleTests(unittest.TestCase):
    def test_played_with_counts_each_match_once(self):
        p = M.played_with(FILES, ME)
        names = [x["name"] for x in p]
        self.assertEqual(sorted(names), ["SupremeLlama15", "jFalcon"])  # not me, no bots, not someone else's match
        j = next(x for x in p if x["name"] == "jFalcon")
        self.assertEqual((j["games"], j["chests"], j["best"], j["platform"]), (1, 57, 57, "PlayStation"))

    def test_platform_names(self):
        self.assertEqual(M.platform_name("PS5"), "PlayStation")
        self.assertEqual(M.platform_name("XSX"), "Xbox")
        self.assertEqual(M.platform_name(""), "")


class TreasureViewTests(unittest.TestCase):
    def test_rows_and_collection(self):
        peers = [{"name": "Falcon", "treasures": {"count": 1, "recent": [{"prize": "DelilahPrize:dpid_item21",
                                                                          "when": NOW - 50}]}}]
        r = M.treasure_rows(FINDS, peers)
        self.assertEqual([x["who"] for x in r], ["Falcon", "You", "You"])
        self.assertTrue(r[2]["pirate"])
        weeks = M.collection(r)
        self.assertEqual([w["week"] for w in weeks][-1], "Pirate Shop")
        self.assertEqual(weeks[-1]["items"][0]["found_by"], ["You"])
        pppp = next(i for w in weeks for i in w["items"] if i["name"] == "PPPP")
        self.assertEqual(sorted(pppp["found_by"]), ["Falcon", "You"])
        self.assertEqual(sum(len(w["items"]) for w in weeks), 27)

    def test_sprite_gizmos(self):
        finds = [{"prize": "gizmo", "name": "Sprite gizmo", "when": NOW - k * 100} for k in range(3)]
        peers = [{"name": "Falcon", "treasures": {"recent": [{"prize": "gizmo", "when": NOW - 7}]}}]
        r = M.treasure_rows(finds + FINDS, peers)
        giz = [x for x in r if x["kind"] == "sprite gizmo"]
        self.assertEqual(len(giz), 4)
        self.assertEqual({x["name"] for x in giz}, {"Sprite gizmo"})
        self.assertNotEqual(max(r, key=lambda x: x["rarity"])["kind"], "sprite gizmo")  # never "rarest"
        last = M.collection(r)[-1]["items"]
        self.assertEqual(last[1]["prize"], "gizmo")
        self.assertEqual(last[1]["found_by"], ["You ×3", "Falcon"])
        self.assertEqual(T.ODDS["sprite gizmo"], 127)
        row = next(x for x in M.odds_table(1000, 2 / 3, 0.5) if x["kind"] == "sprite gizmo")
        self.assertEqual((row["label"], row["more"]), ("Sprite gizmo", 0))
        row = next(x for x in M.odds_table(60, 0.5, 0.5) if x["kind"] == "sprite gizmo")  # every chest counts
        self.assertTrue(row["any_chest"])
        self.assertAlmostEqual(row["chance"], M.chance_by_now(60, 127))
        self.assertEqual(row["need"], M.chests_for(0.5, 127))
        car = next(x for x in M.odds_table(60, 1.0, 0.5) if x["kind"] == "car")  # Epic: every chest counts
        self.assertTrue(car["any_chest"])
        self.assertAlmostEqual(car["chance"], M.chance_by_now(60, 7813))
        self.assertEqual(car["need"], M.chests_for(0.5, 7813))
        self.assertFalse(T.TIERED_ONLY)

    def test_the_same_find_twice_is_one_row(self):
        self.assertEqual(len(M.treasure_rows(FINDS + FINDS, [])), 2)


class PlayTimesTests(unittest.TestCase):
    def test_grid_by_weekday_and_time_of_day(self):
        g = M.play_times(rows(), 60, NOW)
        self.assertEqual(len(g), 7)
        self.assertTrue(all(len(r) == 6 for r in g))
        self.assertEqual(sum(map(sum, g)), 86)  # every chest in the last 60 days lands in one cell
        lt = time.localtime(SOLO["started"])
        self.assertGreaterEqual(g[lt.tm_wday][lt.tm_hour // 4], 70)

    def test_old_games_are_left_out(self):
        self.assertEqual(sum(map(sum, M.play_times(rows(), 1, NOW))), 70)


class OddsTests(unittest.TestCase):
    def test_chance_and_chests_needed(self):
        self.assertAlmostEqual(M.chance_by_now(0, 137500), 0.0)
        self.assertAlmostEqual(M.chance_by_now(137500, 137500), 1 - (1 - 1 / 137500) ** 137500, places=9)
        n50 = M.chests_for(0.5, 137500)
        self.assertGreaterEqual(M.chance_by_now(n50, 137500), 0.5)
        self.assertLess(M.chance_by_now(n50 - 1, 137500), 0.5)
        self.assertEqual(n50, 95308)
        self.assertGreater(M.chests_for(0.9, 137500), n50)

    def test_odds_live_in_treasures(self):
        self.assertEqual(T.ODDS["pirate shop"], 137500)
        self.assertTrue(T.is_tiered("Tiered_Chest_Athena") and T.is_tiered("B_TieredChest_Relics"))
        self.assertFalse(T.is_tiered("AlwaysSpawn_NormalChest"))

    def test_tiered_share_from_my_map_chests(self):
        rec = {"map": {"players": [{"name": "RunninRobin", "recorder": True}, {"name": "Other"}],
                       "chests": [{"who": "RunninRobin", "class": "Tiered_Chest_Athena"}] * 30
                       + [{"who": "RunninRobin", "class": "AlwaysSpawn_NormalChest"}] * 10
                       + [{"who": "Other", "class": "AlwaysSpawn_NormalChest"}] * 50}}
        share, known = M.tiered_share({"a": rec}, ME)
        self.assertEqual((share, known), (0.75, 40))
        self.assertAlmostEqual(M.tiered_share({}, ME)[0], 2 / 3)  # nothing known yet

    def test_odds_table_and_pace(self):
        rows_ = M.odds_table(1000, 2 / 3, 0.5)
        self.assertEqual([r["kind"] for r in rows_][0], "pirate shop")
        jam = next(r for r in rows_ if r["kind"] == "jam track")
        self.assertEqual(jam["more"], 0)  # 1000 chests already give 50% for a 1-in-391 prize
        pz = M.pace(rows(), 14, NOW)
        self.assertEqual(pz["days_played"], 2)


class FormatTests(unittest.TestCase):
    def test_when(self):
        self.assertTrue(M.fmt_when(NOW - 3600, NOW).startswith("Today "))
        self.assertTrue(M.fmt_when(NOW - 86400, NOW).startswith("Yesterday "))
        self.assertEqual(M.fmt_when(0, NOW), "-")
        self.assertEqual(M.fmt_length(867.2), "14:27")
        self.assertEqual(M.fmt_length(None), "-")
        self.assertEqual(M.fmt_ago(NOW - 7200, NOW), "2 h ago")


# --------------------------------------------------------------------------------------------
# The window itself, with a stand-in for the running app
# --------------------------------------------------------------------------------------------
class FakeReplays:
    def __init__(self, db):
        self.db = db
        self.reattributed = 0

    def my_names(self):
        return ME

    def reattribute(self):
        self.reattributed += 1


class FakeNet:
    status = "connected"
    wake = threading.Event()


class FakeApp:
    """Just what the window and the settings page use. (Its module is set to chest_tracker below, so the
    window finds SettingsWindow the way it does in the real app.)"""

    def __init__(self, root, tmp: Path):
        self.root = root
        self.cfg = dict(ct.DEFAULT_CONFIG, name="Robin", fortnite_names=["RunninRobin"], team_code="sample-code")
        self.stats = ct.Stats(tmp / "stats.json")
        self.stats.history = [dict(IMPORTED), dict(ABSENT)]
        self.team = ct.Team(self, tmp / "team.json")
        self.treasures = T.TreasureLog(tmp / "treasures.json")
        self.treasures.items = list(FINDS)
        db = ct.ReplayDB(tmp / "replays.json")
        db.files = dict(FILES)
        self.replays = FakeReplays(db)
        self.net = FakeNet()
        self.map = None
        self.toasts, self.opened_map, self.quit_called = [], 0, 0

    def match_in_progress(self):
        return False

    def toast(self, text, seconds=4.0):
        self.toasts.append(text)

    def set_opacity(self, v):
        self.cfg["bg_opacity"] = round(v, 2)

    def set_scale(self, v):
        self.cfg["overlay_scale"] = round(v, 2)

    def guest_names(self):
        return {}

    def guest_stats(self):
        return {}

    def apply_credits(self, m=None):
        pass

    def open_map(self):
        self.opened_map += 1

    def quit(self):
        self.quit_called += 1


FakeApp.__module__ = "chest_tracker"


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk

            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception as e:  # no display
            raise unittest.SkipTest(f"no Tk: {e}")
        # Every app window these tests open is fully transparent: it still lays out and draws (so the tests
        # check the real thing) but nothing pops up on the screen while the suite runs.
        import app_window

        real_init = app_window.AppWindow.__init__

        def hidden_init(self, app):
            real_init(self, app)
            try:
                self.win.attributes("-alpha", 0.0)
            except tk.TclError:
                pass

        cls._hide = mock.patch.object(app_window.AppWindow, "__init__", hidden_init)
        cls._hide.start()

    @classmethod
    def tearDownClass(cls):
        cls._hide.stop()
        for job in cls.root.tk.splitlist(cls.root.tk.call("after", "info")):  # the windows' 1 s refresh timers
            try:
                cls.root.after_cancel(job)
            except Exception:
                pass
        cls.root.destroy()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="chest_appwin_"))
        p = mock.patch.object(ct, "CONFIG_PATH", self.tmp / "config.json")
        p.start()
        self.addCleanup(p.stop)
        self.app = FakeApp(self.root, self.tmp)
        import app_window

        self.aw = app_window
        self.w = app_window.show(self.app, "overview")
        self.addCleanup(lambda: self.w.win.destroy())

    def pump(self):
        for _ in range(3):
            self.root.update()

    def test_every_page_builds_and_fills(self):
        import app_theme

        app_theme.PAINT_ERRORS.clear()
        self.assertEqual(float(self.w.win.attributes("-alpha")), 0.0)  # (the tests never pop a window up)
        for size in ("1400x900", "1000x660"):  # roomy and small windows
            self.w.win.geometry(size)
            for key, _label, _glyph in self.aw.PAGES:
                self.w.go(key)
                for _ in range(3):
                    self.pump()
                    time.sleep(0.05)
                self.assertEqual(self.w.page, key)
        self.assertEqual(self.w.title.cget("text"), "Settings")
        self.assertEqual(app_theme.PAINT_ERRORS, [])  # every card drew without an error

    def test_overview_numbers(self):
        self.pump()
        self.assertEqual(self.w.ov["total"][0].cget("text"), f"{self.app.stats.total:,}")
        self.assertEqual(len(self.w.recent.rows), 2)

    def test_this_game_shows_the_mid_match_replay_count(self):
        self.app.match_in_progress = lambda: True
        self.app.stats.partial = {"opened": 12, "broken": 4}
        self.w._key = None
        self.w._update()
        self.assertIn("12 opened + 4 broken", self.w.ov["game"][1].cget("text"))

    def test_matches_period_and_detail(self):
        self.w.go("matches")
        self.w._period(None)
        self.pump()
        self.assertEqual(len(self.w.m_table.rows), 2)
        self.w.m_table.select(0)
        self.pump()
        texts = self.w.m_detail_lines  # what the detail card shows
        self.assertTrue(any("Played by Falcon" in t or "Replay" in t for t in texts), texts)
        # sorting by chests puts the biggest first
        self.w.m_table._head_click(type("E", (), {"x": self.w.m_table.columns[2]["_x"] + 5})())
        self.assertEqual(self.w.m_table.rows[0]["data"]["chests"], 16)

    def test_reopening_reuses_the_window(self):
        self.w.hide()
        self.assertEqual(self.app.cfg["app_page"], "overview")
        again = self.aw.show(self.app, "treasures")
        self.assertIs(again, self.w)
        self.assertEqual(self.w.page, "treasures")

    def test_settings_page_saves_like_the_settings_window(self):
        self.w.go("settings")
        self.pump()
        panel = self.w.settings_panel
        self.assertIsInstance(panel, ct.SettingsWindow)
        panel.fn.set("RunninRobin, RobinPS")
        panel._save_fortnite_names()
        self.assertEqual(self.app.cfg["fortnite_names"], ["RunninRobin", "RobinPS"])
        self.assertTrue(ct.CONFIG_PATH.exists())
        panel.op.set(40)
        self.assertEqual(self.app.cfg["bg_opacity"], 0.4)
        lock = panel.lock.get()
        panel.lock.set(not lock)
        panel._lock_changed()
        self.assertEqual(self.app.cfg["lock_in_match"], not lock)
        panel.full.set(True)  # keep the full overlay (everyone's chests) open
        panel._overlay_full_changed()
        self.assertTrue(self.app.cfg["overlay_always_full"])
        panel.glass.set(False)  # the classic overlay look
        panel._overlay_style_changed()
        self.assertEqual(self.app.cfg["overlay_style"], "classic")
        live = bool(self.app.cfg.get("live_replay", True))
        panel.live_replay.set(not live)
        panel._live_replay_changed()
        self.assertEqual(self.app.cfg["live_replay"], not live)
        panel.name.set("Roo")
        self.w.hide()  # closing the window saves what was typed
        self.assertEqual(self.app.cfg["name"], "Roo")

    def test_map_buttons_use_show_view(self):
        calls = []

        class FakeMapWindow:
            def show_view(self, view="match", kind=None, match_file=None):
                calls.append((view, kind, match_file))

            def compare(self, files):
                calls.append(("compare", files))

        self.app._map_window = FakeMapWindow()  # no map data here: the page falls back to the map window
        self.w._open_map(True, kind="landing")
        self.w._open_map(False, "UnsavedReplay-A.replay")
        self.w._compare(["a.replay", "b.replay"])
        self.assertEqual(calls, [("heat", "landing", None), ("match", None, "UnsavedReplay-A.replay"),
                                 ("compare", ["a.replay", "b.replay"])])
        self.assertEqual(self.w.page, "map")

    def test_ctrl_click_picks_several_games_to_compare(self):
        self.w.go("matches")
        self.w._period(None)
        self.pump()
        t = self.w.m_table
        marks = []
        t.on_marks = marks.append
        ev = lambda y, state=0: type("E", (), {"y": y, "state": state})()  # noqa: E731
        t._click(ev(5))
        t._click(ev(t.row_h + 5, 0x4))  # Ctrl+click
        self.assertEqual(len(t.marked_data()), 2)
        self.assertEqual(len(marks[-1]), 2)
        self.w._key = None  # the window's regular refresh keeps what's picked
        self.w._update()
        self.assertEqual(len(t.marked_data()), 2)
        self.assertEqual(self.w.m_compare.cget("text"), "")  # (no map records here, so nothing to compare)
        t._click(ev(5))  # a plain click starts over
        self.assertEqual(t.marked_data(), [])

    def test_map_page_embeds_the_map_with_drop_spots(self):
        import map_tracker

        mgr = map_tracker.MapManager(self.app, self.tmp, self.tmp, self.tmp / "none.exe", lambda: None, lambda: False)
        rec = {"file": "UnsavedReplay-A.replay", "start": NOW - 3600, "length": 867.2, "season": "43", "recorder": "RunninRobin",
               "map": {"players": [{"name": "RunninRobin", "recorder": True, "landing": [60, 57460, 11635], "kills": [],
                                    "route": [[60, 57460, 11635, 100], [500, 58000, 12000, 100]]}],
                       "chests": [{"t": 70, "who": "RunninRobin", "x": 57955, "y": 11960, "broken": False,
                                   "source": "opener"}], "spots": []}}
        mgr.replay_maps = {rec["file"]: rec}
        self.app.map = mgr
        self.app.open_map = lambda: None
        self.w._snap = None
        self.w.go("map")
        self.pump()
        self.assertIsNotNone(self.w.embedded_map)
        mw = self.w.embedded_map
        self.assertTrue(mw.embedded)
        self.assertTrue(mw._visible())
        # embedded: showing goes through the page, never a window of its own or cfg["map_window"]
        self.app.cfg["map_window"] = False
        self.w.go("overview")
        self.pump()
        self.assertFalse(mw._visible())  # hidden page: the map doesn't draw
        mw.show()
        self.pump()
        self.assertEqual(self.w.page, "map")
        self.assertFalse(self.app.cfg["map_window"])
        mw.hide()
        self.assertTrue(mw.win.winfo_exists())
        # Space plays / pauses the replay shown in the page
        played = []
        mw.toggle_play = lambda: played.append(1)
        self.assertEqual(self.w._space(type("E", (), {"widget": mw.canvas})()), "break")
        self.assertEqual(played, [1])
        # simple mode by default: the map's own toolbars are hidden; "All controls" brings them back
        if hasattr(mw, "set_simple"):
            self.assertTrue(mw.simple)
            self.w._toggle_simple()
            self.assertFalse(mw.simple)
            self.w._toggle_simple()
            self.assertTrue(mw.simple)
            shown = mw.show_panel.get()
            menus = []
            self.w._menu = menus.append
            self.w._map_more_menu()
            labels = [label for label, _cmd in menus[-1]]
            self.assertTrue(any("stats panel" in lb for lb in labels) and any("picture" in lb for lb in labels), labels)
            dict(menus[-1])[labels[0]]()  # show / hide the stats panel
            self.assertEqual(mw.show_panel.get(), not shown)
        # the side panel: pick a game, tick games to overlay them, events of the picked game
        calls = []
        real = (mw.show_match, mw.compare, mw.jump)
        mw.show_match = lambda f: calls.append(("show", f))
        mw.compare = lambda fs: calls.append(("compare", list(fs)))
        mw.jump = lambda t: calls.append(("jump", t))
        self.w._snap = None
        self.w._refresh_page(force=True)
        self.w._fill_side(self.w._snap)
        titles = [r.get("title") for r in self.w.games_list.rows]
        self.assertIn("Live / latest game", titles)
        self.w._game_checked("UnsavedReplay-A.replay", True)
        self.w._game_checked("UnsavedReplay-B.replay", True)
        self.assertEqual(calls[-1], ("compare", ["UnsavedReplay-A.replay", "UnsavedReplay-B.replay"]))
        self.assertIn("Overlaying 2 games", self.w.side_foot_label.cget("text"))
        self.w._game_clicked("UnsavedReplay-A.replay")  # one game: clears the overlay
        self.assertEqual(calls[-1], ("show", "UnsavedReplay-A.replay"))
        self.assertEqual(self.w.map_overlay, [])
        self.w._show_side_tab("events")
        texts = [r.get("title", "") for r in self.w.events_list.rows]
        self.assertTrue(any("You landed" in t for t in texts), texts)
        self.assertTrue(any("You opened a chest" in t for t in texts), texts)
        ev = next(r for r in self.w.events_list.rows if "You opened a chest" in r.get("title", ""))
        self.w._event_clicked(ev["data"])
        self.assertEqual(calls[-1], ("jump", 70))
        self.w._show_side_tab("games")
        mw.show_match, mw.compare, mw.jump = real
        self.w._show_map_tab("drops")
        self.pump()
        self.assertEqual(len(self.w.drops_table.rows), 1)
        self.assertEqual(self.w.drops_table.rows[0]["data"]["games"], 1)
        self.w._open_map(False, rec["file"])  # shows the match in the page, not a window
        self.assertEqual(self.w.map_tab, "map")
        self.assertEqual(self.w.embedded_map.view.get(), "match")
        # the Matches page gets "landed at" from the map
        self.app.stats.history.append(dict(SOLO))
        a = next(r for r in self.w.snapshot()["rows"] if r["replay"] == rec["file"])
        self.assertTrue(a["on_map"])
        self.assertIn("alive", a["map"])

    def test_intro_card_can_be_dismissed_and_brought_back(self):
        self.pump()
        self.assertTrue(self.w.intro.winfo_ismapped())
        self.w._hide_intro()
        self.pump()
        self.assertFalse(self.w.intro.winfo_ismapped())
        self.assertTrue(self.app.cfg["app_intro_done"])
        self.w._show_intro()  # the "?" button in the header
        self.pump()
        self.assertTrue(self.w.intro.winfo_ismapped())

    def test_today_badge_is_never_red(self):
        self.pump()
        badge = self.w._today_badge
        self.assertTrue(badge is None or badge[1] in ("green", "muted"))

    def test_widgets_on_cards_are_drawn_above_the_glass(self):
        self.w.go("settings")
        self.pump()
        panel = self.w.settings_panel
        for wdg in (panel.op, panel.lock_sw, panel.name_e):
            self.assertTrue(wdg.winfo_ismapped(), wdg)
            card = next(c for c in self.w.pages["settings"].inner.cards if wdg in c._puts)
            stack = self.root.tk.splitlist(self.root.tk.call("winfo", "children", str(card.master)))  # bottom first
            self.assertGreater(stack.index(str(wdg)), stack.index(str(card)), f"{wdg} is under its card")

    def test_more_detail_adds_columns(self):
        self.w.go("matches")
        self.w._period(None)
        self.pump()
        simple = len(self.w.m_table.columns)
        self.w.m_more.set(True)
        self.w._more_cols()
        self.assertEqual(len(self.w.m_table.columns), simple + 3)
        self.assertEqual(len(self.w.m_table.rows[0]["values"]), simple + 3)
        titles = [c["title"] for c in self.w.m_table.columns]
        self.assertIn("Squad total", titles)
        self.assertNotIn("By build", titles)  # one "broken" number everywhere

    def test_colour_themes_switch_and_rebuild_the_window(self):
        import app_theme

        self.addCleanup(lambda: app_theme.use_theme("arctic"))
        self.assertEqual(len(app_theme.THEME_ORDER), 6)
        ember_accent = app_theme.ACCENT
        self.w.go("settings")
        self.pump()
        self.w.switch_theme("ocean")
        w2 = self.app._app_window
        self.addCleanup(lambda: w2.win.destroy())
        self.assertIsNot(w2, self.w)
        self.assertEqual(self.app.cfg["app_theme"], "ocean")
        self.assertEqual(w2.page, "settings")  # same page after the switch
        self.assertNotEqual(app_theme.ACCENT, ember_accent)
        self.assertEqual(app_theme.THEMES["ocean"]["accent"], app_theme.ACCENT_RGB)
        app_theme.PAINT_ERRORS.clear()
        for key, _label, _glyph in self.aw.PAGES:  # every page draws in the new colours
            w2.go(key)
            for _ in range(3):
                self.pump()
                time.sleep(0.05)
        self.assertEqual(app_theme.PAINT_ERRORS, [])

    def test_every_theme_has_its_own_accent_and_swatch(self):
        import app_theme

        accents = {app_theme.THEMES[k]["accent"] for k in app_theme.THEME_ORDER}
        self.assertEqual(len(accents), 6)
        for k in app_theme.THEME_ORDER:
            self.assertEqual(app_theme.theme_swatch(k, 120, 72).size, (120, 72))

    def test_key_tab_draws_every_mark(self):
        import map_tracker

        mgr = map_tracker.MapManager(self.app, self.tmp, self.tmp, self.tmp / "none.exe", lambda: None, lambda: False)
        self.app.map = mgr
        self.app.open_map = lambda: None
        self.w.go("map")
        self.pump()
        self.w._show_side_tab("key")
        self.pump()
        self.assertGreater(len(self.w.key_view.canvas.find_all()), 2 * len(self.w.key_view.ITEMS))
        for kind, _t, _s in self.w.key_view.ITEMS:
            self.assertEqual(self.aw.key_icon(kind, 28).size, (28, 28))

    def test_navigation_has_settings_and_map_in_the_middle(self):
        self.w.win.geometry("1400x900")
        for _ in range(4):
            self.pump()
            time.sleep(0.05)
        self.w._layout_nav(self.w.header.winfo_width())
        keys = [it[0] for it in self.w._nav_layout]
        self.assertEqual(sorted(keys), sorted(k for k, _l, _g in self.aw.PAGES))  # every page, Settings too
        mid = next(it for it in self.w._nav_layout if it[0] == "map")
        centre = self.w.nav_card.winfo_x() + mid[-1] + mid[3] / 2
        self.assertLess(abs(centre - self.w.header.winfo_width() / 2), 3)  # Map sits in the middle of the window
        self.assertTrue(mid[5])  # and it's the big one
        self.w._layout_nav(900)  # a narrow window: compact, no overlap with your name on the right
        self.assertTrue(self.w._nav_compact)

    def test_squad_member_page_lists_their_games(self):
        self.w.go("team")
        self.pump()
        self.w.open_player("jFalcon", ["jFalcon"])
        self.pump()
        self.assertEqual(self.w.page, "player")
        rows = [r["data"] for r in self.w.pl_table.rows]
        self.assertEqual(len(rows), 1)  # replays B and B2 are the same match: once
        self.assertEqual((rows[0]["chests"], rows[0]["opened"], rows[0]["broken"]), (57, 55, 2))
        self.assertIn("SupremeLlama15", rows[0]["with"])
        self.assertEqual(self.w._pl["sum"]["games"], 1)
        # "me" goes to my own games
        self.w._person_clicked({"name": "Robin", "me": True})
        self.assertEqual(self.w.page, "matches")

    def test_replay_counts_include_far_away_teammates(self):
        e = FILES["UnsavedReplay-B.replay"]
        c = M.replay_counts(e)
        self.assertEqual(c["SupremeLlama15"], (8, 3))  # the recorder
        self.assertEqual(c["jFalcon"], (55, 2))       # a teammate, near or far: their real count
        self.assertEqual(M.replay_counts(None), {})

    def test_player_games_from_replays(self):
        rows = M.player_games(FILES, MAPS, ["SupremeLlama15"], ME)
        self.assertEqual(len(rows), 1)  # the recorder of B (and B2, the same match)
        self.assertEqual(rows[0]["chests"], 11)
        self.assertEqual(M.player_summary(rows)["best"], 11)
        self.assertEqual(M.player_games(FILES, MAPS, ["nobody"], ME), [])

    def test_map_page_controls(self):
        import map_tracker

        mgr = map_tracker.MapManager(self.app, self.tmp, self.tmp, self.tmp / "none.exe", lambda: None, lambda: False)
        rec = {"file": "UnsavedReplay-A.replay", "start": NOW - 3600, "length": 867.2, "season": "43",
               "map": {"players": [{"name": "RunninRobin", "recorder": True, "landing": [60, 57460, 11635], "kills": [],
                                    "route": [[60, 57460, 11635, 100], [500, 58000, 12000, 100]]},
                                   {"name": "Falcon", "recorder": False, "kills": [],
                                    "route": [[60, 57460, 11635, 100], [500, 58000, 12000, 100]]}],
                       "chests": [{"t": 70, "who": "RunninRobin", "x": 57955, "y": 11960, "broken": False,
                                   "source": "opener"},
                                  {"t": 90, "who": "Falcon", "x": 57955, "y": 11960, "broken": True, "source": "opener"}],
                       "spots": []}}
        mgr.replay_maps = {rec["file"]: rec}
        self.app.map = mgr
        self.app.open_map = lambda: None
        self.app.stats.history.append(dict(SOLO))
        self.w._snap = None
        self.w.go("map")
        self.pump()
        mw = self.w.embedded_map
        self.w._game_clicked(rec["file"])
        self.pump()
        st = self.w.info._state()  # the "this game" panel: the replay's total for the recorder (48 + 18), the map's
        self.assertEqual(st["kind"], "game")  # marks for someone the replay entry doesn't list
        self.assertEqual({q["name"]: q["opened"] + q["broken"] for q in st["people"]}, {"RunninRobin": 66, "Falcon": 1})
        rec["map"]["chests"].append({"t": 95, "who": "Falcon", "x": 1, "y": 1, "broken": False, "source": "search"})
        self.w._snap["files"]["UnsavedReplay-A.replay"] = dict(FILES["UnsavedReplay-A.replay"], recorder="RunninRobin",
                                                               raw={"opened": 48, "broken": 18},
                                                               teammates=[{"name": "Falcon", "opened": 30, "broken": 4}])
        mw.t = None
        st = self.w.info._state()  # the whole game: the replay's real numbers, far-away chests included
        self.assertEqual({q["name"]: q["opened"] + q["broken"] for q in st["people"]}, {"RunninRobin": 66, "Falcon": 34})
        mw.t = 100.0
        st = self.w.info._state()  # so far: the map's marks (the far-away one too), never past the total
        self.assertEqual({q["name"]: q["opened"] + q["broken"] for q in st["people"]}, {"RunninRobin": 1, "Falcon": 2})
        mw.t = None
        mw.app.cfg["console_teammates"] = ["Falcon"]  # squad only: random fills never listed (see test_map)
        self.assertEqual(self.w._map_players(), ["Falcon"])
        # the replay controls under the map: play / pause, seek, speed, who to watch
        pb = self.w.playbar
        pb.refresh(force=True)
        st = pb.state()
        self.assertIsNotNone(st)
        self.assertEqual(st["players"], ["RunninRobin", "Falcon"])
        pb.seek(100)
        self.assertAlmostEqual(pb.state()["t"], 100)
        pb.step(1)
        self.assertAlmostEqual(pb.state()["t"], 100 + pb.SEEK)
        pb.step(-20)
        self.assertAlmostEqual(pb.state()["t"], 0)
        pb.set_speed("10x")
        self.assertEqual(pb.state()["speed"], "10x")
        pb.watch("Falcon")
        self.assertEqual(pb.state()["watched"], "Falcon")
        pb.toggle()
        self.assertTrue(pb.state()["playing"])
        pb.toggle()
        self.assertFalse(pb.state()["playing"])
        self.assertFalse(mw.timeline.winfo_ismapped())  # the map's own timeline row stays hidden in the app
        pb.refresh(force=True)
        lanes = {label: marks for label, _col, marks in pb.lanes}  # one timeline row per player, their chests on it
        self.assertEqual(sorted(lanes), ["Falcon", "RunninRobin"])
        # (his far-away chest, added above, is on his row too)
        self.assertEqual([k for _t, k, _x in lanes["Falcon"] if k in ("chest", "broken")], ["broken", "chest"])
        self.assertIn("chest", [k for _t, k, _x in lanes["RunninRobin"]])
        # what's around the map can be switched off (and is remembered)
        h_full = pb.want_h
        self.w._toggle_view("show_lanes")
        self.assertEqual(pb.lanes, [])
        self.assertLess(pb.want_h, h_full)  # just the buttons and a slim scrub bar
        self.assertFalse(self.app.cfg["app_map_lanes"])
        self.w._toggle_view("show_lanes")
        self.assertEqual(len(pb.lanes), 2)
        self.w._toggle_view("show_list")
        self.assertFalse(self.app.cfg["app_map_list"])
        self.w._toggle_view("show_list")
        self.w._toggle_view("show_players")
        self.assertFalse(self.w.show_players)
        self.w._toggle_view("show_players")
        self.w._set_player_filter("Falcon")  # the games list: only games with Falcon
        self.assertIn("Falcon", self.w.games_list.rows[1]["right"])
        self.w._set_map_mode("heat")
        self.assertEqual(mw.view.get(), "heat")
        self.w._set_map_mode("match")
        self.assertEqual(mw.view.get(), "match")
        # Plan route: the map's loot planner, with its own controls
        modes = []
        mw.set_plan_mode = lambda on: modes.append(on)
        counts = []
        mw.set_plan_count = lambda n: counts.append(n)
        self.w._pick_view("plan")
        self.assertTrue(self.w.plan_on)
        self.assertEqual(self.w._map_mode(), "plan")
        self.assertFalse(self.w.playbar.want_h)  # no replay controls while planning
        self.w.plan_slider.set(40)
        self.assertEqual(counts[-1], 40)
        mw.loot = {"place": "Cursed Cavern", "spots": [("a", 1, 2)] * 12, "chests": 7.6, "metres": 512, "seconds": 140}
        st = self.w.info._state()
        self.assertEqual((st["kind"], st["stops"], st["place"]), ("plan", 12, "Cursed Cavern"))
        tools = []
        mw.set_plan_tool = lambda t: tools.append(t)
        self.w._plan_tool("avoid")
        self.w._plan_tool("checkpoint")
        self.assertEqual(tools, ["avoid", "checkpoint"])
        spread = []
        mw.set_plan_spread = lambda v: spread.append(v)
        mw.plan_per_place = lambda: None
        menus = []
        real_menu, self.w._menu = self.w._menu, menus.append
        self.w._plan_menu()
        self.w._menu = real_menu
        labels = dict(menus[-1])
        pick = next(k for k in labels if "3 per place" in k)
        labels[pick]()
        self.assertEqual(spread, [3])
        self.w._pick_view("match")
        self.assertEqual(modes, [True, False])
        was = mw.show_spots.get()  # the Chests toggle: the chest locations on / off
        self.w._toggle_chests()
        self.assertEqual(mw.show_spots.get(), not was)
        self.assertEqual(self.app.cfg["map_spots"], not was)
        self.w._toggle_chests()
        self.assertEqual(mw.show_spots.get(), was)
        saved = []
        mw.save_image = lambda: saved.append(1)  # Save image is always there
        self.w._save_map_image()
        self.assertEqual(saved, [1])

    def test_odds_page(self):
        self.w.go("treasures")
        self.pump()
        self.w.go("odds")
        self.pump()
        o = self.w._odds
        self.assertEqual(o["chests"], self.app.stats.total)
        self.assertEqual(o["one_in"], 137500)
        self.w._odds_preset(90)
        self.assertEqual(self.w.odds_target, 0.9)
        self.assertGreater(self.w._odds["need"], 0)
        self.assertEqual(len(self.w.odds_table.rows), len(T.ODDS))
        self.w._odds_set_who("squad")
        self.assertTrue(self.w._odds["squad"])

    def test_map_buttons_open_the_map_window(self):
        self.w.go("map")
        self.w._open_map(True)
        self.assertEqual(self.app.opened_map, 1)


if __name__ == "__main__":
    unittest.main()
