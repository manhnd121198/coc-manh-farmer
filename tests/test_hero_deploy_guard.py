"""Tests for which shape a hero drop is measured against.

The bug this pins: Ring Sweep plans its drops on a ring built from the YOLO
base hull, but heroes were checked against the red-zone polygon — the hull
of every red pixel on the base, which reaches past the real boundary. A
ring point on open grass read as "inside the red zone" and every hero was
skipped. Whichever shape the troops were planned against is the one the
heroes have to be judged by.
"""

import unittest
from unittest import mock

try:
    import cv2  # noqa: F401
    import numpy as np
    _HAVE_CV2 = True
except ImportError:                                    # pragma: no cover
    _HAVE_CV2 = False


def _square(x1, y1, x2, y2):
    import numpy as np
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.int32)


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class DeployGuardTest(unittest.TestCase):
    """A small base hull inside a much larger red-pixel polygon — the real
    geometry measured off a capture, where the hull sat ~43px inside."""

    RED = _square(100, 100, 900, 900)                  # inflated red polygon
    HULL = _square(200, 200, 800, 800)                 # YOLO base hull
    ON_GRASS = (815, 500)                              # just outside the hull

    def _ctx(self, **over):
        from types import SimpleNamespace
        from vision.skills.red_zone_polygon import RedZonePolygonSkill
        base = dict(
            polygon=self.RED, deploy_guard=None, deploy_guard_margin_px=25,
            skills=SimpleNamespace(red_zone=RedZonePolygonSkill()),
        )
        base.update(over)
        return SimpleNamespace(**base)

    def _safe(self, ctx, point):
        from logic.rules.air_attack_rule import AirAttackRule
        return AirAttackRule._is_safe_deploy_point(ctx, point)

    def test_the_red_polygon_alone_rejects_a_point_on_grass(self):
        """Why heroes were being skipped."""
        self.assertFalse(self._safe(self._ctx(), self.ON_GRASS))

    def test_the_base_hull_accepts_that_same_point(self):
        ctx = self._ctx(deploy_guard=self.HULL, deploy_guard_margin_px=0)
        self.assertTrue(self._safe(ctx, self.ON_GRASS))

    def test_a_point_on_the_base_is_still_refused(self):
        """The guard must not become a rubber stamp."""
        ctx = self._ctx(deploy_guard=self.HULL, deploy_guard_margin_px=0)
        self.assertFalse(self._safe(ctx, (500, 500)))

    def test_without_a_guard_it_falls_back_to_the_red_polygon(self):
        """Air Attack sets no guard and must behave exactly as before."""
        self.assertTrue(self._safe(self._ctx(), (50, 50)))
        self.assertFalse(self._safe(self._ctx(), (500, 500)))


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class RingSweepPublishesItsGuardTest(unittest.TestCase):

    def test_the_yolo_branch_hands_the_hull_to_the_hero_check(self):
        from types import SimpleNamespace
        from logic.rules.ring_sweep_rule import RingSweepRule

        hull = _square(200, 200, 800, 800)
        ring = [(150, 500), (850, 500), (500, 150), (500, 850)]
        skills = SimpleNamespace(
            red_zone=mock.Mock(), ring=mock.Mock(), target=mock.Mock(),
            touch=mock.Mock(),
        )
        skills.red_zone.yolo_base_polygon.return_value = hull
        skills.red_zone.centroid.return_value = (500, 500)
        skills.ring.plan_from_base.return_value = ring
        skills.ring.sides_covered.return_value = {"left", "right"}
        skills.ring.pick_drops.return_value = ring[:1]
        skills.target.find_one.return_value = None      # stop after planning

        ctx = SimpleNamespace(
            config={"ring_sweep": {"use_yolo_corridor": True,
                                   "min_valid_points": 2}},
            skills=skills, screenshot=np.zeros((1080, 1350, 3), dtype=np.uint8),
            ui_cutoff=900, polygon=_square(100, 100, 900, 900),
            base_centroid=(500, 500), profile={"selected_troops": ["dragon"]},
            engine=None, deploy_guard=None, deploy_guard_margin_px=25,
            mode_key="hv",
        )

        RingSweepRule().execute(ctx)

        self.assertIs(hull, ctx.deploy_guard)
        self.assertEqual(0, ctx.deploy_guard_margin_px)


if __name__ == "__main__":
    unittest.main()
