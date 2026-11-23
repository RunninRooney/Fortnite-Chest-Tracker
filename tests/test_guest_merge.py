"""A console teammate (no tracker) listed on two trackers: their matches are combined, never doubled.

Run:  python -m unittest discover -s tests
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
_isolate.check(ct)

OLD = int(time.time()) - 5 * 86400


def guest(by_match, game=0, today=0):
    return {"name": "Brave-Boots", "platform": "PS5", "game": game, "today": today,
            "total": sum(c for c, _ in by_match.values()), "by_match": by_match}


class MergeTests(unittest.TestCase):
    def test_shared_matches_once_and_only_ones_added(self):
        mine = guest({"A": [30, OLD], "B": [20, OLD], "C": [10, OLD]})          # 60
        falcon = guest({"B": [25, OLD], "C": [10, OLD], "D": [40, OLD]})        # 75
        g = ct.merge_guest([(mine, True), (falcon, True)])
        self.assertEqual(g["total"], 30 + 25 + 10 + 40)  # B at the higher count, D added
        self.assertEqual(g["matches"], 4)
        self.assertNotIn("by_match", g)

    def test_older_tracker_without_per_match_never_lowers_it(self):
        mine = guest({"A": [30, OLD]})
        old = {"name": "Brave-Boots", "platform": "PS5", "game": 0, "today": 0, "total": 500}
        self.assertEqual(ct.merge_guest([(mine, True), (old, True)])["total"], 500)

    def test_today_and_this_game(self):
        now = int(time.time())
        mine = guest({"A": [30, OLD], "T1": [12, now - 600]}, game=12, today=12)
        falcon = guest({"T1": [15, now - 600], "T2": [9, now - 60]}, game=9, today=24)
        g = ct.merge_guest([(mine, True), (falcon, False)])
        self.assertEqual(g["today"], 15 + 9)
        self.assertEqual(g["game"], 12)  # only online trackers' current game
        self.assertTrue(g["online"])


class TeamTests(unittest.TestCase):
    def setUp(self):
        ct.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.json"
        ct.STATS_PATH = ct.CONFIG_PATH.parent / "stats.json"
        self.app = ct.App()
        self.app.cfg["console_teammates"] = ["Brave-Boots"]
        self.app.guest_stats = lambda: {"brave-boots": guest({"A": [30, OLD], "B": [20, OLD]})}

    def test_per_match_only_over_the_internet(self):
        self.assertIn("by_match", self.app.team.message(per_match=True)["guests"]["brave-boots"])
        lan = self.app.team.message()
        self.assertNotIn("by_match", lan["guests"]["brave-boots"])
        self.assertLess(len(json.dumps(lan)), 4096)  # LAN packets are read with a 4 KB buffer

    def test_brothers_list_combined_with_mine(self):
        now = time.time()
        bro = {"app": "chesttracker", "team": self.app.team.key, "id": "abcdef012345", "name": "Falcon", "game": 0,
               "total": 100, "ts": now, "online": True,
               "guests": {"brave-boots": guest({"B": [25, OLD], "D": [40, OLD]})}}
        self.app.team.update(bro, "net")
        self.assertEqual(self.app.team.guests()["brave-boots"]["total"], 30 + 25 + 40)
        # then a LAN message from him (no per-match list): his last list is kept
        lan = dict(bro, ts=now + 1, guests={"brave-boots": {k: v for k, v in bro["guests"]["brave-boots"].items()
                                                               if k != "by_match"}})
        self.app.team.update(lan, "lan")
        self.assertEqual(self.app.team.guests()["brave-boots"]["total"], 95)


if __name__ == "__main__":
    unittest.main()
