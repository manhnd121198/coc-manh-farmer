"""Regression tests for narrow-panel devices such as 1350x1080 (5:4).

Two things used to break on any screen narrower than the module defaults:

1. ``HumanTouchSkill._jitter`` clamped against ``DEFAULT_SCREEN_WIDTH``
   (2340) instead of the live resolution, so ~31% of the taps aimed near
   the right edge of a 1350px panel landed outside 0..1349 and were
   silently discarded by Android — troops simply never deployed.

2. ``min_polygon_width_px`` is an absolute pixel gate tuned at 1920 wide
   (500px ≈ 26%). Applied unchanged to 1350px it demanded 37% of the
   screen and rejected valid bases, pushing V2 into the legacy fallback.
"""

import unittest

try:
    import cv2  # noqa: F401
    import numpy as np
    _HAVE_CV2 = True
except ImportError:  # pragma: no cover - depends on the environment
    _HAVE_CV2 = False


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class NarrowResolutionTest(unittest.TestCase):
    def setUp(self):
        from core.adb_handler import set_active_resolution
        self._set_resolution = set_active_resolution

    def tearDown(self):
        self._set_resolution(1920, 1080)

    # ── 1. Taps must stay on-screen ───────────────────────────────
    def test_jitter_never_leaves_a_narrow_screen(self):
        from logic.skills.human_touch import HumanTouchSkill

        width, height = 1350, 1080
        self._set_resolution(width, height)
        touch = HumanTouchSkill()

        for _ in range(2000):
            x, y = touch._jitter(width - 5, height - 5, 12)
            self.assertLessEqual(x, width - 1)
            self.assertLessEqual(y, height - 1)
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)

    def test_zero_jitter_still_clamps_to_screen(self):
        from logic.skills.human_touch import HumanTouchSkill

        self._set_resolution(1350, 1080)
        x, y = HumanTouchSkill()._jitter(5000, 5000, 0)
        self.assertEqual((1349, 1079), (x, y))

    # ── 2. Polygon width gate must scale with the panel ───────────
    def test_min_polygon_width_scales_down_on_narrow_screens(self):
        from vision.skills.red_zone_polygon import RedZonePolygonSkill

        # A 360px-wide base: valid on a 1350 panel (27%), too small at 1920.
        verts = np.array(
            [[100, 200], [460, 200], [460, 700], [100, 700]], dtype=np.int32,
        )
        self.assertTrue(
            RedZonePolygonSkill._sanity_ok(verts, 1350, 838, {}, "hsv"),
            "360px base should pass on a 1350-wide screen",
        )
        self.assertFalse(
            RedZonePolygonSkill._sanity_ok(verts, 1920, 736, {}, "hsv"),
            "1920-wide screens must keep the original 500px gate",
        )

    def test_wide_screens_keep_the_configured_gate(self):
        from vision.skills.red_zone_polygon import RedZonePolygonSkill

        # 520px wide clears the 500px gate at 1920 and above.
        verts = np.array(
            [[100, 200], [620, 200], [620, 700], [100, 700]], dtype=np.int32,
        )
        for width, cutoff in ((1920, 736), (2340, 660)):
            self.assertTrue(
                RedZonePolygonSkill._sanity_ok(verts, width, cutoff, {}, "hsv"),
                f"520px base should pass at {width} wide",
            )


if __name__ == "__main__":
    unittest.main()
