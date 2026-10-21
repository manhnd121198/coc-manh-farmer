"""Tests for the battle speed-up tap.

The game puts up a speed button once a minute is left on the battle clock,
and it gets exactly one tap. The button goes on showing 1x for a few frames
while it animates, and the battle loop sees every one of those, so the tap
is driven by the button appearing rather than by it being on screen.
"""

import unittest
from unittest import mock

import numpy as np

from core.state_machine import GameState
from logic.home_village import HomeVillageLogic


class _Reader:
    """Stands in for ScreenReader, showing the 1x button on demand."""

    def __init__(self):
        self.visible = False
        self.point = (1289, 714)

    def find_template_by_name(self, screenshot, name, threshold=None):
        if name == "battle_speed_1x":
            return self.point if self.visible else None
        return None


class BattleSpeedTest(unittest.TestCase):
    def setUp(self):
        self.reader = _Reader()
        self.logic = HomeVillageLogic(
            profile={}, sm=mock.Mock(), sr=self.reader, ocr=mock.Mock(),
        )
        self.frame = np.zeros((1080, 1350, 3), dtype=np.uint8)

    def test_taps_once_when_the_button_appears(self):
        self.reader.visible = True
        with mock.patch("logic.home_village.tap") as tap:
            self.assertTrue(self.logic._speed_up_battle(self.frame))
        tap.assert_called_once_with(1289, 714)

    def test_never_taps_twice_however_long_the_button_stays_up(self):
        """The button keeps showing 1x for a few frames after the tap, and a
        battle can leave it on screen for its whole last minute. Tapping any
        of those frames would push the speed past what was asked for."""
        self.reader.visible = True
        with mock.patch("logic.home_village.tap") as tap:
            for _ in range(60):
                self.logic._speed_up_battle(self.frame)
        tap.assert_called_once()

    def test_nothing_happens_while_the_button_is_absent(self):
        with mock.patch("logic.home_village.tap") as tap:
            for _ in range(5):
                self.assertFalse(self.logic._speed_up_battle(self.frame))
        tap.assert_not_called()

    def test_the_next_appearance_taps_again(self):
        """It is one tap per appearance, not one for all time."""
        with mock.patch("logic.home_village.tap") as tap:
            self.reader.visible = True
            self.logic._speed_up_battle(self.frame)
            self.reader.visible = False
            self.logic._speed_up_battle(self.frame)
            self.reader.visible = True
            self.logic._speed_up_battle(self.frame)
        self.assertEqual(2, tap.call_count)

    def test_the_battle_carries_on_in_the_same_tick(self):
        """The speed button covers nothing, so unlike the reward prompt it
        must not swallow the frame the attack would have used."""
        self.reader.visible = True
        self.logic._attack_active = True
        self.logic._battle_phase_done = False
        with mock.patch("logic.home_village.tap"), \
             mock.patch.object(self.logic, "_execute_full_attack") as attack:
            self.logic._handle_in_battle(self.frame)
        attack.assert_called_once()

    def test_a_new_battle_allows_a_fresh_tap(self):
        """Each attack starts back at 1x, so the count has to clear."""
        self.reader.visible = True
        with mock.patch("logic.home_village.tap") as tap:
            self.logic._speed_up_battle(self.frame)
            self.logic._count_attack()
            self.logic._speed_up_battle(self.frame)
        self.assertEqual(2, tap.call_count)

    def test_the_home_village_never_reaches_the_speed_check(self):
        """Only states that can be inside a battle are offered the frame."""
        self.reader.visible = True
        with mock.patch("logic.home_village.tap") as tap, \
             mock.patch.object(self.logic, "_handle_home"), \
             mock.patch.object(self.logic, "_log_own_storage"):
            self.logic.handle(self.frame, GameState.HOME)
        tap.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
