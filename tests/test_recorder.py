"""Recording every match with OBS: start/stop with the match, keep the last 5, clips around real finds.

Run:  python -m unittest discover -s tests
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import match_recorder as mr  # noqa: E402
import treasures  # noqa: E402


class FakeObs:
    """Stands in for OBS: records to files in a temp folder."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.active = False
        self.calls = []
        self.n = 0
        self.user_recording = False

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def request(self, kind, data=None):
        self.calls.append(kind)
        if kind == "GetRecordStatus":
            return {"outputActive": self.active or self.user_recording, "outputDuration": 500}
        if kind == "StartRecord":
            self.active = True
            return {}
        if kind == "StopRecord":
            self.active = False
            self.n += 1
            path = self.folder / f"2026-11-21 0{self.n}-00-00.mp4"
            path.write_bytes(b"video")
            return {"outputPath": str(path)}
        raise AssertionError(kind)


class App:
    def __init__(self):
        self.cfg = {"record_matches": True}
        self.toasts = []

    def toast(self, text, seconds=4):
        self.toasts.append(text)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.obs = FakeObs(self.d)
        self.app = App()
        self.rec = mr.MatchRecorder(self.app, self.d / "recordings.json", client_factory=self.obs)

    def run_jobs(self):
        while self.rec._jobs:
            self.rec._jobs.pop(0)()

    def match(self):
        self.rec.match_started()
        self.run_jobs()
        r = self.rec.recordings[-1]
        self.rec.match_over()
        self.run_jobs()
        return r

    def test_each_match_is_recorded_and_the_last_5_kept(self):
        for _ in range(7):
            self.match()
        self.assertEqual(self.obs.calls.count("StartRecord"), 7)
        videos = sorted(p.name for p in self.d.glob("*.mp4"))
        self.assertEqual(len(videos), 5)
        self.assertEqual(videos[0], "2026-11-21 03-00-00.mp4")  # the 2 oldest were deleted
        self.assertEqual(len(json.loads((self.d / "recordings.json").read_text())["recordings"]), 5)

    def test_real_find_keeps_the_whole_match_video_for_good(self):
        r = self.match()
        r["end"] = r["start"] + 1200  # a 20-minute match
        started = r["start"] + 30  # the replay began 30 s into the video
        finds = [{"t": 200.0, "prize": "gizmo", "won": True, "name": "RunninRobin"},
                 {"t": 412.0, "prize": treasures.PIRATE_SHOP, "won": True, "name": "JayDaAsianDude"}]
        self.rec.replay_read("a.replay", started, finds, treasures.describe)
        self.run_jobs()
        kept = Path(r["path"])
        self.assertTrue(kept.name.startswith("Pirate Shop Access - JayDaAsianDude"))
        self.assertTrue(kept.name.endswith("(at 7m22s).mp4"))  # 30 + 412 s into the video
        self.assertEqual(kept.read_bytes(), b"video")  # the full recording, untrimmed
        self.assertEqual([f["name"] for f in r["finds"]], ["Pirate Shop Access"])  # not the sprite gizmo
        for _ in range(6):
            self.match()
        self.assertTrue(kept.exists())  # that match stays, past the last 5
        self.assertEqual(len(list(self.d.glob("*.mp4"))), 6)
        self.assertTrue(any("Match video kept" in t for t in self.app.toasts))

    def test_only_sprite_gizmos_keeps_nothing_extra(self):
        r = self.match()
        r["end"] = r["start"] + 1200
        self.rec.replay_read("a.replay", r["start"], [{"t": 100.0, "prize": "gizmo", "won": True}],
                             treasures.describe)
        self.run_jobs()
        self.assertFalse(r.get("keep"))
        self.assertTrue(Path(r["path"]).name.startswith("2026-11-21"))  # not renamed

    def test_a_teammates_shared_find_keeps_my_video(self):
        """Nov 22, 2026: Falcon's loading screen wasn't in my replay (too far away), so the video was deleted."""
        r = self.match()
        r["end"] = r["start"] + 1000
        self.rec.shared_find(r["start"] + 588, "DelilahPrize:dpid_item27", "Falcon", treasures.describe)
        self.run_jobs()
        kept = Path(r["path"])
        self.assertTrue(r["keep"])
        self.assertEqual(kept.name[:len("Week 4 Loading Screen - Falcon")], "Week 4 Loading Screen - Falcon")
        self.assertTrue(kept.name.endswith("(at 9m48s).mp4"))
        for _ in range(6):
            self.match()
        self.assertTrue(kept.exists())

    def test_a_shared_find_during_the_match_is_named_when_it_ends(self):
        self.rec.match_started()
        self.run_jobs()
        r = self.rec.recordings[-1]
        self.rec.shared_find(time.time(), "DelilahPrize:dpid_item26", "llama", treasures.describe)
        self.run_jobs()
        self.assertTrue(r["keep"])
        self.rec.match_over()
        self.run_jobs()
        self.assertTrue(Path(r["path"]).name.startswith("Week 4 Jam Track - llama"))
        self.assertTrue(Path(r["path"]).exists())

    def test_the_same_find_from_my_replay_and_its_finder_counts_once(self):
        r = self.match()
        r["end"] = r["start"] + 1200
        self.rec.replay_read("a.replay", r["start"], [{"t": 300.0, "prize": "DelilahPrize:dpid_item26", "won": True,
                                                       "name": "llama"}], treasures.describe)
        self.rec.shared_find(r["start"] + 302, "DelilahPrize:dpid_item26", "llama", treasures.describe)
        self.run_jobs()
        self.assertEqual(len(r["finds"]), 1)

    def test_shared_gizmos_and_old_finds_keep_nothing(self):
        r = self.match()
        r["end"] = r["start"] + 1200
        self.rec.shared_find(r["start"] + 100, "gizmo", "llama", treasures.describe)
        self.rec.shared_find(r["start"] - 4000, "DelilahPrize:dpid_item26", "llama", treasures.describe)
        self.run_jobs()
        self.assertFalse(r.get("keep"))

    def test_off_means_nothing_is_recorded(self):
        self.app.cfg["record_matches"] = False
        self.rec.match_started()
        self.run_jobs()
        self.assertEqual(self.obs.calls, [])

    def test_a_recording_i_started_myself_is_left_alone(self):
        self.obs.user_recording = True
        self.rec.match_started()
        self.run_jobs()
        self.rec.match_over()
        self.run_jobs()
        self.assertNotIn("StartRecord", self.obs.calls)
        self.assertNotIn("StopRecord", self.obs.calls)

    def test_requeued_from_the_results_screen(self):
        self.rec.match_started()
        self.run_jobs()
        self.rec.match_started()  # no "over" in between: the last one is stopped first
        self.run_jobs()
        self.assertEqual(self.obs.calls.count("StopRecord"), 1)
        self.assertEqual(sum(1 for r in self.rec.recordings if r.get("recording")), 1)

    def test_obs_not_actually_recording_is_noticed(self):
        """Nov 21, 2026: with OBS's Settings window open, StartRecord succeeds but nothing records."""
        start = self.obs.request
        self.obs.request = lambda kind, data=None: {} if kind == "StartRecord" else start(kind, data)
        mr.time.sleep, sleep = (lambda s: None), mr.time.sleep
        try:
            self.rec.match_started()
            self.run_jobs()
        finally:
            mr.time.sleep = sleep
        self.assertEqual(self.rec.recordings, [])
        self.assertTrue(any("didn't start recording" in t for t in self.app.toasts))

    def test_files_it_didnt_record_are_never_deleted(self):
        mine = self.d / "my own video.mp4"
        mine.write_bytes(b"x")
        for _ in range(8):
            self.match()
        self.assertTrue(mine.exists())

    def test_obs_password_answer(self):
        # obs-websocket 5 authentication: base64(sha256(base64(sha256(password + salt)) + challenge))
        import base64
        import hashlib

        secret = base64.b64encode(hashlib.sha256(b"pw" + b"salt").digest()).decode()
        want = base64.b64encode(hashlib.sha256((secret + "chal").encode()).digest()).decode()
        self.assertEqual(mr._auth("pw", "salt", "chal"), want)


if __name__ == "__main__":
    unittest.main()
