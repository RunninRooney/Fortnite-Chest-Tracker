"""Tests for replay confirmation, broken chests and importing past matches.

Run:  python -m unittest discover -s tests
Uses fake replay files and a fake reader, so it doesn't need Fortnite or the .NET reader.
Set CHEST_REAL_DEMOS=1 to also check the header reader against your real Demos folder.
"""
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
_isolate.check(ct)


def write_replay(path: Path, live: bool, started: float, length_ms=900_000):
    """Writes just enough of a .replay file header for read_replay_header."""
    import datetime as dt

    ticks = int((dt.datetime.fromtimestamp(started) - dt.datetime(1, 1, 1)).total_seconds() * 10_000_000)
    name = "Unsaved Replay".encode("utf-16-le")
    data = struct.pack("<II", ct.REPLAY_MAGIC, 7) + struct.pack("<i", 1) + b"\0" * 20
    data += struct.pack("<III", length_ms, 1, 2) + struct.pack("<i", -(len(name) // 2)) + name
    data += struct.pack("<I", 1 if live else 0) + struct.pack("<q", ticks) + b"\0" * 64
    path.write_bytes(data)
    old = time.time() - 60
    os.utime(path, (old, old))


class FakeApp:
    def __init__(self, d: Path):
        self.cfg = dict(ct.DEFAULT_CONFIG, season_start="2026-11-01")
        self.stats = ct.Stats(d / "stats.json")
        self.toasts = []

    def toast(self, text, seconds=4.0):
        self.toasts.append(text)

    def flash(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.demos = self.d / "Demos"
        self.demos.mkdir()
        self.exe = self.d / "reader.exe"
        self.exe.write_text("fake")
        self.app = FakeApp(self.d)
        self.results = {}  # replay name -> reader JSON
        self.calls = []

        self.background_calls = []

        def fake_reader(exe, path, timeout=600, with_map=False, background=False, **kw):
            self.calls.append(path.name)
            if background:
                self.background_calls.append(path.name)
            return self.results[path.name]

        self._orig = ct.run_reader
        ct.run_reader = fake_reader
        self.w = ct.ReplayWatcher(self.app, demos=self.demos, exe=self.exe, db=ct.ReplayDB(self.d / "replays.json"))
        self.app.replays = self.w

    def tearDown(self):
        ct.run_reader = self._orig

    def result(self, name, opened, broken, guid=None, ok=True):
        self.results[name] = {"guid": guid or name, "complete": True, "recorder_found": ok,
                              "opened": opened, "broken": broken, "length_ms": 900_000}

    def drain(self):
        """Run the watcher's work loop until there's nothing left to read."""
        for _ in range(500):
            files = self.w._list()
            job = self.w.next_job(files, time.time())
            if not job:
                self.w.detect_names()  # what the watcher does once everything is read
                return
            _, name, hdr = job
            self.w.process(name, files[name][0], hdr)
        self.fail("watcher never finished")


class GameCountTests(unittest.TestCase):
    def test_before_replay(self):
        self.assertEqual(ct.game_count(5, 2, 0, None), 7)

    def test_replay_adds_broken_and_takes_max_opened(self):
        self.assertEqual(ct.game_count(5, 0, 0, {"opened": 6, "broken": 2}), 8)   # replay saw more
        self.assertEqual(ct.game_count(7, 0, 0, {"opened": 6, "broken": 2}), 9)   # OCR saw one the replay missed
        self.assertEqual(ct.game_count(5, 1, 0, {"opened": 5, "broken": 2}), 7)   # F6 for a broken chest isn't doubled
        self.assertEqual(ct.game_count(5, 3, 0, {"opened": 5, "broken": 2}), 8)   # extra F6 presses still count
        self.assertEqual(ct.game_count(5, 0, -1, {"opened": 5, "broken": 2}), 6)  # F7 after confirmation


class StatsTests(Base):
    def test_current_game_confirmed(self):
        s = self.app.stats
        s.new_game("t", replay="a.replay")
        for _ in range(4):
            s.add(1)
        self.assertEqual((s.game, s.total), (4, 4))
        self.assertEqual(s.confirm("a.replay", opened=5, broken=3), ("current", 4, 8))
        self.assertEqual((s.game, s.total), (8, 8))
        s.add(1, manual=True)  # F6 after confirmation still counts
        s.add(-1, manual=True)
        s.add(-1, manual=True)  # F7
        self.assertEqual((s.game, s.total), (7, 7))

    def test_past_game_confirmed_after_next_match_started(self):
        s = self.app.stats
        s.new_game("t", replay="a.replay")
        s.add(1); s.add(1)
        s._last_reset = -1e9
        s.new_game("t", replay="b.replay")
        s.add(1)
        self.assertEqual(s.confirm("a.replay", opened=2, broken=1), ("past", 2, 3))
        self.assertEqual((s.game, s.total), (1, 4))
        self.assertEqual(s.history[-1]["chests"], 3)

    def test_unlinked_returns_none(self):
        self.assertIsNone(self.app.stats.confirm("nope.replay", 1, 1))

    def test_double_new_game_signal_keeps_counts(self):
        s = self.app.stats
        s.new_game("log line")
        self.assertFalse(s.new_game("replay started", replay="a.replay"))  # same match
        self.assertEqual(s.replay, "a.replay")
        s.add(1)
        self.assertFalse(s.new_game("log line again"))  # debounced, game not wiped
        self.assertEqual(s.game, 1)

    def test_replay_noticed_late_is_still_this_match(self):
        # Nov 19: "match joined" 01:34:59, recording began 01:35:17, but the replay watcher was busy
        # reading an old replay and only saw it at 01:38:27. It used to start a new game there,
        # splitting the match (and counting its first 6 chests twice in TOTAL).
        s = self.app.stats
        s.new_game("match joined", debounce=20)
        joined = s.game_started
        for _ in range(6):
            s.add(1)
        s._last_reset -= 208  # 3.5 minutes later
        self.assertFalse(s.new_game("replay started", replay="m.replay", started=joined + 18))
        self.assertEqual((s.game, s.total, s.replay), (6, 6, "m.replay"))
        self.assertEqual(s.confirm("m.replay", opened=6, broken=1), ("current", 6, 7))
        self.assertEqual(s.total, 7)

    def test_replay_of_a_later_match_is_a_new_game(self):
        s = self.app.stats
        s.new_game("match joined", debounce=20)
        s.add(1)
        s._last_reset -= 1200
        # began 20 minutes after this game started: another match (its "joined" line was missed)
        self.assertTrue(s.new_game("replay started", replay="n.replay", started=s.game_started + 1200))
        self.assertEqual((s.game, s.total), (0, 1))
        # ...and a replay that began during a game already over is a new one too
        s.add(1)
        s.finish()
        s._last_reset -= 1200
        self.assertTrue(s.new_game("replay started", replay="o.replay", started=s.game_started + 30))

    def test_forget_screen_counts(self):
        s = self.app.stats
        s.import_game("old.replay", time.time() - 9999, 900, opened=10, broken=2)  # 12 from a replay
        s.add(1); s.add(1)                       # 2 fake screen counts in the lobby
        s._last_reset = -1e9
        s.new_game("t", replay="m.replay")       # a match: 3 screen counts, replay says 1 + 1
        s.add(1); s.add(1); s.add(1)
        s.add(1, manual=True)                    # one F6
        s.confirm("m.replay", opened=1, broken=1)
        self.assertEqual(s.total, 12 + 2 + 4)
        s.forget_screen_counts()
        # lobby game gone; match = replay's 1 opened + max(F6 1, broken 1)
        self.assertEqual((s.game, s.total), (2, 14))
        self.assertEqual(len(s.history), 1)

    def test_saved_and_reloaded(self):
        s = self.app.stats
        s.new_game("t", replay="a.replay")
        s.add(1)
        s.confirm("a.replay", 2, 1)
        s2 = ct.Stats(self.d / "stats.json")
        self.assertEqual((s2.game, s2.total, s2.replay, s2.confirmed), (3, 3, "a.replay", {"opened": 2, "broken": 1}))


class WatcherTests(Base):
    def test_header_reader(self):
        t = time.time() - 3600
        write_replay(self.demos / "x.replay", live=True, started=t)
        h = ct.read_replay_header(self.demos / "x.replay")
        self.assertTrue(h["live"])
        self.assertAlmostEqual(h["started"], t, delta=1)
        (self.demos / "junk.replay").write_bytes(b"nope")
        self.assertIsNone(ct.read_replay_header(self.demos / "junk.replay"))

    def test_backfill_this_season_only(self):
        season = ct.season_start_epoch("2026-11-01")
        write_replay(self.demos / "old.replay", False, season - 86400)
        for i in range(3):
            write_replay(self.demos / f"m{i}.replay", False, season + 86400 * (i + 1))
            self.result(f"m{i}.replay", opened=10 + i, broken=i)
        self.w.startup(self.w._list())
        self.drain()
        s = self.app.stats
        self.assertEqual(s.total, 10 + 11 + 12 + 0 + 1 + 2)
        self.assertEqual(s.imported, s.total)
        self.assertEqual(s.game, 0)  # imports never touch the current game
        self.assertNotIn("old.replay", self.calls)
        self.assertEqual(self.w.db.files["old.replay"]["status"], "skipped")

        # second start: nothing is read or counted again
        self.calls.clear()
        w2 = ct.ReplayWatcher(self.app, demos=self.demos, exe=self.exe, db=ct.ReplayDB(self.d / "replays.json"))
        self.w = w2
        self.drain()
        self.assertEqual(self.calls, [])
        self.assertEqual(s.total, 36)

    def test_renamed_replay_not_counted_twice(self):
        t = ct.season_start_epoch("2026-11-01") + 86400
        write_replay(self.demos / "a.replay", False, t)
        write_replay(self.demos / "My saved game.replay", False, t)
        self.result("a.replay", 5, 1, guid="G1")
        self.result("My saved game.replay", 5, 1, guid="G1")
        self.drain()
        self.assertEqual(self.app.stats.total, 6)

    def test_live_match_flow(self):
        """New replay file -> new game; OCR counts; match ends -> replay confirms and adds broken chests."""
        self.w.startup(self.w._list())
        s = self.app.stats
        write_replay(self.demos / "live.replay", True, time.time() - 30)
        self.w.check_new(self.w._list(), time.time())
        self.assertEqual(s.replay, "live.replay")
        self.assertTrue(self.w.handles_new_games)
        s.add(1); s.add(1); s.add(1)  # pop-ups seen
        self.assertIsNone(self.w.next_job(self.w._list(), time.time()))  # still recording: not read yet
        write_replay(self.demos / "live.replay", False, time.time() - 30)  # match ended
        self.result("live.replay", opened=4, broken=2)
        self.drain()
        self.assertEqual((s.game, s.total), (6, 6))
        self.assertEqual(s.confirmed, {"opened": 4, "broken": 2})
        self.assertIn("Replay check: 4 opened + 2 broken. Now 6", self.app.toasts)

    def test_live_replay_noticed_late_keeps_the_game(self):
        """The watcher only sees the new recording minutes in (it was reading an old replay)."""
        self.w.startup(self.w._list())
        s = self.app.stats
        s.new_game("match joined", debounce=20)
        s.add(1); s.add(1)
        s._last_reset -= 210
        write_replay(self.demos / "live.replay", True, s.game_started + 18)
        self.w.check_new(self.w._list(), time.time())
        self.assertEqual((s.replay, s.game, s.total), ("live.replay", 2, 2))
        self.assertNotIn("New game. Good luck!", self.app.toasts)

    def test_started_mid_match_links_without_reset(self):
        s = self.app.stats
        s.new_game("log line")
        s.add(1)
        write_replay(self.demos / "live.replay", True, time.time() - 120)
        self.w.startup(self.w._list())
        self.assertEqual((s.replay, s.game), ("live.replay", 1))

    def test_games_played_without_the_tracker_are_read_during_a_match_in_the_background(self):
        """Nov 20, 2026: past games used to wait for the lobby, so a backlog took many matches to come in."""
        t = ct.season_start_epoch("2026-11-01") + 86400
        write_replay(self.demos / "old.replay", False, t)
        self.result("old.replay", 3, 0)
        live = self.demos / "live.replay"
        write_replay(live, True, time.time())
        os.utime(live, None)  # being written right now
        job = self.w.next_job(self.w._list(), time.time())
        self.assertEqual(job[1], "old.replay")  # the match being played is no reason to wait
        self.assertTrue(self.w.playing())
        self.w.background = not self.app.stats.is_linked("old.replay") and self.w.playing()
        self.w.process("old.replay", self.demos / "old.replay", job[2])
        self.assertEqual(self.background_calls, ["old.replay"])  # read at the lowest priority, on one core
        self.assertEqual(self.app.stats.total, 3)
        self.assertFalse(any("Reading past replays" in t for t in self.app.toasts))  # no messages over the game

    def test_unreadable_replay_retried_then_dropped(self):
        t = ct.season_start_epoch("2026-11-01") + 86400
        write_replay(self.demos / "bad.replay", False, t)
        self.result("bad.replay", 0, 0, ok=False)
        self.drain()
        self.assertEqual(self.calls, ["bad.replay"] * 3)
        self.assertEqual(self.app.stats.total, 0)


class NewGameTests(Base):
    """Versions before 1.11 missed new matches: Fortnite creates the replay file empty and fills it in
    a moment later, and the empty file was written off for good."""

    def test_empty_new_file_is_looked_at_again(self):
        self.w.startup(self.w._list())
        s = self.app.stats
        s.new_game("t", replay="old.replay")
        s._last_reset = -1e9
        p = self.demos / "new.replay"
        p.write_bytes(b"")  # just created by Fortnite
        self.w.check_new(self.w._list(), time.time())
        self.assertEqual(s.replay, "old.replay")
        write_replay(p, True, time.time())
        os.utime(p, None)
        self.w.check_new(self.w._list(), time.time())
        self.assertEqual(s.replay, "new.replay")  # new game started

    def test_chest_after_replay_read_starts_new_game(self):
        s = self.app.stats
        s.new_game("t", replay="a.replay")
        s.add(1)
        s.confirm("a.replay", 3, 0)
        s._last_reset = -1e9
        s.add(1)  # the next match's first chest, its start was missed
        self.assertEqual((s.game, s.replay), (1, None))
        self.assertEqual(s.total, 4)

    def test_replay_linked_to_game_whose_start_was_missed(self):
        s = self.app.stats
        t = time.time() - 900
        s.new_game("chest after the last game's replay was read")  # unlinked, started at its first chest
        s.add(1); s.add(1)
        write_replay(self.demos / "m.replay", False, t)  # the match had started 15 min earlier
        self.result("m.replay", opened=3, broken=1)
        self.drain()
        self.assertEqual((s.replay, s.game), ("m.replay", 4))
        self.assertEqual(s.total, 4)  # confirmed, not imported on top

    def test_repair_double_counted(self):
        s = self.app.stats
        s.new_game("t", replay="a.replay")
        s.add(1)
        s.confirm("a.replay", 1, 0)
        for _ in range(30):  # later matches' pop-ups piled on (old version, before the auto new game)
            with s.lock:
                s.seen += 1
                s._recount()
        s.import_game("b.replay", 0, 900, 20, 5)  # ...and those matches were imported as well
        self.assertEqual(s.total, 31 + 25)
        s.drop_double_counted()
        self.assertEqual((s.total, s.game), (1 + 25, 0))


class ConsoleTeammateTests(Base):
    def setUp(self):
        super().setUp()
        t = ct.season_start_epoch("2026-11-01") + 86400
        for i in range(3):
            write_replay(self.demos / f"m{i}.replay", False, t + i * 3600)
            self.results[f"m{i}.replay"] = {"guid": f"g{i}", "complete": True, "recorder_found": True, "opened": 5,
                                            "broken": 0, "length_ms": 900_000, "recorder_name": "RunninRobin",
                                            "session_id": f"S{i}", "teammates": [mate("jFalcon", "PS5", 10 + i, 2)],
                                            "recorder_searches": 5, "build_breaks": 0, "treasures": [], "treasure_v": 3}
        self.app.cfg["fortnite_names"] = ["RunninRobin"]
        self.app.cfg["console_teammates"] = ["jfalcon"]
        self.app.guest_names = lambda: ct.App.guest_names(self.app)
        self.app.guest_stats = lambda: ct.App.guest_stats(self.app)
        self.app.cfg["team_code"] = ""
        self.app.team = ct.Team(self.app, self.d / "team.json")

    def test_tally_and_team_totals(self):
        s = self.app.stats
        s.new_game("t", replay="m2.replay")
        self.drain()
        g = self.app.guest_stats()["jfalcon"]
        self.assertEqual((g["name"], g["platform"], g["matches"], g["total"], g["game"]), ("jFalcon", "PS5", 3, 39, 14))
        self.assertEqual(self.app.team.team_total(), s.total + 39)
        self.assertEqual(self.app.team.team_game(), s.game + 14)
        self.assertIn("jFalcon (PS)", [p["name"] for p in self.app.team.players()])
        self.assertTrue(any("jFalcon: 14 chests" in t for t in self.app.toasts))

    def test_not_counted_twice_once_a_teammate_claims_the_name(self):
        self.drain()
        before = self.app.team.team_total()
        bro = {"app": "chesttracker", "team": "", "id": "abcdef012345", "name": "jamie", "game": 0, "total": 500,
               "ts": time.time(), "online": True, "fortnite_names": ["falcon", "jfalcon"]}
        self.app.team.update(bro, "net")
        # jFalcon's chests now come through his own total (credits), not as a console teammate
        self.assertEqual(self.app.team.team_total(), before - 39 + 500)
        self.assertNotIn("jfalcon", self.app.team.guests())

    def test_guests_shared_by_a_teammate_are_merged(self):
        self.app.cfg["console_teammates"] = []
        peer = {"app": "chesttracker", "team": "", "id": "abcdef012345", "name": "Sis", "game": 0, "total": 0,
                "ts": time.time(), "online": True,
                "guests": {"jfalcon": {"name": "jFalcon", "game": 2, "total": 40, "platform": "PS5"}}}
        self.app.team.update(peer, "net")
        self.assertEqual(self.app.team.team_total(), self.app.stats.total + 40)


def mate(name, platform, opened, broken, bot=False):
    return {"name": name, "platform": platform, "opened": opened, "broken": broken, "bot": bot,
            "searches": opened, "seen_opened": opened}


class WhoseChestsTests(Base):
    """A PC shared by two players (RunninRobin and SupremeLlama15), and a brother on PlayStation."""

    def result2(self, name, recorder, opened, broken, mates=(), session=None):
        self.results[name] = {"guid": name, "complete": True, "recorder_found": True, "opened": opened,
                              "broken": broken, "length_ms": 900_000, "recorder_name": recorder,
                              "recorder_platform": "WIN", "teammates": list(mates), "session_id": session or "S-" + name,
                              "recorder_searches": opened, "build_breaks": 0, "treasures": [], "treasure_v": 3}

    def setUp(self):
        super().setUp()
        t = ct.season_start_epoch("2026-11-01") + 86400
        for i in range(12):  # 12 matches RunninRobin recorded, brother on PS5 in each
            write_replay(self.demos / f"rr{i}.replay", False, t + i * 3600)
            self.result2(f"rr{i}.replay", "RunninRobin", 10, 2, [mate("jFalcon", "PS5", 5, 1)])
        for i in range(3):  # 3 matches SupremeLlama15 recorded on this PC; RunninRobin on another PC
            write_replay(self.demos / f"sb{i}.replay", False, t + 100000 + i * 3600)
            self.result2(f"sb{i}.replay", "SupremeLlama15", 7, 7, [mate("RunninRobin", "WIN", 4, 1),
                                                                   mate("jFalcon", "PS5", 3, 0)])
        write_replay(self.demos / "solo.replay", False, t + 200000)  # SupremeLlama15 without RunninRobin
        self.result2("solo.replay", "SupremeLlama15", 9, 9)

    def test_my_counts(self):
        r = self.results["sb0.replay"]
        self.assertEqual(ct.my_counts(r, {"runninrobin"}), (4, 1, "teammate"))
        self.assertEqual(ct.my_counts(r, {"supremellama15"}), (7, 7, "recorder"))
        self.assertEqual(ct.my_counts(self.results["solo.replay"], {"runninrobin"}), (0, 0, "absent"))
        self.assertEqual(ct.my_counts(r, set()), (7, 7, "recorder"))  # name not known yet

    def test_name_detected_and_only_mine_counted(self):
        self.drain()
        self.assertEqual(self.app.cfg["fortnite_names"], ["RunninRobin"])
        # 12 x (10+2) as recorder + 3 x (4+1) as teammate; SupremeLlama15's own chests not counted
        self.assertEqual(self.app.stats.total, 12 * 12 + 3 * 5)

    def test_name_change_recounts_without_rereading(self):
        self.drain()
        self.calls.clear()
        self.app.cfg["fortnite_names"] = ["SupremeLlama15"]
        self.w.reattribute()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.app.stats.total, 3 * 14 + 18)  # his 3 shared matches + his solo match

    def test_old_replays_reread_once_and_recounted(self):
        # the previous version counted every replay for whoever recorded it
        for name, r in self.results.items():
            self.app.stats.import_game(name, 0, 900, r["opened"], r["broken"])
            self.w.db.mark(name, status="imported", guid=name, opened=r["opened"], broken=r["broken"], started=0)
        self.assertEqual(self.app.stats.total, 12 * 12 + 3 * 14 + 18)
        self.drain()
        self.assertEqual(len(self.calls), 16)  # each re-read once
        self.assertEqual(self.app.stats.total, 12 * 12 + 3 * 5)
        self.calls.clear()
        self.drain()
        self.assertEqual(self.calls, [])

    def test_build_breaks_added_once_when_the_reader_learns_them(self):
        # read by 1.12's reader: no "build_breaks" yet
        self.results = {k: {kk: vv for kk, vv in v.items() if kk not in ("build_breaks", "treasures", "treasure_v")} for k, v in self.results.items()}
        self.drain()
        total = self.app.stats.total
        self.assertEqual(total, 12 * 12 + 3 * 5)
        # the new reader finds 3 more chests in each of RunninRobin's matches, broken with a cone
        for name, r in self.results.items():
            if r["recorder_name"] == "RunninRobin":
                r.update(broken=r["broken"] + 3, build_breaks=3, treasures=[], treasure_v=3)
            else:
                r.update(build_breaks=0, treasures=[], treasure_v=3)
        os.utime(self.exe, (time.time() + 5, time.time() + 5))  # the reader gets rebuilt
        self.calls.clear()
        self.drain()
        self.assertEqual(len(self.calls), 16)  # each re-read once
        self.assertEqual(self.app.stats.total, total + 12 * 3)
        self.calls.clear()
        self.drain()
        self.assertEqual(self.calls, [])

    def test_old_reader_doesnt_loop_and_retries_after_rebuild(self):
        for name, r in self.results.items():
            self.app.stats.import_game(name, 0, 900, r["opened"], r["broken"])
            self.w.db.mark(name, status="imported", guid=name, opened=r["opened"], broken=r["broken"], started=0)
        new = dict(self.results)
        self.results = {k: {kk: vv for kk, vv in v.items()
                            if kk not in ("teammates", "recorder_name", "session_id", "recorder_searches",
                                          "build_breaks", "treasures", "treasure_v")}
                        for k, v in new.items()}  # what the old reader reports
        total = self.app.stats.total
        self.drain()
        self.assertEqual(len(self.calls), 16)  # each tried once...
        self.drain()
        self.assertEqual(len(self.calls), 16)  # ...and not again with the same old reader
        self.assertEqual(self.app.stats.total, total)  # counts unchanged
        self.results = new
        os.utime(self.exe, (time.time() + 5, time.time() + 5))  # the reader gets rebuilt
        self.drain()
        self.assertEqual(len(self.calls), 32)
        self.assertEqual(self.app.stats.total, 12 * 12 + 3 * 5)

    def test_screen_counts_dropped_when_someone_else_played(self):
        s = self.app.stats
        s.new_game("t", replay="solo.replay")
        s.add(1); s.add(1); s.add(1)  # pop-ups seen while SupremeLlama15 played on this PC
        self.app.cfg["fortnite_names"] = ["RunninRobin"]
        self.drain()
        self.assertEqual(s.game, 0)

    def test_credits_for_console_teammate(self):
        self.drain()
        credits = ct.build_credits(self.w.db.files.values(), self.w.my_names())
        # not RunninRobin (that's me); SupremeLlama15 for the matches he recorded on this PC
        self.assertEqual(set(credits), {"jfalcon", "supremellama15"})
        self.assertEqual(len(credits["jfalcon"]), 15)
        self.assertEqual(sum(o + b for o, b, _ in credits["jfalcon"].values()), 12 * 6 + 3 * 3)
        self.assertEqual(sum(o + b for o, b, _ in credits["supremellama15"].values()), 3 * 14 + 18)

    def test_credits_applied_once_on_brothers_pc(self):
        self.drain()
        msg = {"app": "chesttracker", "type": "credits", "team": "", "from": "Robin",
               "credits": ct.build_credits(self.w.db.files.values(), self.w.my_names())}
        # the brother's tracker: his own PC, his names
        d = Path(tempfile.mkdtemp())
        (d / "Demos").mkdir()
        bro = FakeApp(d)
        bro.cfg["fortnite_names"] = ["Falcon"]
        bro.team = type("T", (), {"key": ""})()
        bro.replays = ct.ReplayWatcher(bro, demos=d / "Demos", exe=self.exe, db=ct.ReplayDB(d / "r.json"))
        apply = lambda m=None: ct.App.apply_credits(bro, m)
        apply(msg)
        self.assertEqual(bro.stats.total, 0)  # "jFalcon" isn't one of his names yet
        bro.cfg["fortnite_names"] = ["Falcon", "jFalcon"]
        apply()  # names changed: the credits already received now apply
        self.assertEqual(bro.stats.total, 12 * 6 + 3 * 3)
        apply(msg)
        apply(msg)  # sent again (retained message, reconnects): nothing doubles
        self.assertEqual(bro.stats.total, 12 * 6 + 3 * 3)
        self.assertTrue(any("Added 81 chests from 15 matches" in t for t in bro.toasts))

    def test_credits_from_other_team_ignored(self):
        bro = FakeApp(Path(tempfile.mkdtemp()))
        bro.cfg["fortnite_names"] = ["jFalcon"]
        bro.team = type("T", (), {"key": "ourteam"})()
        bro.replays = self.w
        ct.App.apply_credits(bro, {"type": "credits", "team": "someoneelse", "from": "x",
                                   "credits": {"jfalcon": {"m1": [400, 0, 0]}}})
        self.assertEqual(bro.stats.total, 0)


@unittest.skipUnless(os.environ.get("CHEST_REAL_DEMOS"), "set CHEST_REAL_DEMOS=1 to read the real Demos folder")
class RealDemosTests(unittest.TestCase):
    def test_headers(self):
        files = sorted(ct.DEMOS_DIR.glob("*.replay"))
        self.assertTrue(files)
        for p in files:
            h = ct.read_replay_header(p)
            self.assertIsNotNone(h, p.name)
            stamp = time.strftime("%Y.%m.%d-%H.%M.%S", time.localtime(h["started"]))
            self.assertIn(stamp, p.name)  # header time matches the file name


if __name__ == "__main__":
    unittest.main()
