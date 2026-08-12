"""Contract tests for the optional YOLO completeness guard."""

import unittest

try:
    import cv2  # noqa: F401
    import numpy as np
    _HAVE_CV2 = True
except ImportError:  # pragma: no cover
    _HAVE_CV2 = False


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class RedZoneYoloGuardTest(unittest.TestCase):
    def setUp(self):
        from vision.skills.red_zone_polygon import RedZonePolygonSkill
        self.skill = RedZonePolygonSkill
        self.cfg = {
            "yolo_guard_min_coverage": 0.85,
            "yolo_guard_tolerance_px": 0,
        }
        self.guard = np.array(
            [[300, 200], [900, 200], [900, 650], [300, 650]],
            dtype=np.int32,
        )

    def test_complete_redline_candidate_is_accepted(self):
        candidate = np.array(
            [[200, 120], [1000, 120], [1000, 730], [200, 730]],
            dtype=np.int32,
        )
        self.assertTrue(
            self.skill._candidate_covers_guard(
                candidate, self.guard, 1350, self.cfg,
            ),
        )

    def test_candidate_that_cuts_off_half_the_base_is_rejected(self):
        candidate = np.array(
            [[200, 120], [650, 120], [650, 730], [200, 730]],
            dtype=np.int32,
        )
        self.assertFalse(
            self.skill._candidate_covers_guard(
                candidate, self.guard, 1350, self.cfg,
            ),
        )

    def test_missing_guard_preserves_the_hsv_fallback(self):
        candidate = np.array(
            [[200, 120], [650, 120], [650, 730], [200, 730]],
            dtype=np.int32,
        )
        self.assertTrue(
            self.skill._candidate_covers_guard(
                candidate, None, 1350, self.cfg,
            ),
        )


if __name__ == "__main__":
    unittest.main()
