"""Broken chests a replay can't see: a teammate far from the recorder (Nov 20, 2026: across 30 matches my
replays saw 1,363 of Falcon's own 1,708 chests; opens come through the search flag from anywhere, broken
chests only from ~300 m of the recorder).

Run:  python -m unittest discover -s tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
import app_model  # noqa: E402
_isolate.check(ct)


def a_map():
    """The recorder stands at (0, 0) all match; Comet searches 3 times next to them and 7 times 1 km away."""
    route = [[t, 0, 0, 1000, 3] for t in range(0, 900, 10)]
    near = [{"t": 100 + i, "who": "Comet", "source": "search", "x": 2000 * i, "y": 0} for i in range(3)]
    far = [{"t": 300 + i, "who": "Comet", "source": "search", "x": 100_000, "y": 0} for i in range(7)]
    return {"players": [{"name": "Me", "recorder": True, "route": route},
                        {"name": "Comet", "recorder": False, "route": route}],
            "chests": near + far + [{"t": 50, "who": "Me", "source": "opener", "x": 0, "y": 0}]}


class FarTests(unittest.TestCase):
    def setUp(self):
        app_model.ESTIMATE_FAR_BROKEN = True  # (off in the app for now; the estimate itself is tested here)

    def tearDown(self):
        app_model.BREAK_RATE = 0.3
        app_model.ESTIMATE_FAR_BROKEN = False

    def test_estimate_is_off_in_the_app(self):
        app_model.ESTIMATE_FAR_BROKEN = False
        t = {"name": "Comet", "opened": 20, "broken": 1, "far": 0.7}
        self.assertEqual(app_model.teammate_chests(t), 21)
        entries = [{"status": "confirmed", "session": "A", "started": 1, "recorder": "Falcon", "teammates": [t]}]
        self.assertEqual(ct.build_credits(entries, {"falcon"}), {"comet": {"A": [20, 1, 1]}})

    def test_share_of_searches_far_from_the_recorder(self):
        self.assertEqual(ct.far_share(a_map(), "Comet"), 0.7)
        self.assertIsNone(ct.far_share(a_map(), "Nobody"))
        self.assertIsNone(ct.far_share({}, "Comet"))

    def test_far_away_teammate_gets_the_broken_chests_the_replay_missed(self):
        t = {"name": "Comet", "opened": 20, "broken": 1}
        self.assertEqual(app_model.teammate_chests(t), 21)  # no map data: as the replay saw it
        ct.mark_far(a_map(), [t])
        app_model.BREAK_RATE = 0.3
        self.assertEqual(app_model.teammate_chests(t), 21 + round(0.3 * 20 * 0.7))  # 25
        t["own"] = 30  # their own tracker's count always wins
        self.assertEqual(app_model.teammate_chests(t), 30)

    def test_break_rate_learned_from_teammates_own_counts(self):
        db = ct.ReplayDB(Path(tempfile.mkdtemp()) / "replays.json")
        db.files = {f"{i}.replay": {"status": "confirmed", "how": "recorder", "opened": 50, "broken": 15,
                                    "teammates": [{"name": "Falcon", "opened": 60, "broken": 2, "far": 0.8,
                                                   "own": 60 + 2 + 12}]} for i in range(6)}
        rate, src = db.break_rate()
        self.assertAlmostEqual(rate, 12 / 48)  # 12 missed of 60 * 0.8 far-away opens
        self.assertIn("own counts", src)
        for e in db.files.values():  # no teammate trackers: my own broken per opened chest
            del e["teammates"][0]["own"]
        self.assertEqual(db.break_rate(), (0.3, "my own broken chests"))

    def test_old_replays_get_far_from_their_saved_map(self):
        d = Path(tempfile.mkdtemp())
        (d / "maps").mkdir()
        (d / "maps" / "a.map.json").write_text(json.dumps({"map": a_map()}))
        db = ct.ReplayDB(d / "replays.json")
        db.files = {"a.replay": {"status": "confirmed", "teammates": [{"name": "Comet", "opened": 20, "broken": 0}]},
                    "b.replay": {"status": "confirmed", "teammates": [{"name": "X", "opened": 5, "broken": 0}]}}
        self.assertEqual(db.backfill_far(d / "maps"), 1)
        self.assertEqual(db.files["a.replay"]["teammates"][0]["far"], 0.7)
        self.assertNotIn("far", db.files["b.replay"]["teammates"][0])  # no map saved for it
        self.assertEqual(db.backfill_far(d / "maps"), 0)  # once

    def test_credits_carry_the_estimate(self):
        entries = [{"status": "confirmed", "session": "A", "started": 1, "recorder": "Falcon",
                    "teammates": [{"name": "jFalcon", "opened": 20, "broken": 1, "far": 0.5},
                                  {"name": "Near", "opened": 9, "broken": 3}]}]
        self.assertEqual(ct.build_credits(entries, {"falcon"}),
                         {"jfalcon": {"A": [20, 1, 1, 3]}, "near": {"A": [9, 3, 1]}})


if __name__ == "__main__":
    unittest.main()
