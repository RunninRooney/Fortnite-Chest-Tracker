"""Teammates' chests from the map data: credited chests + searches that aren't the same chest by location.

Run:  python -m unittest discover -s tests
Shaped like the reader's --map output for the Nov 18, 2026 20:10 match (Brave-Boots far from the
recorder: 39 searches, 12 chests credited to them when they came into view minutes later).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import chest_tracker as ct  # noqa: E402
_isolate.check(ct)


def search(t, x, y, who="Mate"):
    return {"t": t, "who": who, "source": "search", "x": x, "y": y}


def credited(t, cid, x=0, y=0, who="Mate", broken=False, source="opener"):
    # (an "opener" chest's x/y is where the teammate stood when the update arrived, not the chest)
    return {"t": t, "who": who, "id": cid, "source": source, "x": x, "y": y, "broken": broken}


def replay(chests, mates=("Mate",)):
    return {"opened": 50, "recorder_searches": 50, "map": {"chests": chests},
            "teammates": [{"name": n, "opened": 0, "broken": 0} for n in mates]}


class SquadCountTests(unittest.TestCase):
    def test_late_credited_chest_is_the_same_as_its_search(self):
        spots = {"C1": (1000.0, 2000.0)}
        r = replay([search(100, 1050, 2000), credited(470, "C1", x=90000, y=90000)])  # arrives 6 min later
        self.assertEqual(ct.squad_counts(r, spots), {"Mate": 1})

    def test_credited_chest_with_no_search_is_added(self):
        spots = {"C1": (1000.0, 2000.0), "C2": (50000.0, 50000.0)}
        r = replay([search(100, 1000, 2000), credited(470, "C1"), credited(470, "C2")])
        self.assertEqual(ct.squad_counts(r, spots), {"Mate": 2})  # C2 was never seen as a search

    def test_a_search_after_the_chest_is_not_it(self):
        spots = {"C1": (1000.0, 2000.0)}
        r = replay([credited(100, "C1"), search(300, 1000, 2000)])  # same spot, but searched later
        self.assertEqual(ct.squad_counts(r, spots), {"Mate": 2})

    def test_one_search_per_chest(self):
        spots = {"C1": (1000.0, 2000.0), "C2": (1100.0, 2000.0)}  # two chests side by side
        r = replay([search(100, 1050, 2000), credited(400, "C1"), credited(400, "C2")])
        self.assertEqual(ct.squad_counts(r, spots), {"Mate": 2})

    def test_unknown_spot_pairs_only_by_time(self):
        r = replay([search(100, 0, 0), credited(103, "X1"), credited(400, "X2")])
        self.assertEqual(ct.squad_counts(r, {}), {"Mate": 2})

    def test_searches_scaled_by_the_chest_share_and_broken_not_opened(self):
        r = replay([search(t, t * 1000, 0) for t in range(10)] + [credited(5, "B", broken=True)])
        r["opened"], r["recorder_searches"] = 45, 50  # 90% of the recorder's searches were chests
        self.assertEqual(ct.squad_counts(r, {}), {"Mate": 9})

    def test_spawned_chest_uses_its_own_position(self):
        r = replay([search(100, 500, 500), credited(400, "Buried@500,500", x=500, y=520, source="actor")])
        self.assertEqual(ct.squad_counts(r, {}), {"Mate": 1})

    def test_no_map_data(self):
        self.assertIsNone(ct.squad_counts({"teammates": [{"name": "Mate"}]}, {}))


if __name__ == "__main__":
    unittest.main()
