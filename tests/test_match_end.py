"""Match start / squad out / lobby from Fortnite's log, the in-match overlay lock, credit updates and
the overlay drawing.

Run:  python -m unittest discover -s tests
The log lines are copied from a real FortniteGame.log (Nov 15, 2026; long lines shortened, the
match server's address and tokens replaced).
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
_isolate.check(ct)

SPECTATE = ("[2026.11.16-03.45.33:668][689]LogLoad: LoadMap: 10.1.2.3:9013/Delilah/Maps/Delilah_Terrain?"
            "EncryptionToken=x:y?SpectatorOnly=1?PartyMemberSpectator?FollowPlayer=MCP:z")
JOIN = ("[2026.11.16-05.09.21:158][402]LogLoad: LoadMap: 10.1.2.3:9003/Delilah/Maps/Delilah_Terrain?"
        "EncryptionToken=x:y?VerseURI=/Fortnite.com/GameFeatures/BRRoot")
WARMUP = ("[2026.11.16-05.09.35:583][969]LogBattleRoyaleGamePhaseLogic: UpdateGamePhaseStep. "
          "Phase = EAthenaGamePhase::Warmup. PhaseStep = EAthenaGamePhaseStep::Warmup")
BUS = ("[2026.11.16-05.09.53:486][465]LogBattleRoyaleGamePhaseLogic: UpdateGamePhaseStep. "
       "Phase = EAthenaGamePhase::Aircraft. PhaseStep = EAthenaGamePhaseStep::BusLocked")
DIED = "[2026.11.16-05.27.01:832][  1]LogSpectateAfterDeathViewTarget: [7a5c] [RunninRobin]  No teammates available to follow"
PLACEMENT = ("[2026.11.16-05.27.34:198][613]LogWidgetSlotting: [WidgetSlottingLeak] Registry AddWidgetHost: "
             "host=PlacementOverlay tag=DynamicUI.HUD.Fort.Athena.PostMatch.BelowVictoryPlacement totalEntries=36")
PLACEMENT2 = ("[2026.11.16-05.27.34:267][613]LogWidgetSlotting: [WidgetSlottingLeak] Registry AddWidgetHost: "
              "host=PlacementOverlay_C_2147482641 tag=DynamicUI.HUD.Fort.Athena.PostMatch.LosingPlacementOverride")
ENDGAME = "[2026.11.16-04.44.51:738][825]LogSpectateAfterDeathViewTarget: [7a64] [RunninRobin]  Cannot follow, GameState in EndGame phase"
LOBBY = "[2026.11.16-05.27.54:561][392]LogLoad: LoadMap: /Game/Maps/Frontend?closed"
CLOSED = "[2026.11.16-05.28.23:282][649]Log file closed, 11/15/26 22:28:23"
NOISE = "[2026.11.16-05.20.00:000][100]LogFortCosmetics: Pawn PlayerPawn_Athena_C_2147482645 is applying variants"


class FakeApp:
    def __init__(self):
        self.events = []

    def on_match_start(self):
        self.events.append("start")

    def on_match_over(self, squad_out):
        self.events.append("squad out" if squad_out else "left")


class GameLogTests(unittest.TestCase):
    def setUp(self):
        self.g = ct.GameLog(FakeApp(), path=Path(tempfile.mkdtemp()) / "FortniteGame.log")

    def feed(self, *lines):
        return [self.g.feed(ln) for ln in lines]

    def test_a_whole_match(self):
        self.assertIsNone(self.g.match_state())  # nothing read yet: can't tell
        self.assertEqual(self.feed(LOBBY, SPECTATE), ["lobby", "spectate"])
        self.assertEqual(self.g.state, "spectating")  # watching a party member: not my match
        self.assertEqual(self.feed(LOBBY, JOIN, WARMUP, NOISE, BUS), ["lobby", "join", None, None, None])
        self.assertEqual((self.g.state, self.g.phase), ("match", "Aircraft"))
        self.assertEqual(self.feed(DIED), [None])  # I'm dead, but a teammate could still bring me back
        self.assertEqual(self.feed(PLACEMENT, PLACEMENT2, ENDGAME), ["results", None, None])
        self.assertEqual(self.g.state, "results")
        self.assertEqual(self.feed(LOBBY, CLOSED), ["lobby", "closed"])

    def test_leaving_before_the_placement_screen_ends_my_game(self):
        self.assertEqual(self.feed(JOIN, BUS, LOBBY), ["join", None, "left"])
        self.assertEqual(self.feed(JOIN, CLOSED), ["join", "left"])  # Fortnite quit mid-match

    def test_endgame_line_alone_counts_as_results(self):
        self.assertEqual(self.feed(JOIN, ENDGAME), ["join", "results"])

    def test_placement_lines_outside_a_match_ignored(self):
        self.assertEqual(self.feed(LOBBY, PLACEMENT), ["lobby", None])

    def test_quiet_log_mid_match_proves_nothing(self):
        self.feed(JOIN)
        self.g.last_growth = time.monotonic()
        self.assertEqual(self.g.match_state(), "match")
        self.g.last_growth = time.monotonic() - self.g.QUIET - 1  # Fortnite crashed mid-match
        self.assertIsNone(self.g.match_state())

    def test_reading_the_file(self):
        p = self.g.path
        p.write_text("\n".join([LOBBY, JOIN, BUS, NOISE]) + "\n", encoding="utf-8")
        with open(p, "rb") as f:
            pos = self.g._read(f, 0, live=False)  # catching up on start: no events fired
        self.assertEqual((self.g.state, self.g.app.events), ("match", []))
        with open(p, "a", encoding="utf-8") as f:
            f.write(PLACEMENT + "\n" + LOBBY[:30])  # the last line isn't finished yet
        with open(p, "rb") as f:
            pos = self.g._read(f, pos, live=True)
        self.assertEqual(self.g.app.events, ["squad out"])
        with open(p, "a", encoding="utf-8") as f:
            f.write(LOBBY[30:] + "\n" + JOIN + "\n")
        with open(p, "rb") as f:
            self.g._read(f, pos, live=True)
        self.assertEqual(self.g.app.events, ["squad out", "start"])


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.s = ct.Stats(Path(tempfile.mkdtemp()) / "stats.json")

    def test_finish_then_next_chest_starts_a_new_game(self):
        self.s.add(1); self.s.add(1)
        self.assertTrue(self.s.finish())
        self.assertFalse(self.s.finish())  # already over
        self.s.add(1)  # a chest after the placement screen: a new match began unnoticed
        self.assertEqual((self.s.game, self.s.total, self.s.finished), (1, 3, None))

    def test_log_start_overrides_long_debounce(self):
        self.assertTrue(self.s.new_game("a"))
        self.assertFalse(self.s.new_game("b"))                # same match (default 90 s)
        time.sleep(0.05)
        self.assertTrue(self.s.new_game("log", debounce=0.01))  # Fortnite's log says it's a new one

    def test_credit_updates_when_the_reader_improves(self):
        self.assertEqual(self.s.credit_game("M1", "Robin", 0, 3, 1), 4)
        self.assertIsNone(self.s.credit_game("M1", "Robin", 0, 3, 1))     # same numbers again
        self.assertEqual(self.s.credit_game("M1", "Robin", 0, 20, 1), 17)  # better count for that match
        self.assertIsNone(self.s.credit_game("M1", "Other", 0, 30, 1))     # someone else: already counted
        self.assertEqual(self.s.total, 21)
        again = ct.Stats(self.s.path)
        self.assertEqual(again.total, 21)


class LockTests(unittest.TestCase):
    """The overlay takes no clicks during a match, unless unlocked from the taskbar."""

    def setUp(self):
        ct.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.json"
        ct.STATS_PATH = ct.CONFIG_PATH.parent / "stats.json"
        self.app = ct.App()
        self.app.gamelog.state, self.app.gamelog.last_growth = "match", time.monotonic()
        self.front = True
        self._orig = ct.fortnite_in_front
        ct.fortnite_in_front = lambda: self.front
        self.app._apply_click_through = lambda: None  # (no window in tests)

    def tearDown(self):
        ct.fortnite_in_front = self._orig

    def test_locked_in_match_only(self):
        self.assertTrue(self.app.locked())
        self.app.gamelog.state = "results"
        self.assertFalse(self.app.locked())  # placement screen: free to move again
        self.app.gamelog.state = "lobby"
        self.assertFalse(self.app.locked())
        self.app.gamelog.state = "match"
        self.app.cfg["lock_in_match"] = False
        self.assertFalse(self.app.locked())

    def test_not_locked_when_nothing_says_a_match_is_on(self):
        self.app.gamelog.state = "unknown"  # log not read yet, and no replays recorded on this PC
        self.app.replays.demos = Path(tempfile.mkdtemp())
        self.app.replays._mip_checked = -1e9
        self.assertTrue(self.app.match_in_progress())  # counting isn't blocked when it can't tell...
        self.assertFalse(self.app.locked())             # ...but the overlay isn't locked either

    def test_taskbar_unlocks_until_back_in_fortnite(self):
        self.app._unlocked, self.app._unlock_away = True, False  # (what open_settings does)
        self.front = False  # the taskbar / settings window is in front now
        self.app._update_lock()
        self.assertFalse(self.app.locked())
        self.app._update_lock()
        self.assertFalse(self.app.locked())
        self.front = True  # clicked back into the game
        self.app._update_lock()
        self.assertTrue(self.app.locked())

    def test_new_match_locks_again(self):
        self.app._unlocked = True
        self.app.on_match_start()
        self.assertTrue(self.app.locked())

    def test_squad_out_shows_final_once(self):
        self.app.stats.add(1); self.app.stats.add(1)
        self.app.on_match_over(squad_out=True)
        self.assertTrue(self.app.stats.finished)
        self.assertIn("Squad out! You: 2 chests", self.app._toast[0])
        first = self.app._toast
        self.app.on_match_over(squad_out=False)  # then the lobby: no second message
        self.assertEqual(self.app._toast, first)


class DrawTests(unittest.TestCase):
    def test_draws_with_shadow_and_final(self):
        p = ct.Painter(1.0)
        rows = [("TEAM GAME", 96), ("MY TOTAL", 2913), ("TEAM TOTAL", 4777)]
        party = [{"name": "Robin", "game": 52, "total": 2913, "online": True, "me": True}]
        for opacity in (0.0, 0.25, 0.85):
            img = p.draw(52, rows, opacity=opacity, final=True, confirmed=True, party=party)
            self.assertEqual(img.size, (p.W, p.H + p.party_height(1)))
            # the shadow makes dark, partly see-through pixels next to the text even with no background
            px = [img.getpixel((x, y)) for x in range(90, 200) for y in range(10, 90)]
            self.assertTrue(any(a > 40 and r < 60 for r, g, b, a in px))

    def test_custom_font_folder(self):
        orig = ct.APP_DIR
        try:
            ct.APP_DIR = Path(tempfile.mkdtemp())
            self.assertIsNone(ct.custom_font())
            (ct.APP_DIR / "fonts").mkdir()
            (ct.APP_DIR / "fonts" / "readme.txt").write_text("x")
            self.assertIsNone(ct.custom_font())
            windir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "impact.ttf"
            if windir.exists():
                (ct.APP_DIR / "fonts" / "Mine.ttf").write_bytes(windir.read_bytes())
                self.assertEqual(ct.custom_font().name, "Mine.ttf")
                ct.Painter(1.0).draw(1, [("TOTAL", 1)])
        finally:
            ct.APP_DIR = orig


if __name__ == "__main__":
    unittest.main()
