"""Counting chests from Fortnite's XP pop-up: one XP number with what it was for stacked under it
("+2,700 XP / SPRITE CHEST / RARE CHEST X2"). Line positions are from real reads (debug frames of
a match on Nov 19, 2026, 2576x1048 window) and a screenshot (2000x843)."""
import datetime as dt
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
_isolate.check(ct)

import numpy as np  # noqa: E402

CFG = ct.DEFAULT_CONFIG
# where the screenshot shows the merged pop-up (pixels of a 2000x843 window)
XP_BOX = (1215, 243, 1327, 268)
LABEL_BOX = (1292, 269, 1352, 285)


def popup(xp_text="+ 1,200XP", label="CHEST X2"):
    return [(xp_text, XP_BOX), (label, LABEL_BOX)]


def read(lines):
    return ct.read_xp_popup(lines, CFG)


class ParseTests(unittest.TestCase):
    def test_xp(self):
        for text, want in [("+ 1,200XP", 1200), ("+600xP", 600), ("Chest +600 XP", 600), ("+1.800 XP", 1800),
                           ("+1,2O0XP", 1200), ("+75", 75), ("+380.", 380), ("CHEST", None), ("XP", None)]:
            self.assertEqual(ct.parse_xp(text), want, text)

    def test_mult(self):
        for text, want in [("CHEST X2", 2), ("CHESTX3", 3), ("Chest x 4", 4), ("CHEST ×5", 5), ("CHEST", None),
                           ("CHEST X1", None), ("Chest +600 XP", None), ("RARE CHESTX2", 2)]:
            self.assertEqual(ct.parse_mult(text), want, text)


class ReadPopupTests(unittest.TestCase):
    def test_merged_popup(self):
        p = read(popup())
        self.assertEqual((p["xp"], p["chests"], p["pure"]), (1200, 2, True))

    def test_x_read_as_its_own_word(self):
        p = read([("+ 1,200XP", XP_BOX), ("CHEST", (1292, 269, 1336, 285)), ("X2", (1338, 269, 1352, 285))])
        self.assertEqual((p["xp"], p["chests"]), (1200, 2))

    def test_single_popup(self):
        p = read(popup("+600xP", "CHEST"))
        self.assertEqual((p["xp"], p["chests"], p["pure"]), (600, 1, True))
        self.assertIsNotNone(ct.find_chest_popup(popup("+600xP", "CHEST"), CFG))

    def test_stacked_lines(self):
        p = read([("+2,700xP", (165, 154, 310, 185)), ("SPRITE CHEST", (214, 185, 329, 209)),
                  ("RARE CHEST X2", (215, 215, 342, 238))])
        self.assertEqual((p["chests"], p["pure"]), (3, False))
        p = read([("+380.", (182, 134, 291, 187)), ("SPRITE LEVEL", (247, 185, 357, 208)),
                  ("RARE CHEST", (247, 215, 350, 239)), ("X60", (18, 268, 69, 294))])
        self.assertEqual((p["chests"], p["pure"]), (1, False))
        # the same chest kind twice, with something else between
        p = read([("+2,050xp", (163, 138, 310, 170)), ("RARE CHEST", (196, 169, 300, 195)),
                  ("MAXIMUMAMMO", (197, 200, 342, 222)), ("653m", (80, 222, 137, 249)),
                  ("RARECHEST", (183, 231, 286, 254))])
        self.assertEqual(p["chests"], 2)

    def test_not_chest_popups(self):
        for lines in ([("+ 900xp", (165, 167, 280, 198)), ("NEW STORM PHASE", (199, 197, 360, 224))],
                      [("-200xP", (170, 168, 276, 198)), ("WEAKPOINTSTREAK", (234, 201, 391, 221))],
                      [("SEARCH", (35, 366, 122, 394))]):
            self.assertIsNone(read(lines), lines)

    def test_pickup_text_nearby_is_ignored(self):
        p = read([("SMALL SHIELD POTION", (21, 109, 285, 136)), ("UNCOMMON", (18, 141, 122, 165)),
                  ("x3", (149, 144, 179, 165)), ("+600XP", (180, 167, 297, 199)), ("CHEST", (258, 198, 316, 224))])
        self.assertEqual((p["xp"], p["chests"], p["pure"]), (600, 1, True))
        p = read([("+ 600XP", (183, 167, 299, 199)), ("X30", (106, 191, 154, 217)), ("CHEST", (260, 198, 319, 224))])
        self.assertEqual(p["chests"], 1)


class LogicTests(unittest.TestCase):
    def run_reads(self, reads, logic=None):
        """reads: [(t, xp, mult)] or (t, None) for a read that saw no pop-up. Returns total counted."""
        logic = logic or ct.ChestLogic()
        total = 0
        for r in reads:
            if r[1] is None and len(r) == 2:
                total += logic.sample(False, r[0])
            else:
                total += logic.sample(True, r[0], r[1], r[2])
        return total

    @staticmethod
    def gone(t0, t1, step=0.2):
        n = round((t1 - t0) / step)
        return [(round(t0 + i * step, 2), None) for i in range(n)]

    def test_one_popup_read_many_times_is_one_chest(self):
        self.assertEqual(self.run_reads([(i * 0.1, 600, None) for i in range(20)]), 1)

    def test_second_chest_while_popup_up(self):
        reads = [(i * 0.1, 600, None) for i in range(8)] + [(0.8 + i * 0.1, 1200, 2) for i in range(15)]
        self.assertEqual(self.run_reads(reads), 2)

    def test_three_back_to_back(self):
        reads = ([(i * 0.1, 600, None) for i in range(5)] + [(0.5 + i * 0.1, 1200, 2) for i in range(5)]
                 + [(1.0 + i * 0.1, 1800, 3) for i in range(15)])
        self.assertEqual(self.run_reads(reads), 3)

    def test_x_unreadable_xp_still_rises(self):
        reads = [(i * 0.1, 600, None) for i in range(5)] + [(0.5 + i * 0.1, 1200, None) for i in range(10)]
        self.assertEqual(self.run_reads(reads), 2)

    def test_xp_counting_up_is_one_chest(self):
        # real: "+600" -> "+1,054" (counting up) -> "+1,200" with "CHEST X2"
        reads = [(0.0, 600, None), (0.2, 600, None), (0.4, 1054, 2), (0.6, 1200, 2), (0.8, 1200, 2)]
        self.assertEqual(self.run_reads(reads), 2)
        reads = [(0.0, 600, None), (0.2, 1054, None), (0.4, 1200, None), (0.6, 1200, None)]
        self.assertEqual(self.run_reads(reads), 2)

    def test_one_misread_does_not_count(self):
        reads = [(i * 0.1, 600, None) for i in range(5)] + [(0.5, None, 7)] + [(0.6 + i * 0.1, 600, None) for i in range(5)]
        self.assertEqual(self.run_reads(reads), 1)
        reads = [(i * 0.1, 600, None) for i in range(5)] + [(0.5, 6000, None)] + [(0.6, 600, None)]
        self.assertEqual(self.run_reads(reads), 1)

    def test_first_read_already_x2(self):
        # the "+600" moment was missed; X and XP agree, so both chests count at once
        self.assertEqual(self.run_reads([(0.0, 1200, 2)]), 2)
        # X alone needs a second read...
        self.assertEqual(self.run_reads([(0.0, None, 2)]), 1)
        self.assertEqual(self.run_reads([(0.0, None, 2), (0.1, None, 2)]), 2)
        # ...or the pop-up going away right after
        self.assertEqual(self.run_reads([(0.0, None, 2), (0.2, None), (0.4, None)]), 2)

    def test_slow_read_is_not_the_popup_going_away(self):
        # a read took 1.1 s while the game was busy (real, 01:37:51): same pop-up, one chest
        reads = [(0.0, 600, None), (1.12, 600, None), (1.3, 600, None)]
        self.assertEqual(self.run_reads(reads), 1)

    def test_one_missed_read_is_the_same_popup(self):
        reads = [(i * 0.1, 600, None) for i in range(5)] + [(0.6, None), (0.9, 600, None), (1.0, 600, None)]
        self.assertEqual(self.run_reads(reads), 1)

    def test_next_chest_after_popup_gone(self):
        # 1.7 s pop-up, gone 0.7 s (seen gone twice), then the next one (real, 01:43:14)
        reads = [(i * 0.1, 600, None) for i in range(17)] + self.gone(1.8, 2.2) + [(2.4, 600, None), (2.5, 600, None)]
        self.assertEqual(self.run_reads(reads), 2)

    def test_x_count_carried_into_a_new_popup(self):
        # X2 gone for 1.2 s, then back as X3: one more chest, not three
        reads = [(0.0, 1200, 2), (0.1, 1200, 2)] + self.gone(0.2, 1.2) + [(1.3, 1800, 3), (1.4, 1800, 3)]
        self.assertEqual(self.run_reads(reads), 3)
        # ...but a new plain pop-up after an X2 is a fresh one
        reads = [(0.0, 1200, 2), (0.1, 1200, 2)] + self.gone(0.2, 1.2) + [(1.3, 600, None), (1.4, 1200, 2),
                                                                         (1.5, 1200, 2)]
        self.assertEqual(self.run_reads(reads), 4)

    def test_new_plain_popup_right_after_an_x2(self):
        # X2 gone at 0.2 s, a plain "+600 XP" up at 0.5 s: quicker than GONE_FOR, but the X dropped
        reads = [(0.0, 1200, 2), (0.1, 1200, 2), (0.2, 1200, 2), (0.35, None), (0.5, 600, None), (0.6, 600, None)]
        self.assertEqual(self.run_reads(reads), 3)
        # a read that only lost the X (XP still 1,200) is the same pop-up
        reads = [(0.0, 1200, 2), (0.1, 1200, 2), (0.3, None), (0.5, 1200, None), (0.6, 1200, 2)]
        self.assertEqual(self.run_reads(reads), 2)

    def test_real_rare_and_sprite_burst(self):
        """Debug frames of 01:42:52-01:42:58: a rare chest, a sprite chest, two more rare chests.
        The old version counted 9 here."""
        frames = [
            (296.68, [("+380.", (182, 134, 291, 187)), ("SPRITE LEVEL", (247, 185, 357, 208)),
                      ("RARE CHEST", (247, 215, 350, 239))]),
            (297.57, [("+ 1,050xP", (164, 154, 308, 186)), ("SPRITE LEVEL", (230, 185, 341, 209)),
                      ("RARE CHEST", (229, 216, 333, 239))]),
            (298.80, [("+1,050XP", (168, 171, 309, 200)), ("SPRITE LEVEL", (228, 199, 342, 227)),
                      ("RARE CHEST", (230, 231, 333, 255))]),
            (299.02, []),
            (299.25, [("+900xp ", (177, 167, 307, 198)), ("SPRITE CHEST", (201, 199, 318, 223))]),
            (299.95, [("+ 900xP", (178, 167, 297, 198)), ("SPRITE CHEST", (212, 199, 328, 223))]),
            (300.64, [("+1,379", (165, 133, 315, 188)), ("SPRITE CHEST", (226, 184, 343, 208)),
                      ("X12", (153, 197, 199, 224)), ("RARECHEST", (246, 215, 349, 238))]),
            (301.11, [("+1,800xp", (165, 153, 311, 186)), ("SPRITE CHEST", (226, 185, 342, 210)),
                      ("RARE CHEST", (225, 215, 329, 240))]),
            (301.86, [("+ 1,800xP", (163, 153, 312, 186)), ("SPRITE CHEST", (226, 185, 342, 209)),
                      ("RARE CHEST", (227, 215, 330, 238))]),
            (302.19, [("+2,482xP", (164, 148, 312, 187)), ("SPRITE CHEST", (212, 182, 333, 211)),
                      ("RARE CHESTX2", (215, 215, 342, 237))]),
            (302.39, [("+2,700xP", (165, 154, 310, 185)), ("SPRITE CHEST", (214, 185, 329, 209)),
                      ("RARE CHEST X2", (215, 215, 342, 238))]),
            (303.80, [("+2,700xP", (160, 149, 311, 189)), ("SPRITE CHEST", (213, 185, 329, 208)),
                      ("RARE CHEST X2", (213, 215, 343, 239))]),
            (304.20, []), (304.40, []), (304.65, []),
        ]
        logic = ct.ChestLogic()
        self.assertEqual(sum(ct.sample_popup(logic, read(lines), t) for t, lines in frames), 4)


class VerifiedOpenedTests(unittest.TestCase):
    """Live counts checked against the replay, chest by chest (ct.verified_opened)."""
    START = 1_000_000.0

    def chests(self, *ts, broken=()):
        return [{"t": t, "broken": False} for t in ts] + [{"t": t, "broken": True} for t in broken]

    def v(self, live, chests, upto=None):
        return ct.verified_opened([self.START + t for t in live], chests, self.START, upto)

    def test_matched_and_missed(self):
        c = self.chests(10, 30, 50, broken=(40,))
        self.assertEqual(self.v([10.2, 30.4, 50.1], c), 3)
        self.assertEqual(self.v([10.2, 50.1], c), 3)   # the screen missed one: the replay adds it

    def test_double_count_dropped_x2_kept(self):
        c = self.chests(10, 30)
        self.assertEqual(self.v([10.2, 10.5, 30.1], c), 2)  # 0.3 s apart: one pop-up counted twice
        # a "CHEST X2" the replay missed: 1.2 s after the first count, kept
        self.assertEqual(self.v([10.2, 11.4, 30.1], c), 3)

    def test_popup_the_replay_missed_counts(self):
        self.assertEqual(self.v([10.2, 20.0, 30.1], self.chests(10, 30)), 3)

    def test_mid_match(self):
        c = self.chests(10, 30, 50, 70)
        # replay read up to 40 s: 2 chests; live counted 10 and 50 so far (missed 30)
        self.assertEqual(self.v([10.2, 50.3], c, upto=40), 3)
        # live counts after the read are counted as they are, not checked yet
        self.assertEqual(self.v([10.2, 30.1, 50.3, 60.0], c, upto=40), 4)

    def test_no_replay_chests(self):
        self.assertEqual(self.v([1.0, 5.0], []), 2)
        self.assertEqual(self.v([], self.chests(3, broken=(4,))), 1)


class CheckedStatsTests(unittest.TestCase):
    """Stats with live counts checked against the replay (mid-match and at the end)."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "stats.json"
        self.s = ct.Stats(self.path)
        self.s.new_game("t", replay="m.replay")
        self.start = time.time() - 600

    def live(self, *ts):
        """Live counts at these seconds into the replay (~0.9 s after each chest's search began)."""
        self.s.add(len(ts))
        self.s.seen_at[-len(ts):] = [self.start + t for t in ts]
        self.s._recount()

    def chests(self, *ts, broken=()):
        return [{"t": t, "broken": False} for t in ts] + [{"t": t, "broken": True} for t in broken]

    def test_mid_match_fixes_both_ways(self):
        self.live(10.9, 11.2, 50.9)  # 11.2: one pop-up counted twice; the chest at 30 s was missed
        self.assertEqual(self.s.game, 3)
        r = {"chests": self.chests(10, 30, 50, broken=(40,)), "read_upto": 55.0,
             "start_utc": dt.datetime.fromtimestamp(self.start, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")}
        self.assertEqual(self.s.confirm_partial("m.replay", 3, 1, **ct._replay_check(r, None, live=True)), (3, 4))
        self.assertEqual(self.s.game, 4)          # 3 chests + 1 broken
        self.assertIsNone(self.s.confirmed)       # the game stays open
        self.live(70.9)                           # after the read: counts right away
        self.assertEqual(self.s.game, 5)

    def test_final_read_and_restart(self):
        self.live(10.9, 30.9)
        self.s.confirm("m.replay", 3, 0, chests=self.chests(10, 30, 45), start=self.start)
        self.assertEqual(self.s.game, 3)
        again = ct.Stats(self.path)               # the times are saved
        self.assertEqual((again.game, len(again.seen_at)), (3, 2))

    def test_final_read_takes_what_the_reader_returns(self):
        # the watcher passes _replay_check(..., live=False) straight to confirm() (1.16 crashed on "upto")
        self.live(10.9, 30.9)
        r = {"chests": self.chests(10, 30, 45), "read_upto": 60.0, "length_ms": 60000,
             "start_utc": dt.datetime.fromtimestamp(self.start, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")}
        self.assertEqual(self.s.confirm("m.replay", 3, 0, **ct._replay_check(r, None, live=False)),
                         ("current", 2, 3))
        self.s.new_game("next", replay="n.replay", debounce=0)
        self.assertEqual(self.s.confirm("m.replay", 3, 0, **ct._replay_check(r, None, live=False))[0], "past")

    def test_past_game(self):
        self.live(10.9, 11.1, 30.9)               # a double count
        self.s.new_game("next", replay="n.replay", debounce=0)
        self.assertEqual(self.s.total, 3)
        self.assertEqual(self.s.confirm("m.replay", 2, 1, chests=self.chests(10, 30, broken=(20,)), start=self.start),
                         ("past", 3, 3))          # 2 checked + 1 broken
        self.assertEqual(self.s.total, 3)

    def test_without_chest_times_as_before(self):
        self.live(10.9, 11.1, 30.9)
        self.s.confirm("m.replay", 2, 1)
        self.assertEqual(self.s.game, 4)          # max(seen 3, opened 2) + 1

    def test_broken_never_goes_down_mid_match(self):
        self.live(10.9)
        kw = {"chests": self.chests(10, broken=(20, 25)), "start": self.start, "upto": 30}
        self.s.confirm_partial("m.replay", 1, 2, **kw)
        kw = {"chests": self.chests(10, broken=(20,)), "start": self.start, "upto": 21}
        self.s.confirm_partial("m.replay", 1, 1, **kw)  # stopped early on a half-written chunk
        self.assertEqual(self.s.game, 3)

    def test_replay_start(self):
        self.assertAlmostEqual(ct.replay_start({"start_utc": "2026-11-19T08:35:17.9310000Z"}),
                               dt.datetime(2026, 11, 19, 8, 35, 17, 931000, dt.timezone.utc).timestamp(), places=3)
        self.assertEqual(ct.replay_start({}, {"started": 5.0}), 5.0)
        self.assertEqual(ct._replay_check({"opened": 1}, None, live=True), {})  # an old reader: as before
        self.assertIsNone(ct._replay_check({"chests": [], "length_ms": 0}, None, live=True)["upto"])


class FakeReader:
    def __init__(self, result):
        self.result, self.calls, self.sizes = result, 0, []

    def lines(self, img):
        self.calls += 1
        self.sizes.append(img.shape[:2])
        return self.result(img) if callable(self.result) else self.result


class GateTests(unittest.TestCase):
    def setUp(self):
        self.d = ct.Detector(app=None)
        self.img = np.full((385, 462, 3), 90, np.uint8)

    def test_frame_changed(self):
        a = ct.Detector.thumb(self.img)
        self.assertFalse(ct.frame_changed(a, a.copy()))
        b = self.img.copy()
        b[100:130, 150:260] = 255  # a line of text appears
        self.assertTrue(ct.frame_changed(a, ct.Detector.thumb(b)))
        c = self.img.copy()
        c[200, 200] = 255  # one pixel: noise
        self.assertFalse(ct.frame_changed(a, ct.Detector.thumb(c)))

    def test_unchanged_picture_is_not_read_again(self):
        self.d.reader = FakeReader([])
        for i in range(10):
            self.d.check(self.img, i * 0.1, CFG)
        self.assertEqual(self.d.reader.calls, 1)

    def test_idle_reads_capped_at_scans_per_second(self):
        self.d.reader = FakeReader([])
        for i in range(30):  # 3 s of a moving picture, 10 looks a second
            img = np.full((385, 462, 3), (i * 37) % 200, np.uint8)
            self.d.check(img, i * 0.1, CFG)
        self.assertLessEqual(self.d.reader.calls, 3 * CFG["scans_per_second"] + 1)

    LINES = [("+600xP", (179, 167, 296, 198)), ("CHEST", (271, 198, 330, 224))]  # real, Nov 19

    def popup_reader(self):
        """Reads the pop-up wherever the crop is (lines are relative to the crop)."""
        def lines(img):
            off = self.offset
            return [(t, (b[0] - off[0], b[1] - off[1], b[2] - off[0], b[3] - off[1])) for t, b in self.LINES]
        self.offset = (0, 0)
        return FakeReader(lines)

    def test_fast_reads_in_a_small_area_while_popup_up(self):
        self.d.reader = self.popup_reader()
        _, added, _ = self.d.check(self.img, 0.0, CFG)
        self.assertEqual(added, 1)
        crop, self.offset = self.d._crop_around(self.img, self.d.focus)
        self.assertLess(crop.shape[0] * crop.shape[1], 0.3 * self.img.shape[0] * self.img.shape[1])
        calls = self.d.reader.calls
        for i in range(1, 11):  # the pop-up animates: a different picture every look
            img = np.full_like(self.img, 50 + (i * 40) % 200)
            self.d.check(img, i * 0.1, CFG)
        self.assertGreaterEqual(self.d.reader.calls - calls, 5)  # 5 reads a second, not 3
        self.assertTrue(all(s == crop.shape[:2] for s in self.d.reader.sizes[1:]))

    def test_spot_read_while_no_popup(self):
        """Once a pop-up showed, the small spot is read 5x a second; the whole area every 3 s."""
        self.d.reader = self.popup_reader()
        self.d.check(self.img, 0.0, CFG)  # a pop-up: its spot is learned
        self.assertEqual(self.d.home, (179, 167, 330, 224))
        self.d.reader = FakeReader([])
        for i in range(60):  # from 10 s on (pop-up long gone): 6 s of a moving picture with white in it
            self.d.check(self.moving(i, white=True), 10 + i * 0.1, CFG)
        sizes = self.d.reader.sizes
        whole = [s for s in sizes if s == self.img.shape[:2]]
        self.assertGreaterEqual(len(sizes), 28)  # ~5 a second
        self.assertIn(len(whole), (2, 3))        # every 3 s
        spot = [s for s in sizes if s != self.img.shape[:2]]
        self.assertLess(spot[0][0] * spot[0][1], 0.3 * self.img.shape[0] * self.img.shape[1])

    def test_spot_from_the_learned_area_before_any_popup(self):
        # scan_region was learned with the pop-up in its middle: read there from the start
        cfg = dict(CFG, scan_region=[0.54, 0.13, 0.72, 0.5])
        self.d.reader = FakeReader([])
        for i in range(20):
            self.d.check(self.moving(i, white=True), i * 0.1, cfg)
        small = [s for s in self.d.reader.sizes if s != self.img.shape[:2]]
        self.assertGreaterEqual(len(small), 7)

    def test_no_white_no_read(self):
        """Waiting for a pop-up and nothing white in the spot: not read (most looks in a match)."""
        self.d.reader = self.popup_reader()
        self.d.check(self.img, 0.0, CFG)
        self.d.reader = FakeReader([])
        for i in range(30):  # 3 s of a moving picture, no white
            self.d.check(self.moving(i, white=False), 10 + i * 0.1, CFG)
        sizes = self.d.reader.sizes
        self.assertTrue(all(s == self.img.shape[:2] for s in sizes))  # only the whole-area safety read
        self.assertLessEqual(len(sizes), 1)
        # ...and the white check itself, on a real pop-up's colours
        spot = np.full((150, 280, 3), (60, 110, 160), np.uint8)
        self.assertFalse(ct.Detector.has_white_text(spot))
        spot[45:75, 50:170] = (250, 252, 255)  # "+600 XP"
        self.assertTrue(ct.Detector.has_white_text(spot))

    def moving(self, i, white):
        img = np.full_like(self.img, 50 + (i * 40) % 160)
        if white:
            img[170:200, 180:300] = 245
        return img


class FrameRecorderTests(unittest.TestCase):
    def test_saves_frames_around_a_popup(self):
        root = Path(tempfile.mkdtemp())
        r = ct.FrameRecorder(root, max_mb=50)
        img = np.zeros((40, 60, 3), np.uint8)
        for i in range(60):  # 6 s at 10 a second, pop-up up from 2.0 to 2.5 s
            t = i * 0.1
            r.add(t, img, {"t": t}, keep=2.0 <= t <= 2.5)
        deadline = time.time() + 5
        while not r.q.empty() and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)
        (d,) = list(root.iterdir())
        recs = [json.loads(x) for x in (d / "frames.jsonl").read_text().splitlines()]
        ts = [x["t"] for x in recs]
        self.assertAlmostEqual(min(ts), 0.5, delta=0.11)  # PRE 1.5 s before
        self.assertAlmostEqual(max(ts), 4.6, delta=0.11)  # POST 2 s after
        self.assertTrue(all((d / x["file"]).exists() for x in recs))


if __name__ == "__main__":
    unittest.main()
