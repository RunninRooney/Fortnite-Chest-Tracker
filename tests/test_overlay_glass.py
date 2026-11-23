"""The in-game overlay's glass look (overlay_glass.py): a drop-in for chest_tracker.Painter.

Run:  python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import app_theme  # noqa: E402
import chest_tracker as ct  # noqa: E402
import overlay_glass as OG  # noqa: E402
_isolate.check(ct)

PARTY = [{"name": "Robin", "game": 23, "total": 5057, "online": True, "me": True},
         {"name": "Falcon", "game": 31, "total": 4198, "online": True, "me": False},
         {"name": "a very long console name (PS)", "game": 0, "total": 473, "online": False, "me": False}]
STATES = [dict(mine=23, rows=[("THIS GAME", 23), ("TOTAL", 5057)]),
          dict(mine=23, rows=[("TEAM GAME", 66), ("MY TOTAL", 5057), ("TEAM TOTAL", 11407)]),
          dict(mine=24, rows=[("THIS GAME", 24), ("TOTAL", 5058)], flash=1.0),
          dict(mine=70, rows=[("THIS GAME", 70), ("TOTAL", 5034)], confirmed=True, final=True),
          dict(mine=70, rows=[], message="Squad out! You: 70 chests. Team: 93. Checking the replay for broken chests"),
          dict(mine=23, rows=[("TEAM GAME", 66), ("MY TOTAL", 5057), ("TEAM TOTAL", 11407)], party=PARTY),
          dict(mine=0, rows=[("THIS GAME", 0), ("TOTAL", 0)], highlight=True)]


class GlassPainterTests(unittest.TestCase):
    def tearDown(self):
        app_theme.use_theme("arctic")

    def test_every_state_in_every_theme(self):
        for theme in app_theme.THEME_ORDER:
            p = OG.GlassPainter(1.0, theme)
            for kw in STATES:
                for opacity in (0.0, 0.18, 0.85, 1.0):
                    img = p.draw(opacity=opacity, **kw)
                    self.assertEqual(img.mode, "RGBA")
                    self.assertEqual(img.size, (p.W, p.H + p.party_height(len(kw.get("party") or []))))

    def test_every_size(self):
        for scale in (0.6, 1.0, 1.17, 2.5):
            p = OG.GlassPainter(scale)
            for kw in STATES:
                self.assertEqual(p.draw(**kw).size[0], p.W)

    def test_always_clickable_and_text_stays_solid(self):
        p = OG.GlassPainter(1.0)
        img = p.draw(mine=23, rows=[("TOTAL", 5057)], opacity=0.0)
        a = img.getchannel("A")
        self.assertGreater(a.getpixel((p.W // 2, p.H - 6)), 0)  # never fully see-through: it can be clicked
        self.assertEqual(a.getextrema()[1], 255)  # the numbers are drawn solid

    def test_same_shape_rules_as_the_classic_painter(self):
        g, c = OG.GlassPainter(1.0), ct.Painter(1.0)
        self.assertEqual((g.party_height(0), c.party_height(0)), (0, 0))
        self.assertLess(abs(g.H - c.H), 10)  # about the same size on screen
        self.assertGreater(g.party_height(3), g.party_height(1))


class MatchTimerTests(unittest.TestCase):
    @staticmethod
    def reds(img):
        """Pixels that are clearly the timer's red (no theme draws text this red)."""
        b = img.tobytes()
        return sum(1 for i in range(0, len(b), 4) if b[i + 3] > 200 and b[i] > 220 and b[i + 1] < 110 and b[i + 2] < 110)

    def test_tiny_timer_goes_red_at_14_minutes(self):
        for p in (OG.GlassPainter(1.0, "arctic"), ct.Painter(1.0)):
            base = dict(mine=23, rows=[("THIS GAME", 23), ("TOTAL", 5057)], opacity=0.85)
            calm = p.draw(timer="13:59", **base)
            late = p.draw(timer="14:00", late=True, **base)
            self.assertEqual(calm.size, p.draw(**base).size)  # it fits in the overlay as it is
            self.assertNotEqual(calm.tobytes(), p.draw(**base).tobytes())  # drawn
            self.assertGreater(self.reds(late), self.reds(calm) + 20, type(p).__name__)

    def test_clock_from_joining_the_match(self):
        app = ct.App.__new__(ct.App)
        app.cfg = {}
        self.assertEqual(app.match_timer(), ("", False))  # between matches: nothing
        app._match_t0 = 1000.0
        self.assertEqual(app.match_timer(1000 + 65), ("1:05", False))
        self.assertEqual(app.match_timer(1000 + 839), ("13:59", False))
        self.assertEqual(app.match_timer(1000 + 840), ("14:00", True))
        self.assertEqual(app.match_timer(1000 + 50 * 60), ("", False))  # a stale start (missed "over")
        app.cfg["match_timer"] = False
        self.assertEqual(app.match_timer(1000 + 65), ("", False))


class MakePainterTests(unittest.TestCase):
    def tearDown(self):
        app_theme.use_theme("arctic")

    def test_glass_by_default_classic_on_request(self):
        self.assertIsInstance(ct.make_painter({}, 1.0), OG.GlassPainter)
        self.assertIsInstance(ct.make_painter({"overlay_style": "classic"}, 1.0), ct.Painter)

    def test_falls_back_to_classic_if_glass_breaks(self):
        with mock.patch.object(OG, "GlassPainter", side_effect=RuntimeError("no glass")):
            self.assertIsInstance(ct.make_painter({}, 1.0), ct.Painter)

    def test_uses_the_chosen_theme(self):
        p = ct.make_painter({"app_theme": "rose"}, 1.0)
        self.assertEqual(p.accent, app_theme.THEMES["rose"]["accent"])


if __name__ == "__main__":
    unittest.main()
