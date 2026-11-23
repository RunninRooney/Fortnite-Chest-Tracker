"""The mid-match replay check: reading the replay Fortnite is still writing, every ~20 s.

Run:  python -m unittest discover -s tests
The reader is faked here; reader_src's --live mode was checked on real replays cut off partway
(e.g. 7 minutes in: 19 opened + 7 broken, the same as the full read up to then).
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
from test_replays import Base, write_replay  # noqa: E402
_isolate.check(ct)


class PartialStatsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.s = ct.Stats(Path(tempfile.mkdtemp()) / "stats.json")
        self.s.new_game("t", replay="live.replay")

    def test_partial_adds_broken_and_keeps_the_game_open(self):
        for _ in range(5):
            self.s.add(1)
        self.assertEqual(self.s.confirm_partial("live.replay", 4, 3), (5, 8))  # 5 seen, +3 broken so far
        self.s.add(1)                                                            # a pop-up still counts here
        self.assertEqual((self.s.game, self.s.seen), (9, 6))
        self.assertIsNone(self.s.confirmed)

    def test_never_goes_down_and_final_read_wins(self):
        self.s.confirm_partial("live.replay", 10, 4)
        self.s.confirm_partial("live.replay", 9, 2)  # a read that stopped early on a half-written chunk
        self.assertEqual(self.s.game, 14)
        self.s.confirm("live.replay", 12, 5)
        self.assertEqual(self.s.game, 17)
        self.assertIsNone(self.s.confirm_partial("live.replay", 20, 9))  # after the real read: ignored

    def test_other_games_untouched_and_reset_on_new_game(self):
        self.assertIsNone(self.s.confirm_partial("other.replay", 3, 3))
        self.s.confirm_partial("live.replay", 2, 2)
        self.s.new_game("next", replay="b.replay", debounce=0)
        self.assertEqual((self.s.game, self.s.partial), (0, None))
        self.assertEqual(self.s.history[-1]["chests"], 4)


class LiveCheckTests(Base):
    def setUp(self):
        super().setUp()
        self.live_calls = []
        self.out = {"guid": "G", "complete": False, "recorder_found": True, "opened": 6, "broken": 2,
                    "recorder_name": "RunninRobin", "session_id": "S", "treasures": [],
                    "teammates": [{"name": "jFalcon", "platform": "PS5", "opened": 5, "broken": 1, "bot": False}]}

        def fake_reader(exe, path, timeout=600, with_map=False, live=False):
            self.live_calls.append((path.name, live))
            return self.out

        ct.run_reader = fake_reader
        self.app.match_in_progress = lambda strict=False: True
        self.app.cfg["fortnite_names"] = ["RunninRobin"]
        self.app.flash = lambda: None
        self.entries = []
        self.app.set_live_entry = lambda name, e: self.entries.append((name, e))
        write_replay(self.demos / "now.replay", True, time.time() - 300)
        self.app.stats.new_game("t", replay="now.replay")
        with open(self.demos / "now.replay", "ab") as f:
            f.write(b"x" * 10000)

    def test_reads_live_and_applies_partial(self):
        self.w.live_check(self.w._list())
        self.assertEqual(self.live_calls, [("now.replay", True)])
        self.assertEqual(self.app.stats.partial, {"opened": 6, "broken": 2})
        self.assertEqual(self.app.stats.game, 8)
        self.assertEqual(self.entries[0][0], "now.replay")
        self.w.live_check(self.w._list())  # under 20 s later: not read again
        self.assertEqual(len(self.live_calls), 1)

    def test_no_key_means_wait_for_the_end(self):
        self.out = {"live": True, "complete": False, "error": "no key yet", "encrypted": True, "key_length": 0}
        self.w.live_check(self.w._list())
        self.w._live["at"] = -1e9
        with open(self.demos / "now.replay", "ab") as f:
            f.write(b"x" * 10000)
        self.w.live_check(self.w._list())
        self.assertEqual(len(self.live_calls), 1)  # not tried again this match
        self.assertIsNone(self.app.stats.partial)

    def test_only_reads_when_the_file_grew(self):
        self.w.live_check(self.w._list())
        self.w._live["at"] = -1e9
        self.w.live_check(self.w._list())
        self.assertEqual(len(self.live_calls), 1)

    def test_off_outside_a_match_or_when_disabled(self):
        self.app.match_in_progress = lambda strict=False: False
        self.w.live_check(self.w._list())
        self.app.match_in_progress = lambda strict=False: True
        self.app.cfg["live_replay"] = False
        self.w.live_check(self.w._list())
        self.assertEqual(self.live_calls, [])

    def test_someone_else_playing_changes_nothing(self):
        self.out["recorder_name"] = "DontGetCaught"
        self.w.live_check(self.w._list())
        self.assertIsNone(self.app.stats.partial)


class LiveGuestTests(unittest.TestCase):
    def test_console_teammate_moves_mid_match_then_hands_over(self):
        import tempfile
        ct.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.json"
        ct.STATS_PATH = ct.CONFIG_PATH.parent / "stats.json"
        app = ct.App()
        app.cfg["console_teammates"] = ["jFalcon"]
        app.stats.new_game("t", replay="now.replay")
        entry = {"status": "confirmed", "session": "S", "teammates": [{"name": "jFalcon", "platform": "PS5",
                                                                       "opened": 7, "broken": 1}]}
        app.set_live_entry("now.replay", entry)
        self.assertEqual(app.guest_stats()["jfalcon"]["game"], 8)
        app.set_live_entry("now.replay", dict(entry, teammates=[dict(entry["teammates"][0], opened=12)]))
        self.assertEqual(app.guest_stats()["jfalcon"]["game"], 13)
        app.replays.db.files["now.replay"] = dict(entry, teammates=[dict(entry["teammates"][0], opened=15)])
        app.replays.db.version = getattr(app.replays.db, "version", 0) + 1
        self.assertEqual(app.guest_stats()["jfalcon"]["game"], 16)  # the real read takes over


if __name__ == "__main__":
    unittest.main()
