"""Tests for team codes and live sharing.

Run:  python -m unittest discover -s tests
Set CHEST_NET_TEST=1 to also test two players talking through the real internet relay
(uses a random throwaway team code and made-up names).
"""
import json
import os
import secrets
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
    def __init__(self, name, code=""):
        d = Path(tempfile.mkdtemp())
        self.cfg = dict(ct.DEFAULT_CONFIG, name=name, team_code=code)
        self.stats = ct.Stats(d / "stats.json")
        self.team = ct.Team(self, d / "team.json")
        self.toasts = []

    def toast(self, text, seconds=4.0):
        self.toasts.append(text)


def msg(app):
    return app.team.message()


class TeamTests(unittest.TestCase):
    def setUp(self):
        ct.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.json"
        self.a, self.b, self.c = FakeApp("Ann", "Siblings 42"), FakeApp("Ben", "siblings42"), FakeApp("Cal", "other")

    def test_code_is_case_and_space_insensitive(self):
        self.assertEqual(self.a.team.key, self.b.team.key)
        self.assertNotEqual(self.a.team.key, self.c.team.key)
        self.assertEqual(ct.team_key(""), "")

    def test_messages_are_locked_with_the_code(self):
        blob = self.b.team.seal(msg(self.b))
        self.assertNotIn(b"Ben", blob)
        self.assertIsNone(self.c.team.unseal(blob))                       # another team can't read it
        self.assertEqual(self.a.team.update(self.a.team.unseal(blob), "net"), "Ben")
        self.assertNotIn(ct.old_team_key("siblings42"), self.a.team.key)

    def test_teammates_remembered_before_the_lock_are_kept(self):
        d = Path(tempfile.mkdtemp())
        (d / "team.json").write_text(json.dumps({ct.old_team_key("Siblings 42"): {"ab12": {"name": "Ben", "total": 77}}}))
        app = FakeApp("Ann", "siblings42")
        app.team = ct.Team(app, d / "team.json")
        self.assertEqual(app.team.team_total(), 77)

    def test_counts_add_up_and_other_teams_ignored(self):
        self.a.stats.add(1); self.a.stats.add(1)
        self.b.stats.add(1)
        self.assertEqual(self.a.team.update(msg(self.b), "net"), "Ben")  # just came online
        self.assertIsNone(self.a.team.update(msg(self.b), "net"))       # already online
        self.assertIsNone(self.a.team.update(msg(self.c), "net"))       # different code
        self.assertIsNone(self.a.team.update(msg(self.a), "lan"))       # my own echo
        self.assertEqual(self.a.team.team_game(), 3)
        self.assertEqual(self.a.team.team_total(), 3)
        names = [p["name"] for p in self.a.team.players()]
        self.assertEqual(names, ["Ann", "Ben"])

    def test_same_sibling_over_lan_and_internet_counts_once(self):
        self.b.stats.add(1)
        self.a.team.update(msg(self.b), "lan")
        self.a.team.update(msg(self.b), "net")
        self.assertEqual(self.a.team.team_game(), 1)

    def test_offline_sibling_keeps_total_but_not_game(self):
        self.b.stats.add(1)
        self.a.team.update(msg(self.b), "net")
        self.a.team.update(self.b.team.status_message(False), "net")
        self.assertEqual(self.a.team.team_game(), 0)
        self.assertEqual(self.a.team.team_total(), 1)
        # remembered after a restart
        again = ct.Team(self.a, self.a.team.path)
        self.assertEqual(again.team_total(), 1)

    def test_stale_retained_message_is_offline(self):
        self.b.stats.add(1)
        old = msg(self.b)
        old["ts"] = time.time() - 3600  # left over on the relay from an hour ago
        self.assertIsNone(self.a.team.update(old, "net"))
        self.assertEqual(self.a.team.team_game(), 0)
        self.assertEqual(self.a.team.team_total(), 1)

    def test_junk_ignored(self):
        for junk in [None, [], {"app": "x"}, {"app": "chesttracker", "id": "../evil"},
                     dict(msg(self.b), total="lots", game=-5)]:
            self.a.team.update(junk, "net")
        self.assertEqual(self.a.team.team_total(), 0)

    def test_credits_only_for_names_a_teammate_claimed(self):
        db = type("DB", (), {})()
        db.files = {"a.replay": {"status": "imported", "session": "S1", "started": 0, "teammates": [
            {"name": "jFalcon", "platform": "PS5", "opened": 5, "broken": 1, "bot": False},
            {"name": "RandomFill", "platform": "PS5", "opened": 9, "broken": 0, "bot": False}]}}
        self.a.replays = type("W", (), {"db": db, "my_names": lambda s: {"runninrobin"}})()
        link = ct.NetLink(self.a)
        self.assertIsNone(link.credits_message())  # nobody claimed anything yet
        self.b.cfg["fortnite_names"] = ["Falcon", "jFalcon"]
        self.a.team.update(msg(self.b), "net")
        self.assertEqual(self.a.team.claimed_names(), {"falcon", "jfalcon"})
        m = json.loads(link.credits_message())
        self.assertEqual(set(m["credits"]), {"jfalcon"})  # the random fill's name isn't shared
        self.assertEqual(m["credits"]["jfalcon"]["S1"][:2], [5, 1])

    def test_teammates_own_count_replaces_what_my_replay_saw(self):
        """Nov 19, 2026: my replay saw Falcon get 53 (he was far away a lot); his own tracker counted 76."""
        import app_model

        d = Path(tempfile.mkdtemp())
        # Ben's side: two read games and the current one, each with its replay's match session id
        bdb = ct.ReplayDB(d / "b_replays.json")
        bdb.files = {"b1.replay": {"session": "S1"}, "b2.replay": {"session": "S2"}, "b3.replay": {"session": "S3"}}
        self.b.replays = type("W", (), {"db": bdb})()
        self.b.stats.history = [{"replay": "b1.replay", "chests": 76}, {"replay": None, "chests": 5},
                                {"replay": "b2.replay", "chests": 30}]
        self.b.stats.replay, self.b.stats.confirmed, self.b.stats.game = "b3.replay", {"opened": 9}, 12
        self.b.cfg["fortnite_names"] = ["Falcon"]
        m = self.b.team.message(per_match=True)
        self.assertEqual(m["games"], [["S1", 76], ["S2", 30], ["S3", 12]])
        self.assertNotIn("games", self.b.team.message())  # not in the small LAN packet

        # Ann's side: her replay of S1 saw Falcon (53) and a random fill
        adb = ct.ReplayDB(d / "a_replays.json")
        adb.files = {"a.replay": {"status": "confirmed", "session": "S1", "teammates": [
            {"name": "Falcon", "opened": 46, "broken": 7}, {"name": "TayGETA2409", "opened": 16, "broken": 0}]}}
        self.a.replays = type("W", (), {"db": adb})()
        self.a.team.update(m, "net")
        ct.App.on_teammate_counts(self.a, m)
        mates = {q["name"]: q["chests"] for q in app_model._squad(adb.files["a.replay"], {"runninrobin"})}
        self.assertEqual(mates, {"Falcon": 76, "TayGETA2409": 16})  # the random fill: as the replay saw them
        self.assertEqual(json.loads((d / "a_replays.json").read_text())["files"]["a.replay"]["teammates"][0]["own"], 76)
        row = app_model.player_games(adb.files, {}, ["Falcon"], {"runninrobin"})[0]
        self.assertEqual((row["chests"], row["opened"], row["broken"]), (76, 46, 7))

        # the live map: the recount after the match (27 seen live + 15 broken from the replay) gets there too
        mgr = type("M", (), {"lock": __import__("threading").Lock(), "team": {self.b.stats.id: {"game": 27}},
                             "bump": lambda s: None})()
        self.a.map = mgr
        ct.App.on_teammate_counts(self.a, dict(m, game=42))
        self.assertEqual(mgr.team[self.b.stats.id]["game"], 42)
        ct.App.on_teammate_counts(self.a, dict(m, game=2))  # their next match started: keep the last one
        self.assertEqual(mgr.team[self.b.stats.id]["game"], 42)
        ct.App.on_teammate_counts(self.a, dict(m, team="other", game=99))
        self.assertEqual(mgr.team[self.b.stats.id]["game"], 42)

    def test_a_message_made_once_goes_to_every_relay(self):
        """All relays at once (Nov 19, 2026: two trackers on different relays never saw each other). The
        map's position message is rate limited, so it's made once and every relay sends that one."""
        link = ct.NetLink(self.a)
        made = iter([b'{"t": 1}', None, None, b'{"t": 2}', None])
        make = lambda: next(made)  # noqa: E731
        sent = {"hivemq": {}, "emqx": {}}
        out = {"hivemq": [], "emqx": []}
        for relay in ("hivemq", "emqx", "hivemq", "emqx", "hivemq"):
            got = link._shared("map", make)
            if got and got[0] != sent[relay].get("map"):
                out[relay].append(got[1])
                sent[relay]["map"] = got[0]
        self.assertEqual(out, {"hivemq": [b'{"t": 1}', b'{"t": 2}'], "emqx": [b'{"t": 1}', b'{"t": 2}']})
        self.assertEqual(ct.DEFAULT_CONFIG["mqtt_brokers"][0], "broker.hivemq.com:1883")
        self.assertGreaterEqual(len(link.brokers()), 3)

    def test_changing_code_switches_team(self):
        self.a.team.update(msg(self.b), "net")
        self.a.team.set_code("other")
        self.assertEqual(self.a.team.team_total(), 0)
        self.assertIsNotNone(self.a.team.update(msg(self.c), "net"))


@unittest.skipUnless(os.environ.get("CHEST_NET_TEST"), "set CHEST_NET_TEST=1 to test the real internet relay")
class RelayTests(unittest.TestCase):
    def test_two_players_see_each_other_live(self):
        ct.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.json"
        code = "test-" + secrets.token_hex(8)
        a, b = FakeApp("TestA", code), FakeApp("TestB", code)
        links = [ct.NetLink(a), ct.NetLink(b)]
        for l in links:
            l.start()
        try:
            def wait(cond, secs=25):
                end = time.time() + secs
                while time.time() < end:
                    if cond():
                        return True
                    time.sleep(0.2)
                return False

            self.assertTrue(wait(lambda: all(l.status == "connected" for l in links)), [l.status for l in links])
            a.stats.add(1); a.stats.add(1)
            t0 = time.time()
            self.assertTrue(wait(lambda: b.team.team_game() == 2), "B never saw A's chests")
            print(f"\n  relay delivered a chest in {time.time() - t0:.2f}s")
            b.stats.add(1)
            self.assertTrue(wait(lambda: a.team.team_game() == 3))
            self.assertIn("TestB joined the team", a.toasts)
            links[1].shutdown()
            self.assertTrue(wait(lambda: a.team.team_game() == 2), "A still counts B's game after B quit")
            self.assertEqual(a.team.team_total(), 3)
        finally:
            for l in links:
                l.shutdown()


if __name__ == "__main__":
    unittest.main()
