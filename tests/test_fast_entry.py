"""Tests for the blind-tap attack entry.

Fast entry trades verification for speed, so the two things that must hold
are that it stays OFF unless asked for, and that it refuses to fire on a
screen it was not measured on — its coordinates are raw pixels, so on any
other resolution they would land on whatever happens to be there.
"""

import unittest
from unittest import mock

from logic import fast_entry


class FastEntryAvailabilityTest(unittest.TestCase):
    def _available(self, enabled: bool, resolution: tuple[int, int]) -> bool:
        with mock.patch.object(fast_entry.Settings, "get",
                               lambda self, key, default=None: enabled), \
             mock.patch.object(fast_entry, "get_active_resolution",
                               lambda: resolution):
            return fast_entry.is_available()

    def test_off_by_default(self):
        self.assertFalse(self._available(False, (1350, 1080)))

    def test_on_at_the_calibrated_resolution(self):
        """Either notation counts — the device says 1080x1350, CoC runs
        landscape and screencap returns 1350x1080. Same panel."""
        for resolution in ((1350, 1080), (1080, 1350)):
            self.assertTrue(
                self._available(True, resolution),
                f"{resolution} is the calibrated screen, just written differently",
            )

    def test_refuses_any_other_resolution(self):
        for resolution in ((1920, 1080), (2340, 1080), (1350, 1200)):
            self.assertFalse(
                self._available(True, resolution),
                f"{resolution} is not calibrated — coordinates would be wrong",
            )


class FastEntryCoordinateTest(unittest.TestCase):
    """Each tap must sit well inside its button.

    The boxes below were measured by matching each button's own template
    against a screenshot of the live screen at 1350x1080. Nothing verifies
    a blind tap, so a point near an edge is one layout shift away from the
    neighbouring control — and on the army panel that neighbour is the gem
    counter, i.e. a purchase dialog instead of an attack.
    """

    # step index -> (x1, x2, y1, y2) of the button on a 1350x1080 screen
    BOXES = {
        0: (22, 175, 1008, 1053),      # Attack!      (home village)
        1: (88, 427, 690, 800),        # Find a Match (multiplayer panel)
        2: (1024, 1259, 768, 845),     # Attack!      (army panel)
    }

    # tap() jitters by up to Settings.deploy_jitter pixels (default 15), so
    # the nominal point has to clear every edge by at least that much.
    MARGIN = 15

    def test_every_step_is_inside_its_button(self):
        for index, (x, y, _settle) in enumerate(fast_entry.STEPS):
            x1, x2, y1, y2 = self.BOXES[index]
            with self.subTest(step=index + 1):
                self.assertTrue(
                    x1 + self.MARGIN <= x <= x2 - self.MARGIN
                    and y1 + self.MARGIN <= y <= y2 - self.MARGIN,
                    f"step {index + 1} taps ({x}, {y}), which is not at least "
                    f"{self.MARGIN}px inside x {x1}..{x2} y {y1}..{y2}",
                )

    def test_every_step_fits_the_calibrated_screen(self):
        width, height = max(fast_entry.CALIBRATED_DIMS), min(fast_entry.CALIBRATED_DIMS)
        for index, (x, y, _settle) in enumerate(fast_entry.STEPS):
            with self.subTest(step=index + 1):
                self.assertLess(x, width)
                self.assertLess(y, height)


class FastEntrySequenceTest(unittest.TestCase):
    def test_taps_every_step_in_order(self):
        with mock.patch.object(fast_entry, "tap") as tap, \
             mock.patch.object(fast_entry.time, "sleep"):
            self.assertTrue(fast_entry.run())
        self.assertEqual(
            [(x, y) for x, y, _settle in fast_entry.STEPS],
            [call.args for call in tap.call_args_list],
        )

    def test_stops_when_interrupted(self):
        calls = {"n": 0}

        def interrupted() -> bool:
            calls["n"] += 1
            return calls["n"] > 1          # allow the first tap only

        with mock.patch.object(fast_entry, "tap") as tap, \
             mock.patch.object(fast_entry.time, "sleep"):
            self.assertFalse(fast_entry.run(interrupted))
        self.assertEqual(1, tap.call_count)


if __name__ == "__main__":
    unittest.main()
