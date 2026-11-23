"""Chests today, for every squad member: my own count, sharing it with the team, console guests from replays,
and the overlay's squad table.

Run:  python -m unittest discover -s tests
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
_isolate.check(ct)


class FakeApp:
    def __init__(self, name, code="siblings"):
        d = Path(tempfile.mkdtemp())
        self.cfg = dict(ct.DEFAULT_CONFIG, name=name, team_code=code)
        self.stats = ct.Stats(d / "stats.json")
        self.team = ct.Team(self, d / "team.json")

    def toast(self, text, seconds=4.0):
        pass


class TodayTests(unittest.TestCase):
    def setUp(self):
        ct.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.json"
        self.midnight = ct.local_midnight()

    def test_my_today_counts_todays_games_and_this_one(self):
        a = FakeApp("Ann")
        a.stats.history = [{"started": self.midnight - 3600, "chests": 40},   # yesterday evening
                           {"started": self.midnight + 60, "chests": 12},     # today
                           {"started": self.midnight + 3600, "chests": 7}]
        a.stats.game_started = time.time()
        a.stats.add(3)
        self.assertEqual(a.stats.today(), 22)

    def test_shared_with_the_team(self):
        a, b = FakeApp("Ann"), FakeApp("Ben")
        b.stats.game_started = time.time()
        b.stats.add(5)
        m = b.team.message()
        self.assertEqual(m["today"], 5)
        a.team.update(m, "net")
        ben = next(p for p in a.team.players() if p["name"] == "Ben")
        self.assertEqual(ben["today"], 5)
        me = a.team.players()[0]
        self.assertEqual(me["today"], 0)

    def test_older_tracker_without_today_shows_unknown(self):
        a, b = FakeApp("Ann"), FakeApp("Ben")
        m = b.team.message()
        del m["today"]
        a.team.update(m, "net")
        ben = next(p for p in a.team.players() if p["name"] == "Ben")
        self.assertIsNone(ben["today"])

    def test_yesterdays_message_counts_as_zero_today(self):
        a, b = FakeApp("Ann"), FakeApp("Ben")
        b.stats.game_started = time.time()
        b.stats.add(9)
        m = b.team.message()
        a.team.update(m, "net")
        with a.team.lock:
            p = next(iter(a.team.peers.values()))
            p["ts"] = self.midnight - 60  # last heard from before midnight
            p["games"] = {"m1": 9}        # (offline now, but in my last game, so still listed)
        a.team.last_squad = lambda now=None: (set(), {"m1"})
        ben = next(p for p in a.team.players() if p["name"] == "Ben")
        self.assertEqual(ben["today"], 0)

    def test_console_guests_today_from_replays(self):
        entries = {
            "old.replay": {"status": "imported", "session": "s1", "started": self.midnight - 600,
                           "teammates": [{"name": "jFalcon", "opened": 30, "broken": 2}]},
            "today.replay": {"status": "confirmed", "session": "s2", "started": self.midnight + 600,
                             "teammates": [{"name": "jFalcon", "opened": 10, "broken": 1}]},
            "now.replay": {"status": "confirmed", "session": "s3",  # the game being played (no start time yet)
                           "teammates": [{"name": "jFalcon", "opened": 4, "broken": 0}]},
        }
        g = ct.guest_stats(entries, {"jfalcon": "jFalcon"}, "now.replay")["jfalcon"]
        self.assertEqual(g["today"], 15)
        self.assertEqual(g["total"], 47)
        self.assertEqual(g["game"], 4)

    def test_guests_today_travel_with_a_siblings_message(self):
        a, b = FakeApp("Ann"), FakeApp("Ben")
        b.guest_stats = lambda: {"jfalcon": {"name": "jFalcon", "game": 2, "total": 50, "platform": "PS5", "today": 8,
                                             "matches": 3}}
        a.team.update(b.team.message(), "net")
        guest = next(p for p in a.team.players() if p["name"].startswith("jFalcon"))
        self.assertEqual(guest["today"], 8)

    def test_overlay_squad_table_draws_today(self):
        import overlay_glass

        p = overlay_glass.GlassPainter(1.0)
        party = [{"name": "Ann", "game": 3, "total": 100, "today": 22, "online": True, "me": True},
                 {"name": "Ben", "game": 5, "total": 90, "today": None, "online": True, "me": False}]
        img = p.draw(mine=3, rows=[("TOTAL", 100)], party=party)
        self.assertEqual(img.size, (p.W, p.H + p.party_height(2)))


if __name__ == "__main__":
    unittest.main()
