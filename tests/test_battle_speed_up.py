"""Tests for the post-deploy battle speed-up.

The button is tapped once, after everything is on the field, to spend the
remaining battle clock four times faster. It rides on the monitoring
tick's screenshot — the frame that tick already takes to see whether the
fight is over — so what is worth pinning is: it never takes a screenshot
of its own, a tick where the button has not drawn yet is retried on the
next one, and once it is tapped it is never matched again (at 4x the
template misses anyway, but a second tap would drop the fight back to
normal speed).
"""

import unittest
from unittest import mock

try:
    import cv2  # noqa: F401
    import numpy as np
    _HAVE_CV2 = True
except ImportError:                                    # pragma: no cover
    _HAVE_CV2 = False


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class SpeedUpTest(unittest.TestCase):

    def _logic(self, hit):
        """A HomeVillageLogic with nothing but the two collaborators the
        speed-up touches: the screen reader and the tap."""
        from logic.home_village import HomeVillageLogic

        logic = HomeVillageLogic.__new__(HomeVillageLogic)
        logic._sr = mock.Mock()
        logic._sr.find_template_by_name.return_value = hit
        logic._speed_up_done = False
        return logic

    def _shot(self):
        return np.zeros((1080, 1350, 3), dtype=np.uint8)

    def test_taps_the_button_when_it_is_on_screen(self):
        logic = self._logic((1200, 120))
        with mock.patch("logic.home_village.tap") as tap:
            logic._speed_up_battle(self._shot())
        tap.assert_called_once_with(1200, 120)
        self.assertTrue(logic._speed_up_done)

    def test_reads_the_tick_screenshot_and_takes_none_of_its_own(self):
        logic = self._logic((1200, 120))
        shot = self._shot()
        with mock.patch("logic.home_village.tap"), \
             mock.patch("logic.home_village.adb_screencap") as screencap:
            logic._speed_up_battle(shot)
        screencap.assert_not_called()
        self.assertIs(shot, logic._sr.find_template_by_name.call_args.args[0])

    def test_a_tick_without_the_button_is_retried_next_tick(self):
        logic = self._logic(None)
        with mock.patch("logic.home_village.tap") as tap:
            logic._speed_up_battle(self._shot())
        tap.assert_not_called()
        self.assertFalse(logic._speed_up_done)

    def test_never_taps_twice(self):
        """Every later tick of the same battle must leave the button alone
        — tapping the 4x face again returns the fight to normal speed."""
        logic = self._logic((1200, 120))
        with mock.patch("logic.home_village.tap") as tap:
            logic._speed_up_battle(self._shot())
            logic._speed_up_battle(self._shot())
            logic._speed_up_battle(self._shot())
        tap.assert_called_once()
        self.assertEqual(1, logic._sr.find_template_by_name.call_count)


if __name__ == "__main__":
    unittest.main()
