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
