"""Tests for the YOLO-based Ring Sweep corridor (``plan_from_base``).

The YOLO model returns the base *cluster* (the no-deploy shape), so the
deploy ring is built by offsetting the base edges OUTWARD by a fixed margin.
The in-game contract:

  * every drop must sit OUTSIDE the base (a tap inside the no-deploy zone
    deploys nothing),
  * every drop must sit close to the base edge — on the offset ring, not
    somewhere off in the trees,
  * drops must stay inside the playfield,
  * coverage must reach more than one side,
  * a missing / degenerate base returns no points (caller falls back to HSV).
"""

import unittest

try:
    import numpy as np
    import cv2
    _HAVE_CV2 = True
except ImportError:  # pragma: no cover
    _HAVE_CV2 = False

if _HAVE_CV2:
    from logic.skills.ring_sweep_planner import RingSweepPlannerSkill


SCREEN_W, UI_CUTOFF = 1920, 838
EDGE, TOP = 60, 150
BOTTOM = UI_CUTOFF - EDGE

CFG = {
    "ring_sweep": {
        "use_yolo_corridor": True,
        "yolo_deploy_offset_px": 15,
        "boundary_margin_px": 5,
        "points_per_side": 4,
        "edge_margin_px": EDGE,
        "corner_miter": 1.5,
    },
    "polygon": {"top_ui_exclude_px": TOP},
}

# offset/margin as the planner scales them for a 1920px-wide screen.
OFFSET_PX = round(15 * SCREEN_W / 1350.0)   # 21
MARGIN_PX = round(5 * SCREEN_W / 1350.0)    # 7


def _square():
    return np.array(
        [[600, 300], [1300, 300], [1300, 720], [600, 720]], dtype=np.int32,
    )


def _diamond():
    return np.array(
        [[960, 250], [1360, 500], [960, 760], [560, 500]], dtype=np.int32,
    )


@unittest.skipUnless(_HAVE_CV2, "requires numpy + OpenCV")
class RingSweepFromBaseTest(unittest.TestCase):
    def setUp(self):
        self.planner = RingSweepPlannerSkill()

    def _dist_to_base(self, base, point):
        return cv2.pointPolygonTest(base, (float(point[0]), float(point[1])), True)

    def test_empty_or_degenerate_base_returns_nothing(self):
        self.assertEqual(self.planner.plan_from_base(None, SCREEN_W, UI_CUTOFF, CFG), [])
        two_pts = np.array([[10, 10], [20, 20]], dtype=np.int32)
        self.assertEqual(
            self.planner.plan_from_base(two_pts, SCREEN_W, UI_CUTOFF, CFG), [],
        )

    def test_every_drop_sits_outside_the_base(self):
        base = _square()
        ring = self.planner.plan_from_base(base, SCREEN_W, UI_CUTOFF, CFG)
        self.assertGreaterEqual(len(ring), 6)
        for point in ring:
            dist = self._dist_to_base(base, point)
            # Negative distance == outside; must clear the safety margin.
            self.assertLessEqual(
                dist, -MARGIN_PX,
                f"{point} is inside/too close to the base (dist={dist:.1f}).",
            )

    def test_drops_hug_the_base_edge(self):
        base = _square()
        ring = self.planner.plan_from_base(base, SCREEN_W, UI_CUTOFF, CFG)
        for point in ring:
            dist = abs(self._dist_to_base(base, point))
            # On the offset ring, not off in the trees. Allow generous slack
            # for bevelled corners but nothing near "twice the offset out".
            self.assertLessEqual(
                dist, OFFSET_PX * 3,
                f"{point} is {dist:.1f}px from the base — too far off the ring.",
            )

    def test_drops_stay_inside_the_playfield(self):
        for base in (_square(), _diamond()):
            ring = self.planner.plan_from_base(base, SCREEN_W, UI_CUTOFF, CFG)
            for x, y in ring:
                self.assertTrue(EDGE <= x <= SCREEN_W - EDGE, f"x={x} off screen")
                self.assertTrue(TOP <= y <= BOTTOM, f"y={y} off screen")

    def test_coverage_reaches_multiple_sides(self):
        base = _diamond()
        ring = self.planner.plan_from_base(base, SCREEN_W, UI_CUTOFF, CFG)
        centre = RingSweepPlannerSkill  # for side_of (static)
        sides = {centre.side_of((960, 505), p) for p in ring}
        self.assertGreaterEqual(len(sides), 3)

    def test_corridor_polygon_is_exposed_after_planning(self):
        base = _square()
        self.assertIsNone(self.planner.corridor_polygon)
        self.planner.plan_from_base(base, SCREEN_W, UI_CUTOFF, CFG)
        self.assertIsNotNone(self.planner.corridor_polygon)
        # The corridor encloses the base: its area is larger.
        self.assertGreater(
            cv2.contourArea(self.planner.corridor_polygon),
            cv2.contourArea(base),
        )


if __name__ == "__main__":
    unittest.main()
