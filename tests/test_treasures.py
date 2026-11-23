"""Treasure hunt prizes: names, whose they are, saving, sharing with the team, and the announcements.

Run:  python -m unittest discover -s tests
The treasure entries are shaped like the replay reader's output for real replays (Nov 15-17, 2026).
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
import treasures as T  # noqa: E402
_isolate.check(ct)

PPPP = {"t": 240.4, "prize": "DelilahPrize:dpid_item21", "won": True, "name": "RunninRobin", "platform": "WIN", "who": "me"}
BACKBLING = {"t": 524.0, "prize": "DelilahPrize:dpid_item20", "won": True, "name": "RunninRobin", "platform": "WIN", "who": "me"}
FALCON = {"t": 843.0, "prize": "DelilahPrize:dpid_item22", "won": True, "name": "Falcon", "platform": "WIN", "who": "teammate"}


class DescribeTests(unittest.TestCase):
    def test_known_prizes(self):
        self.assertEqual(T.describe("DelilahPrize:dpid_item21")["name"], "PPPP")
        self.assertEqual(T.describe("DelilahPrize:dpid_item20")["kind"], "back bling")
        self.assertEqual(T.describe("DelilahPrize:dpid_item23")["name"], "Gold Chevrolet Corvette ZR1 Decal")
        self.assertEqual(T.describe("DelilahPrize:dpid_item26")["name"], "Week 4 Jam Track")

    def test_pirate_shop_as_text_or_bytes(self):
        self.assertTrue(T.describe(T.PIRATE_SHOP)["pirate"])
        self.assertTrue(T.describe(T.PIRATE_SHOP.upper())["pirate"])
        raw = "hex:0000" + T.PIRATE_SHOP.replace("-", "").upper() + "00"
        self.assertTrue(T.describe(raw)["pirate"])
        self.assertIn("PIRATE SHOP ACCESS", T.found_message("You", {"prize": T.PIRATE_SHOP}))

    def test_sprite_gizmo(self):
        d = T.describe("gizmo")
        self.assertEqual((d["name"], d["kind"], d["pirate"]), ("Sprite gizmo", "sprite gizmo", False))
        self.assertEqual(T.found_message("You", {"prize": "gizmo"}), "TREASURE! You got a sprite gizmo")
        self.assertEqual(T.found_message("jamie", {"prize": "gizmo"}), "TREASURE! jamie got a sprite gizmo")

    def test_unknown_prizes_still_readable(self):
        self.assertEqual(T.describe("DelilahPrize:dpid_item77")["name"], "Treasure prize #77")
        self.assertEqual(T.describe("12345678-1234-1234-1234-123456789abc")["kind"], "special")
        self.assertEqual(T.describe(None)["name"], "Treasure prize")


class MineTests(unittest.TestCase):
    def test_by_name_or_recorder(self):
        both = [PPPP, FALCON]
        self.assertEqual(T.mine(both, {"runninrobin"}, True), [PPPP])
        self.assertEqual(T.mine(both, {"falcon"}, False), [FALCON])     # a replay where I was the teammate
        self.assertEqual(T.mine(both, set(), True), [PPPP])              # no name set: the recorder's
        self.assertEqual(T.mine(both, set(), False), [])
        self.assertEqual(T.mine([dict(PPPP, won=False)], {"runninrobin"}, True), [])


class LogTests(unittest.TestCase):
    def test_each_find_saved_once(self):
        path = Path(tempfile.mkdtemp()) / "treasures.json"
        log = T.TreasureLog(path)
        self.assertEqual(len(log.add("S1", [PPPP], 1_000_000)), 1)
        self.assertEqual(log.add("S1", [PPPP], 1_000_000), [])           # the same replay read again
        self.assertEqual(len(log.add("S2", [PPPP, BACKBLING], 2_000_000)), 2)
        again = T.TreasureLog(path)
        self.assertEqual([x["name"] for x in again.items], ["PPPP", "PPPP", "Miku's Takarabako"])
        self.assertEqual(again.share()["count"], 3)

    def test_reread_replaces_wrong_gizmos(self):
        log = T.TreasureLog(Path(tempfile.mkdtemp()) / "treasures.json")
        g1 = {"t": 215.2, "prize": "gizmo", "name": "RunninRobin", "who": "me"}
        g2 = {"t": 400.0, "prize": "gizmo", "name": "RunninRobin", "who": "me"}  # really a teammate's (1.18)
        log.add("S1", [PPPP, g1, g2], 1_000_000)
        self.assertEqual(len(log.items), 3)
        log.add("S1", [PPPP, g1], 1_000_000)  # read again with the fixed reader
        self.assertEqual(sorted(x["prize"] for x in log.items), ["DelilahPrize:dpid_item21", "gizmo"])
        log.add("S2", [], 2_000_000)  # another match doesn't touch S1
        self.assertEqual(len(log.items), 2)

    def test_shared_data_checked(self):
        self.assertIsNone(T.parse_shared("junk"))
        got = T.parse_shared({"count": "2", "recent": [{"prize": "DelilahPrize:dpid_item23", "when": 5}, {"x": 1}, 7]})
        self.assertEqual(got, {"count": 2, "recent": [{"prize": "DelilahPrize:dpid_item23", "when": 5}]})


class AppTests(unittest.TestCase):
    def setUp(self):
        ct.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.json"
        ct.STATS_PATH = ct.CONFIG_PATH.parent / "stats.json"
        self.app = ct.App()
        self.app.treasures = T.TreasureLog(ct.CONFIG_PATH.parent / "treasures.json")
        self.app.cfg["fortnite_names"] = ["RunninRobin"]
        self.toasts = []
        self.app.toast = lambda text, s=4.0: self.toasts.append(text)
        self.app.flash = lambda: None

    def test_new_find_saved_and_announced(self):
        r = {"session_id": "S1", "treasures": [PPPP, FALCON]}
        self.app.on_replay_treasures("a.replay", r, time.time() - 900, "recorder")
        self.assertEqual([x["name"] for x in self.app.treasures.items], ["PPPP"])
        self.assertEqual(self.toasts, ["TREASURE! You found PPPP (jam track)",
                                       "TREASURE! Falcon found May Fortune Find You (loading screen)"])

    def test_old_replays_saved_quietly(self):
        r = {"session_id": "S0", "treasures": [BACKBLING]}
        self.app.on_replay_treasures("old.replay", r, time.time() - 5 * 86400, "recorder")
        self.assertEqual(len(self.app.treasures.items), 1)
        self.assertEqual(self.toasts, [])

    def test_teammate_share_announced_once(self):
        now = time.time()
        m = {"app": "chesttracker", "team": self.app.team.key, "id": "abcdef012345", "name": "jamie", "game": 1,
             "total": 9, "ts": now, "online": True, "treasures": {"count": 0, "recent": []}}
        self.app.team.update(m, "net")
        corvette = {"prize": "DelilahPrize:dpid_item23", "when": int(now - 60)}
        m2 = dict(m, ts=now + 1, treasures={"count": 1, "recent": [corvette]})
        self.app.team.update(m2, "net")
        self.app.team.update(dict(m2, ts=now + 2), "net")  # the same news again
        self.assertEqual(self.toasts, ["TREASURE! jamie found Gold Chevrolet Corvette ZR1 Decal (car)"])
        self.assertIn("treasures", self.app.team.message())

    def test_teammate_share_keeps_my_match_video(self):
        got = []

        class Rec:
            def shared_find(self, when, prize, who, describe):
                got.append((when, prize, who))

        self.app.recorder = Rec()
        now = time.time()
        m = {"app": "chesttracker", "team": self.app.team.key, "id": "abcdef012345", "name": "llama", "game": 1,
             "total": 9, "ts": now, "online": True, "treasures": {"count": 0, "recent": []}}
        self.app.team.update(m, "net")
        jam = {"prize": "DelilahPrize:dpid_item26", "when": int(now - 60)}
        self.app.team.update(dict(m, ts=now + 1, treasures={"count": 1, "recent": [jam]}), "net")
        self.assertEqual(got, [(jam["when"], jam["prize"], "llama")])

    def test_pirate_shop_announced_big(self):
        r = {"session_id": "S9", "treasures": [dict(PPPP, prize=T.PIRATE_SHOP)]}
        self.app.on_replay_treasures("p.replay", r, time.time() - 600, "recorder")
        self.assertEqual(self.toasts, ["PIRATE SHOP ACCESS! You won it!"])


if __name__ == "__main__":
    unittest.main()
