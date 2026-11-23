"""Chest pop-up matching, using text read from a real match (Nov 15, 2026, 2576x1048 window)."""
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

W, H = 2576, 1048
CFG = ct.DEFAULT_CONFIG


def px(text, box):
    x1, y1, x2, y2 = box
    return (text, (x1 * W, y1 * H, x2 * W, y2 * H))


class PopupTests(unittest.TestCase):
    def test_real_popup_found(self):
        # exactly as OCR read it in the match: "+600xP" with "CHEST" underneath, plus other HUD text
        lines = [px("CHEST", [0.6411, 0.3187, 0.6628, 0.3412]), px("+600xP", [0.6058, 0.2887, 0.6522, 0.3225]),
                 px("+75", [0.8881, 0.36, 0.9173, 0.3962]),
                 px("Cowjuice92eliminated FinlessShark(40) with anSMG", [0.0192, 0.55, 0.1689, 0.5712])]
        self.assertIsNotNone(ct.find_chest_popup(lines, CFG))

    def test_popup_is_inside_default_area(self):
        l, t, r, b = ct.DEFAULT_POPUP_AREA
        # Measured: real match at 2576x1048, old screenshots at 2000x843.
        boxes = [[0.6058, 0.2887, 0.6522, 0.3225], [0.6411, 0.3187, 0.6628, 0.3412],
                 [1225 / 2000, 240 / 843, 1346 / 2000, 288 / 843]]
        # Predicted for 1920x1080 (16:9): the HUD stays put relative to the screen centre and scales
        # with screen height, so the pop-up's offset from the centre carries over in pixels * 1080/1048.
        cx, cy, k = W / 2, H / 2, 1080 / H
        for x1, y1, x2, y2 in boxes[:2]:
            boxes.append([(960 + (x1 * W - cx) * k) / 1920, (540 + (y1 * H - cy) * k) / 1080,
                          (960 + (x2 * W - cx) * k) / 1920, (540 + (y2 * H - cy) * k) / 1080])
        # ...and if instead it stays at the same fraction of the screen, it's the measured boxes again.
        for x1, y1, x2, y2 in boxes:
            self.assertTrue(l < x1 and x2 < r and t < y1 and y2 < b, (x1, y1, x2, y2))

    def test_real_non_popups_ignored(self):
        cases = [
            # perk / augment descriptions seen in the match
            [px("Revealsnearby Chests", [0.0978, 0.2287, 0.1593, 0.2488]),
             px("Opening Chest grants increased pickaxe speed and", [0.0917, 0.26, 0.2268, 0.2812]),
             px("+75", [0.10, 0.29, 0.12, 0.31])],
            # lobby quests
            [px("Search Chests", [0.2, 0.2, 0.3, 0.22]), px("+25,000 XP", [0.2, 0.23, 0.26, 0.25])],
            [px("Open a chest 3/10", [0.2, 0.2, 0.3, 0.22]), px("+15,000 XP", [0.31, 0.2, 0.36, 0.22])],
            # other XP pop-ups
            [px("ELIMINATION", [0.62, 0.32, 0.66, 0.34]), px("+500XP", [0.61, 0.29, 0.65, 0.32])],
            [px("Ammo Box", [0.62, 0.32, 0.66, 0.34]), px("+100 XP", [0.61, 0.29, 0.65, 0.32])],
            # this chat on the other screen, which the old version read by mistake
            [px("lobby not opening the chest, also it wont let me click or interact", [0.1, 0.1, 0.5, 0.12]),
             px("+XP\".Checking the log and settings:", [0.1, 0.13, 0.3, 0.15])],
        ]
        for lines in cases:
            self.assertIsNone(ct.find_chest_popup(lines, CFG), lines[0][0])

    def test_one_line_popup_still_found(self):
        self.assertIsNotNone(ct.find_chest_popup([px("Chest +600 XP", [0.6, 0.3, 0.66, 0.33])], CFG))
        self.assertIsNotNone(ct.find_chest_popup([px("Rare Chest", [0.6, 0.32, 0.66, 0.34]),
                                                  px("+900 XP", [0.6, 0.29, 0.65, 0.32])], CFG))


class MatchInProgressTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_replays import FakeApp, write_replay

        self.write = write_replay
        self.d = Path(tempfile.mkdtemp())
        (self.d / "Demos").mkdir()
        self.app = FakeApp(self.d)
        self.w = ct.ReplayWatcher(self.app, demos=self.d / "Demos", exe=self.d / "x.exe",
                                  db=ct.ReplayDB(self.d / "r.json"))

    def check(self):
        self.w._mip_checked = -1e9
        return self.w.match_in_progress()

    def test_lobby_vs_match(self):
        self.assertTrue(self.check())  # no replays recorded at all: can't tell, don't block counting
        self.write(self.d / "Demos" / "a.replay", live=False, started=time.time() - 3000)
        self.assertFalse(self.check())  # lobby: last match finished
        self.write(self.d / "Demos" / "b.replay", live=True, started=time.time())
        self.assertTrue(self.check())  # match being recorded
        self.app.cfg["replays"] = False
        self.assertTrue(self.check())


if __name__ == "__main__":
    unittest.main()
