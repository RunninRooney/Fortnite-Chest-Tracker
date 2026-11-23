"""Where the camera is, from Fortnite's log: bus, skydiving, my character on the ground, dead, spectating.
The live map records my route only while tracking_ok().

Run:  python -m unittest discover -s tests
Lines copied from real FortniteGame.log files (Nov 19, 2026; server addresses replaced).
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

JOIN = ("[2026.11.19-16.58.21:715][402]LogLoad: LoadMap: 10.1.2.3:9003/Delilah/Maps/Delilah_Terrain?"
        "EncryptionToken=x:y?VerseURI=/Fortnite.com/GameFeatures/BRRoot")
SELF = "[2026.11.19-16.58.33:137][  1]LogFortViewTarget: [3129] trying to set view target from Pawn:RunninRobin to Pawn:RunninRobin"
TO_BUS = "[2026.11.19-16.59.19:918][  1]LogFortViewTarget: [3129] trying to set view target from Pawn:RunninRobin to RockstarEggnogAircraft_C_2147482644"
BUS_IN = "[2026.11.19-16.59.19:929][  1]LogFort: EnterAircraft: Athena_PlayerController_C_2147482645"
FALL = "[2026.11.19-16.59.42:994][  1]LogFort: Display: TSkydivingCVarSwitch: Adjusting s.AsyncLoadingPrecachePriority to 2."
BUS_OUT = "[2026.11.19-16.59.43:097][  1]LogFort: ExitAircraft: Athena_PlayerController_C_2147482645 (Pawn PlayerPawn_Athena_C_2147482619)"
LANDED = "[2026.11.19-16.59.59:792][  1]LogFort: Display: TSkydivingCVarSwitch: Adjusting s.AsyncLoadingPrecachePriority to 0."
DEAD = "[2026.11.19-07.12.44:288][183]LogSpectateAfterDeathViewTarget: [7a5c] [RunninRobin]  No teammates available to follow"
WATCH = ("[2026.11.19-07.12.48:671][314]LogFortViewTarget: [3129] trying to set view target from "
         "FortSpectatorThirdPersonPawn_2147482641 to Pawn:DontGetCaught")
WATCH2 = "[2026.11.19-07.12.48:675][314]LogFortViewTarget: [3129] trying to set view target from Pawn:DontGetCaught to Pawn:DontGetCaught"
WAIT = "[2026.11.19-07.12.48:675][314]LogSpectateAfterDeathViewTarget: [7a73] [RunninRobin]  Cannot follow, waiting on level streaming"
BACK = "[2026.11.19-07.15.00:000][  1]LogFortViewTarget: [3129] trying to set view target from Pawn:DontGetCaught to Pawn:RunninRobin"


class App:
    cfg = {"fortnite_names": ["RunninRobin"]}

    def on_match_start(self):
        pass

    def on_match_over(self, squad_out):
        pass


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.g = ct.GameLog(App(), path=Path(tempfile.mkdtemp()) / "FortniteGame.log")
        self.g.last_growth = time.monotonic()

    def views(self, *lines):
        out = []
        for ln in lines:
            self.g.feed(ln)
            out.append(self.g.view)
        return out

    def test_bus_then_skydive_then_landed(self):
        self.assertEqual(self.views(JOIN, SELF, TO_BUS, BUS_IN, FALL, BUS_OUT, SELF, LANDED),
                         ["", "", "", "bus", "skydive", "skydive", "skydive", "self"])
        self.assertTrue(self.g.tracking_ok())

    def test_not_on_the_spawn_island_bus_or_falling(self):
        for ln in (JOIN, SELF, BUS_IN, BUS_OUT):
            self.g.feed(ln)
            self.assertFalse(self.g.tracking_ok(), ln[30:60])

    def test_spectating_a_teammate_after_dying(self):
        self.views(JOIN, BUS_IN, BUS_OUT, LANDED)
        self.assertEqual(self.views(DEAD, WATCH, WAIT, WATCH2), ["dead", "spectating", "spectating", "spectating"])
        self.assertEqual(self.g.view_name, "DontGetCaught")
        self.assertFalse(self.g.tracking_ok())
        self.assertEqual(self.g.me_in_log, "RunninRobin")  # not the teammate being watched

    def test_back_on_my_character_after_a_reboot(self):
        self.views(JOIN, BUS_IN, BUS_OUT, LANDED, DEAD, WATCH)
        self.assertEqual(self.views(BACK, FALL, LANDED), ["self", "skydive", "self"])

    def test_without_names_set_it_learns_mine(self):
        self.g.app.cfg = {}
        self.views(JOIN, SELF, BUS_IN, BUS_OUT, LANDED, DEAD, WATCH)
        self.assertEqual((self.g.view, self.g.view_name), ("spectating", "DontGetCaught"))


if __name__ == "__main__":
    unittest.main()
