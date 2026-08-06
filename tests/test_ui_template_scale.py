"""Regression tests for UI-template matching across screen widths.

Three defects made the bot tap the wrong coordinates on a 1350x1080 panel:

1. The scale ladder was derived from a single hard-coded capture width and
   sampled it too coarsely. The correlation peak is sharp — a button that
   scores 0.94 at the true scale drops to 0.57 six percent away — so the
   real button was never matched. When the manifest records the width the
   template was captured on, the exact ratio is known and no guessing is
   needed.

2. Shrinking an already-native template turns it into a small coloured blob
   that cross-matches unrelated buttons, so a template captured on the
   current device must be searched at its own size only.

3. ``detect_state`` judged the "Available Loot" panel at 0.35 and the attack
   button at 0.42. Both are inside the cross-match noise floor, so screens
   that contain neither element were reported as IN_BATTLE / HOME and the
   caller tapped coordinates belonging to a different screen.
"""

import unittest

try:
    import cv2
    import numpy as np
    _HAVE_CV2 = True
except ImportError:  # pragma: no cover - depends on the environment
    _HAVE_CV2 = False


def _button(width: int, height: int) -> "np.ndarray":
    """A button-like patch: distinct border, bright bar, dark glyph block."""
    img = np.full((height, width, 3), 40, dtype=np.uint8)
    cv2.rectangle(img, (0, 0), (width - 1, height - 1), (200, 210, 190), 3)
    cv2.rectangle(img, (4, 4), (width - 5, height // 2), (150, 200, 120), -1)
    cv2.rectangle(
        img, (width // 5, height // 3), (4 * width // 5, 2 * height // 3),
        (30, 30, 30), -1,
    )
    return img


def _scene(width: int, height: int, patch: "np.ndarray", at: tuple[int, int]):
    """Textured background with ``patch`` pasted at ``at`` (top-left)."""
    rng = np.random.default_rng(7)
    scene = rng.integers(60, 120, size=(height, width, 3), dtype=np.uint8)
    x, y = at
    scene[y:y + patch.shape[0], x:x + patch.shape[1]] = patch
    return scene


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class UiTemplateScaleTest(unittest.TestCase):
    def setUp(self):
        from vision.screen_reader import ScreenReader
        self.reader = ScreenReader()
        ScreenReader._ui_scale_hint = None

    def tearDown(self):
        from vision.screen_reader import ScreenReader
        ScreenReader._ui_scale_hint = None

    # ── 1. A recorded capture width pins the exact scale ──────────
    def test_recorded_capture_width_finds_a_downscaled_button(self):
        """A 2400-wide capture must be found on a 1350-wide screen.

        1350/2400 = 0.5625 — nowhere near the 1350/2340 = 0.577 guess, and
        far enough off that the old ladder scored the button below any
        usable threshold.
        """
        template = _button(240, 80)
        scale = 1350 / 2400.0
        shown = cv2.resize(
            template,
            (int(240 * scale), int(80 * scale)),
            interpolation=cv2.INTER_AREA,
        )
        scene = _scene(1350, 1080, shown, (700, 400))
        expected = (700 + shown.shape[1] // 2, 400 + shown.shape[0] // 2)

        hit = self.reader._match_ui(scene, template, 0.80, src_width=2400)
        self.assertIsNotNone(hit, "button must be found at its recorded scale")
        self.assertAlmostEqual(expected[0], hit[0], delta=4)
        self.assertAlmostEqual(expected[1], hit[1], delta=4)

    def test_native_template_matches_at_its_own_size(self):
        template = _button(240, 80)
        scene = _scene(1350, 1080, template, (300, 200))

        hit = self.reader._match_ui(scene, template, 0.80, src_width=1350)
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(300 + 120, hit[0], delta=4)
        self.assertAlmostEqual(200 + 40, hit[1], delta=4)

    # ── 2. Native templates are not hunted at shrunken sizes ──────
    def test_native_template_is_not_searched_shrunken(self):
        """No hit when the button is absent, even if a smaller lookalike is.

        Searching a native template at ~0.58x used to find the smaller
        decoy and report a confident match on the wrong element.
        """
        template = _button(240, 80)
        decoy = cv2.resize(template, (138, 46), interpolation=cv2.INTER_AREA)
        scene = _scene(1350, 1080, decoy, (900, 700))

        self.assertIsNone(
            self.reader._match_ui(scene, template, 0.80, src_width=1350),
            "a 0.58x lookalike must not satisfy a native-scale template",
        )

    # ── 3. State detection must not run on noise-floor thresholds ──
    def test_detect_state_uses_the_configured_ui_threshold(self):
        """No template is judged at a threshold inside the noise floor.

        Measured on real 1350x1080 captures, absent UI elements still score
        up to 0.70 against the wrong screen, so any explicit threshold in
        detect_state below that reports a state the screen is not in.
        """
        import inspect
        import re
        from vision.screen_reader import ScreenReader

        source = inspect.getsource(ScreenReader.detect_state)
        loose = [
            float(m) for m in re.findall(r"f\(screenshot,\s*\"[^\"]+\",\s*([0-9.]+)\)", source)
        ]
        self.assertTrue(
            all(value >= 0.70 for value in loose),
            f"detect_state still trusts a noise-floor threshold: {loose}",
        )


if __name__ == "__main__":
    unittest.main()
