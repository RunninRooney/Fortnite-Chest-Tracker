"""Credits from a teammate's replays for players whose own PC has no replay of a match (console, or a
PC that doesn't save replays): they confirm the game counted live instead of adding it again.

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


class CreditTests(unittest.TestCase):
    def setUp(self):
        self.s = ct.Stats(Path(tempfile.mkdtemp()) / "stats.json")
        self.t0 = time.time() - 3 * 3600

    def live_game(self, started, seen):
        """A game this tracker counted from pop-ups only (Fortnite saved no replay on this PC)."""
        self.s.new_game("t", started=started, debounce=0)
        self.s.game_started = started
        for _ in range(seen):
            self.s.add(1)
        self.s.new_game("next", debounce=0)  # the match ended: it's in history now

    def test_credit_confirms_the_live_game(self):
        self.live_game(self.t0, 20)
        total = self.s.total
        # Robin's replay of that match saw me open 23 and break 6 (it started 40 s before my first chest)
        self.assertEqual(self.s.credit_game("M1", "Robin", self.t0 - 40, 23, 6), 9)  # 23 + 6 = 29, was 20
        self.assertEqual(self.s.total, total + 9)
        h = next(h for h in self.s.history if h.get("match") == "M1")
        self.assertEqual((h["chests"], h["seen"]), (29, 20))
        self.assertEqual(sum(1 for h in self.s.history if h.get("chests")), 1)  # still one game

    def test_live_count_higher_than_the_replay_is_kept(self):
        self.live_game(self.t0, 30)  # a teammate's replay only sees chests near the recorder
        self.assertEqual(self.s.credit_game("M1", "Robin", self.t0, 25, 2), 2)  # max(30, 25) + 2
        self.assertEqual(self.s.history[-1]["chests"] if self.s.history[-1].get("match") else
                         next(h for h in self.s.history if h.get("match") == "M1")["chests"], 32)

    def test_no_live_game_then_added(self):
        self.live_game(self.t0, 10)
        self.assertEqual(self.s.credit_game("M2", "Robin", self.t0 + 3600, 12, 3), 15)  # an hour later
        self.assertEqual(len([h for h in self.s.history if h.get("chests")]), 2)

    def test_new_numbers_for_a_credited_match(self):
        self.live_game(self.t0, 20)
        self.s.credit_game("M1", "Robin", self.t0, 23, 6)
        self.assertEqual(self.s.credit_game("M1", "Robin", self.t0, 25, 6), 2)  # Robin's reader improved
        self.assertIsNone(self.s.credit_game("M1", "Robin", self.t0, 25, 6))
        self.assertIsNone(self.s.credit_game("M1", "Falcon", self.t0, 40, 6))   # someone else: already counted

    def test_my_own_replay_takes_a_credited_match_over(self):
        self.assertEqual(self.s.credit_game("M3", "Robin", self.t0, 12, 3), 15)  # no live game: added
        total = self.s.total
        # later my tracker reads my own replay of that match: it replaces the credit, no second game
        self.s.import_game("mine.replay", self.t0, 900, 20, 5, session="M3")
        self.assertEqual(self.s.total, total + 10)
        games = [h for h in self.s.history if h.get("chests")]
        self.assertEqual(len(games), 1)
        self.assertEqual((games[0]["replay"], games[0]["chests"]), ("mine.replay", 25))
        self.assertIsNone(self.s.credit_game("M3", "Robin", self.t0, 30, 3))  # my replay wins from now on

    def test_credit_for_the_game_im_playing_waits_for_my_replay(self):
        """Nov 19, 2026 19:24: Falcon's credit for the match got in 6 s before my own replay of it was read,
        and the match counted twice (70 from my replay + 56 from his)."""
        self.s.new_game("replay started", replay="mine.replay", started=self.t0, debounce=0)
        for _ in range(40):
            self.s.add(1)
        total = self.s.total
        self.assertIsNone(self.s.credit_game("M4", "Falcon", self.t0 + 6, 54, 2))
        self.assertEqual(self.s.total, total)
        self.assertNotIn("M4", self.s.credited)  # (my replay's session id keeps it out after this)

    def test_credits_my_replays_counted_already_are_removed(self):
        self.s.history.append({"started": self.t0, "ended": self.t0 + 900, "chests": 70, "seen": 48,
                               "replay": "mine.replay", "confirmed": {"opened": 52, "broken": 15}})
        self.s.total = 70
        self.s.credit_game("M5", "Falcon", self.t0 + 6, 54, 2)   # (from before the fix)
        self.s.credit_game("M6", "Falcon", self.t0 + 7200, 12, 3)  # a console match: no replay of mine
        self.assertEqual(self.s.total, 70 + 56 + 15)
        self.assertEqual(self.s.drop_credits_for({"M5", "other"}), 56)
        self.assertEqual(self.s.total, 85)
        self.assertEqual([h.get("match") for h in self.s.history if h.get("credited_by")], ["M6"])
        self.assertEqual(self.s.drop_credits_for({"M5"}), 0)
        self.assertEqual(ct.Stats(self.s.path).total, 85)


class BuildCreditsTests(unittest.TestCase):
    def test_pc_teammates_and_other_recorders(self):
        entries = [
            {"status": "imported", "session": "A", "started": 1, "recorder": "RunninRobin",
             "raw": {"opened": 30, "broken": 5},
             "teammates": [{"name": "SupremeLlama15", "platform": "WIN", "opened": 20, "broken": 2},
                           {"name": "jFalcon", "platform": "PS5", "opened": 18, "broken": 1},
                           {"name": "Bot1", "platform": "", "opened": 9, "broken": 0, "bot": True}]},
            {"status": "imported", "session": "B", "started": 2, "recorder": "SupremeLlama15",
             "raw": {"opened": 14, "broken": 7},
             "teammates": [{"name": "RunninRobin", "platform": "WIN", "opened": 4, "broken": 1}]},
        ]
        c = ct.build_credits(entries, {"runninrobin"})
        self.assertEqual(c, {"supremellama15": {"A": [20, 2, 1], "B": [14, 7, 2]}, "jfalcon": {"A": [18, 1, 1]}})

    def test_old_trackers_only_get_console_credits(self):
        entries = [{"status": "imported", "session": "A", "started": 1, "recorder": "SupremeLlama15",
                    "raw": {"opened": 14, "broken": 7},
                    "teammates": [{"name": "jFalcon", "platform": "PS5", "opened": 18, "broken": 1},
                                  {"name": "Falcon", "platform": "WIN", "opened": 22, "broken": 0}]}]
        self.assertEqual(ct.build_credits(entries, {"runninrobin"}, console_only=True), {"jfalcon": {"A": [18, 1, 1]}})


if __name__ == "__main__":
    unittest.main()
