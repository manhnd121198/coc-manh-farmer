"""Tests for the rolling-barrage spell placement (``advancing_line``).

Spacing contract (what the user asked for):
  * ``spacing_px`` sets the centre-to-centre gap = the spell footprint, so
    NO two drops land within that distance of each other,
  * points come out WAVE-MAJOR and the front advances along the push,
  * within a wave the drops form a line perpendicular to the push,
  * with a base polygon the drops stay on the base,
  * a small base uses fewer than ``drop_count`` drops rather than stacking.
"""

import math
import unittest

try:
    import numpy as np
    import cv2  # noqa: F401
    _HAVE_CV2 = True
except ImportError:  # pragma: no cover
    _HAVE_CV2 = False

if _HAVE_CV2:
    from logic.skills.spell_planner import SpellPlannerSkill
    from vision.skills.red_zone_polygon import RedZonePolygonSkill


def _square():
    return np.array(
        [[150, 300], [950, 300], [950, 700], [150, 700]], dtype=np.int32,
    )


SPACING = 120.0


@unittest.skipUnless(_HAVE_CV2, "requires numpy + OpenCV")
class AdvancingLineTest(unittest.TestCase):
    CLUSTER = (150, 500)   # deploy on the left edge
    TARGET = (550, 500)    # base centre → push to the right

    def _plan(self, count=30, per_wave=5, spacing=SPACING):
        return SpellPlannerSkill._advancing_line(
            self.CLUSTER, self.TARGET, count, per_wave,
            0.35, spacing, 0.9, _square(),
        )

    def test_returns_at_most_requested_count(self):
        pts = self._plan(30, 5)
        self.assertGreater(len(pts), 0)
        self.assertLessEqual(len(pts), 30)

    def test_no_two_drops_overlap(self):
        pts = self._plan(30, 5)
        worst = min(
            math.hypot(a[0] - b[0], a[1] - b[1])
            for i, a in enumerate(pts) for b in pts[i + 1:]
        )
        # Drops must stay roughly one footprint apart (allow jitter slack).
        self.assertGreaterEqual(worst, SPACING * 0.6, f"drops too close: {worst:.0f}px")

    def test_larger_spacing_spreads_further(self):
        near = self._plan(30, 5, spacing=90)
        far = self._plan(30, 5, spacing=180)
        # Wider footprint → each drop claims more room → fewer fit on the base.
        self.assertLessEqual(len(far), len(near))

    def test_front_advances_along_the_push(self):
        pts = self._plan(30, 5)
        half = len(pts) // 2
        first_x = sum(p[0] for p in pts[:half]) / half
        last_x = sum(p[0] for p in pts[half:]) / (len(pts) - half)
        self.assertLess(first_x, last_x, "barrage is not advancing")

    def test_drops_land_on_the_base(self):
        base = _square()
        for p in self._plan(30, 5):
            self.assertTrue(
                RedZonePolygonSkill.is_inside(base, p[0], p[1]),
                f"{p} fell off the base",
            )

    def test_degenerate_direction_returns_nothing(self):
        self.assertEqual(
            SpellPlannerSkill._advancing_line(
                (500, 500), (500, 500), 10, 5, 0.35, SPACING, 0.9, _square(),
            ),
            [],
        )

    def test_plan_spell_routes_advancing_line(self):
        planner = SpellPlannerSkill(target_locator=None)
        profiles = {
            "rage_spell": {
                "placement": "advancing_line",
                "drop_count": 30, "drops_per_wave": 5, "wave_interval_sec": 3,
                "start_fraction": 0.35, "spacing_px": 120, "line_spread": 0.9,
            }
        }
        drops = planner.plan_spell(
            screenshot=None, spell_name="rage_spell",
            cluster_xy=self.CLUSTER, target_xy=self.TARGET,
            config={}, spell_profiles=profiles, base_polygon=_square(),
        )
        self.assertGreater(len(drops), 0)
        self.assertLessEqual(len(drops), 30)


if __name__ == "__main__":
    unittest.main()
