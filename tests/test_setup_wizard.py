"""First-time setup: the Fortnite settings checks and what the answers save."""
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import setup_wizard as sw  # noqa: E402
import teamcrypt  # noqa: E402


class Checks(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.demos = self.d / "Demos"
        self.ini = self.d / "GameUserSettings.ini"
        self.reader = self.d / "ChestReplayReader.exe"

    def states(self):
        return [s for s, _, _ in sw.fortnite_checks(self.demos, self.ini, self.reader)]

    def test_nothing_there(self):
        self.assertEqual(self.states(), ["info", "info", "info"])

    def test_all_good(self):
        self.demos.mkdir()
        (self.demos / "UnsavedReplay-1.replay").write_bytes(b"x")
        self.ini.write_text("[/Script/FortniteGame.FortGameUserSettings]\nLastConfirmedFullscreenMode=1\n")
        self.reader.write_bytes(b"x")
        self.assertEqual(self.states(), ["ok", "ok", "ok"])

    def test_old_replays_and_fullscreen_warn(self):
        self.demos.mkdir()
        f = self.demos / "UnsavedReplay-1.replay"
        f.write_bytes(b"x")
        old = time.time() - 60 * 86400
        os.utime(f, (old, old))
        self.ini.write_text("[/Script/FortniteGame.FortGameUserSettings]\nLastConfirmedFullscreenMode=0\n")
        self.assertEqual(self.states()[:2], ["warn", "warn"])


class Answers(unittest.TestCase):
    def app(self, **cfg):
        a = types.SimpleNamespace(cfg=dict(cfg))
        a.team = types.SimpleNamespace(set_code=lambda c: a.cfg.__setitem__("team_code", c))
        return a

    def test_squad_saves_code_and_names(self):
        a, saved = self.app(), []
        sw.apply(a, " Robin ", "RunninFake, PSFake", "squad", teamcrypt.make_code(), saved.append)
        self.assertEqual(a.cfg["name"], "Robin")
        self.assertEqual(a.cfg["fortnite_names"], ["RunninFake", "PSFake"])
        self.assertFalse(a.cfg["offline"])
        self.assertTrue(a.cfg["team_code"])
        self.assertTrue(saved)

    def test_offline_clears_an_old_code(self):
        a = self.app(team_code="old")
        sw.apply(a, "Ann", "", "offline", "ignored", lambda c: None)
        self.assertTrue(a.cfg["offline"])
        self.assertEqual(a.cfg["team_code"], "")
        self.assertNotIn("fortnite_names", a.cfg)

    def test_made_codes_are_strong_and_unique(self):
        codes = {teamcrypt.make_code() for _ in range(50)}
        self.assertEqual(len(codes), 50)
        self.assertTrue(all(teamcrypt.code_strength(c) == "strong" for c in codes))
        self.assertEqual(teamcrypt.code_strength("sib42"), "weak")


if __name__ == "__main__":
    unittest.main()
