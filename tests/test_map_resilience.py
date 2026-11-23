"""The map worker keeps going: a locked file can't stop it for the rest of the session, and the game that
just ended reaches the map as soon as the chest counter has read its finished replay (Nov 19, 2026: the
map thread died on "Permission denied: maps/index.tmp" and the next game never showed on the map)."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
import map_tracker as mt  # noqa: E402

_isolate.check(ct)


class FakeApp:
    def __init__(self):
        self.cfg = {"name": "Robin", "map_share": True}
        self.stats = type("S", (), {"game": 0, "id": "aaaa1111"})()
        self.team = type("T", (), {"key": "teamkey"})()
        self.in_match = False

    def match_in_progress(self):
        return self.in_match


def make():
    tmp = Path(tempfile.mkdtemp())
    app = FakeApp()
    m = mt.MapManager(app, tmp, tmp / "demos", tmp / "reader.exe", window_rect=lambda: None, in_front=lambda: True)
    m.demos.mkdir(parents=True, exist_ok=True)
    m.maps_dir.mkdir(parents=True, exist_ok=True)
    m.reader.write_text("exe")
    return app, m


def reader_out(complete=True, live=False):
    route = [[t, 1000 * t, 0, 1000] for t in range(0, 30)]
    out = {"start_utc": "2026-11-19T16:58:34Z", "length_ms": 1_000_000, "branch": "++Fortnite+Release-43.10",
           "complete": complete, "map": {"players": [{"name": "RunninRobin", "recorder": True, "route": route}],
                                         "chests": [], "storm": []}}
    if live:
        out["live"] = True
    return out


class MapWorkerResilienceTests(unittest.TestCase):
    def test_the_game_that_just_ended_is_on_the_map_at_once(self):
        app, m = make()
        f = m.demos / "UnsavedReplay-2026.11.19-09.58.34.replay"
        f.write_bytes(b"x" * 100)  # written just now: the match ended seconds ago
        m.take_reader_output(f, reader_out(complete=True))
        self.assertIn(f.name, m.replay_maps)

    def test_a_replay_still_being_written_waits_for_the_worker(self):
        app, m = make()
        f = m.demos / "UnsavedReplay-2026.11.19-09.58.34.replay"
        f.write_bytes(b"x" * 100)
        m.take_reader_output(f, reader_out(complete=False))  # half-read and fresh: maybe still recording
        self.assertNotIn(f.name, m.replay_maps)
        m.take_reader_output(f, reader_out(complete=True, live=True))  # still recording
        self.assertNotIn(f.name, m.replay_maps)
        app.in_match = True  # fair play: never during a match
        m.take_reader_output(f, reader_out(complete=True))
        self.assertNotIn(f.name, m.replay_maps)

    def test_a_failed_index_save_is_logged_not_raised(self):
        app, m = make()
        m._index["a.replay"] = {"key": "1:1", "reader": 1}
        with mock.patch.object(Path, "replace", side_effect=PermissionError(13, "Permission denied")):
            m._save_index()  # must not raise
        m._save_index()
        self.assertIn("a.replay", m._load_index())

    def test_index_saves_from_two_threads_at_once(self):
        app, m = make()
        errors = []

        def save(n):
            try:
                for i in range(40):
                    with m._index_lock:
                        m._index[f"{n}-{i}.replay"] = {"key": "1:1", "reader": 1}
                    m._save_index()
            except Exception as e:  # pragma: no cover - the bug
                errors.append(e)

        ts = [threading.Thread(target=save, args=(n,)) for n in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(m._load_index()), 120)

    def test_a_crashed_map_loop_starts_again(self):
        app, m = make()
        runs = []

        def loop():
            runs.append(1)
            if len(runs) == 1:
                raise PermissionError(13, "Permission denied")

        with mock.patch.object(m._stop, "wait", return_value=False):  # no real waiting between tries
            m._guard(loop)
        self.assertEqual(len(runs), 2)

    def test_stopping_ends_the_retries(self):
        app, m = make()
        runs = []

        def loop():
            runs.append(1)
            raise RuntimeError("broken")

        m.stop()
        m._guard(loop)
        self.assertEqual(runs, [])


if __name__ == "__main__":
    unittest.main()
