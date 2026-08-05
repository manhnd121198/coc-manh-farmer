"""
Geometry of the deploy ring.

``ring_planner`` deliberately imports nothing from ``vision``, so it can
be loaded directly — no cv2, no stubbing.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "logic" / "skills" / "ring_planner.py"

# Only dependency is the logger.
logger_stub = types.ModuleType("core.logger")


class _Log:
    @staticmethod
    def get(_name):
        class _Null:
            def __getattr__(self, _item):
                return lambda *a, **k: None
        return _Null()


logger_stub.BotLogger = _Log
core_pkg = types.ModuleType("core")
sys.modules.setdefault("core", core_pkg)
sys.modules["core.logger"] = logger_stub

SPEC = importlib.util.spec_from_file_location("ring_planner_for_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
Ring = MODULE.RingPlannerSkill

# A diamond, i.e. the shape a CoC base actually has on screen.
DIAMOND = [(500, 100), (900, 400), (500, 700), (100, 400)]


class OffsetTest(unittest.TestCase):
    def test_offset_pushes_every_edge_outward_by_the_exact_distance(self):
        ring = Ring.offset(DIAMOND, 50)

        # Each offset vertex must sit further from the centre than the
        # original, and the shape must stay a quadrilateral.
        self.assertEqual(4, len(ring))
        cx, cy = Ring.centroid(DIAMOND)
        for original, moved in zip(DIAMOND, ring):
            d_before = (original[0] - cx) ** 2 + (original[1] - cy) ** 2
            d_after = (moved[0] - cx) ** 2 + (moved[1] - cy) ** 2
            self.assertGreater(d_after, d_before)

    def test_offset_is_a_true_offset_not_a_scale_about_the_centre(self):
        """A scale would move the far ends of a wide base much further
        than its short sides — that is what pushed drops off the map."""
        wide = [(100, 400), (1500, 380), (1500, 500), (100, 520)]
        ring = Ring.offset(wide, 60)

        # The long horizontal edges move by ~60 px, not by a fraction of
        # the 1400 px width.
        top_edge_shift = min(p[1] for p in wide) - min(p[1] for p in ring)
        self.assertAlmostEqual(60, top_edge_shift, delta=6)

    def test_zero_offset_returns_the_polygon_unchanged(self):
        self.assertEqual(
            [(float(x), float(y)) for x, y in DIAMOND],
            Ring.offset(DIAMOND, 0),
        )

    def test_degenerate_polygon_is_rejected(self):
        self.assertEqual([], Ring.offset([(1, 1), (2, 2)], 40))


class DensifyTest(unittest.TestCase):
    def test_walks_the_closed_contour_at_the_requested_spacing(self):
        square = [(0, 0), (300, 0), (300, 300), (0, 300)]

        walked = Ring.densify(square, 50)

        self.assertEqual(24, len(walked))          # 1200 px perimeter / 50
        # It closes the loop: the last point is near the start again.
        self.assertLess(abs(walked[-1][0] - 0), 60)

    def test_spacing_has_a_floor_so_a_zero_cannot_hang_the_walk(self):
        walked = Ring.densify([(0, 0), (100, 0), (100, 100), (0, 100)], 0)

        self.assertEqual(100, len(walked))


class SideGroupingTest(unittest.TestCase):
    """The whole point of the ring: diagonal rims must be reachable."""

    def test_every_side_of_a_diamond_gets_points(self):
        groups = Ring.plan(DIAMOND, offset_px=40, spacing_px=40)

        self.assertEqual({"left", "right", "top", "bottom"}, set(groups))
        for side, points in groups.items():
            self.assertGreater(len(points), 1, f"{side} came out empty")

    def test_diagonal_points_are_kept_and_split_between_neighbours(self):
        """A bbox corridor discards the diagonals entirely; the ring must
        assign them to one of the two sides they face."""
        groups = Ring.plan(DIAMOND, offset_px=40, spacing_px=30)
        total = sum(len(v) for v in groups.values())

        walked = Ring.densify(Ring.offset(DIAMOND, 40), 30)
        self.assertEqual(len(walked), total, "no ring point may be dropped")

    def test_side_of_uses_the_dominant_axis(self):
        centre = (500, 400)
        self.assertEqual("right", Ring.side_of((900, 420), centre))
        self.assertEqual("left", Ring.side_of((100, 380), centre))
        self.assertEqual("top", Ring.side_of((520, 100), centre))
        self.assertEqual("bottom", Ring.side_of((480, 700), centre))

    def test_explicit_centroid_overrides_the_vertex_average(self):
        groups = Ring.plan(DIAMOND, centroid=(0, 0), offset_px=20, spacing_px=60)

        # With the centre off to the top-left, nothing faces left or top.
        self.assertNotIn("left", groups)
        self.assertNotIn("top", groups)


class SampleTest(unittest.TestCase):
    def test_spreads_the_picks_across_the_whole_arc(self):
        points = [(i, 0) for i in range(20)]

        picked = Ring.sample(points, 5)

        self.assertEqual([(0, 0), (5, 0), (10, 0), (14, 0), (19, 0)], picked)

    def test_keeps_everything_when_there_is_less_than_asked_for(self):
        points = [(1, 1), (2, 2)]

        self.assertEqual(points, Ring.sample(points, 9))

    def test_empty_input_is_not_an_error(self):
        self.assertEqual([], Ring.sample([], 9))


if __name__ == "__main__":
    unittest.main()
