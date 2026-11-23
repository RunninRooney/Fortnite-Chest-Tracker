"""The expanded overlay lists only the squad I'm playing with: teammates online now, in my last game (by
their Fortnite names or the match), and console players in this or the last game. Everyone else is only
in TEAM TOTAL."""
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
_isolate.check(ct)


class FakeApp:
    def __init__(self, name, code="squad", entries=None, guests=None):
        d = Path(tempfile.mkdtemp())
        self.cfg = dict(ct.DEFAULT_CONFIG, name=name, team_code=code)
        self.stats = ct.Stats(d / "stats.json")
        self.replays = types.SimpleNamespace(db=types.SimpleNamespace(files=entries or {}))
        self._guests = guests or {}
        self.team = ct.Team(self, d / "team.json")

    def guest_stats(self):
        return self._guests

    def toast(self, *a, **k):
        pass


def peer_msg(name, fortnite, games=None):
    other = FakeApp(name)
    m = other.team.message()
    m["fortnite_names"] = [fortnite]
    m["games"] = games or []
    return m


class OverlaySquadTests(unittest.TestCase):
    def setUp(self):
        ct.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.json"

    def names(self, app):
        return [p["name"] for p in app.team.players()]

    def add_offline(self, app, m):
        app.team.update(m, "net")
        with app.team.lock:
            for p in app.team.peers.values():
                if p["name"] == m["name"]:
                    p["online"] = False

    def test_just_me_hides_everyone_offline(self):
        a = FakeApp("Robin")
        self.add_offline(a, peer_msg("Llama", "llama1"))
        self.assertEqual(self.names(a), ["Robin"])
        self.assertGreater(a.team.team_total(), -1)  # (their total still counts in TEAM TOTAL)

    def test_online_teammate_shows(self):
        a = FakeApp("Robin")
        a.team.update(peer_msg("Falcon", "falcon1"), "net")
        self.assertEqual(self.names(a), ["Robin", "Falcon"])

    def test_last_game_squadmate_shows_even_offline(self):
        now = time.time()
        a = FakeApp("Robin", entries={"r1": {"status": "confirmed", "how": "recorder", "session": "S1",
                                              "started": now - 1200, "teammates": [{"name": "llama1"}]}})
        self.add_offline(a, peer_msg("Llama", "llama1"))
        self.add_offline(a, peer_msg("Boots", "boots1"))
        self.add_offline(a, peer_msg("Moe", "moe1", games=[["S1", 40]]))  # in that match by the match id
        self.assertEqual(sorted(self.names(a)[1:]), ["Llama", "Moe"])

    def test_old_last_game_doesnt_count(self):
        now = time.time()
        a = FakeApp("Robin", entries={"r1": {"status": "confirmed", "how": "recorder", "session": "S1",
                                              "started": now - 5 * 3600, "teammates": [{"name": "llama1"}]}})
        self.add_offline(a, peer_msg("Llama", "llama1"))
        self.assertEqual(self.names(a), ["Robin"])

    def test_console_teammate_only_when_playing(self):
        now = time.time()
        guests = {"jfalcon": {"name": "jFalcon", "platform": "PSN", "game": 0, "total": 300, "online": True},
                  "pspal": {"name": "PSPal", "platform": "PSN", "game": 12, "total": 50, "online": True},
                  "oldpal": {"name": "OldPal", "platform": "XBL", "game": 0, "total": 90, "online": True}}
        a = FakeApp("Robin", guests=guests,
                    entries={"r1": {"status": "imported", "how": "recorder", "session": "S1", "started": now - 600,
                                    "teammates": [{"name": "jFalcon"}]}})
        shown = self.names(a)
        self.assertIn("jFalcon (PS)", shown)    # in my last game
        self.assertIn("PSPal (PS)", shown)      # has chests in this game
        self.assertNotIn("OldPal (Xbox)", shown)


if __name__ == "__main__":
    unittest.main()
