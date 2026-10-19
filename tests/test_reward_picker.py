"""Tests for the three-card reward prompt.

The prompt appears as the attack passes 33%, 66% and 100% destruction, and
blocks the battlefield until answered. Three things must hold: the cards
are taken in the agreed order (last, last, first), one prompt counts once
however many frames the battle loop sees it in, and the taps land inside
the cards in BOTH layouts the game uses.
"""

import unittest
from unittest import mock

import numpy as np

from logic.reward_picker import (
    CARD_Y_FRACTION,
    PICK_ORDER,
    SLOT_X_FRACTIONS,
    RewardPicker,
)

SCREEN_W, SCREEN_H = 1350, 1080
SLOT_X = [int(f * SCREEN_W) for f in SLOT_X_FRACTIONS]
SLOT_Y = int(CARD_Y_FRACTION * SCREEN_H)

# The card boxes (x1, x2, y1, y2) measured off two real prompts on a
# 1350x1080 screen. The x columns are identical; the victory screen sits
# 34px lower because the result ribbon pushes the row down.
CARD_BOXES = {
    "mid-battle (33%)": [(165, 464, 340, 793), (530, 829, 340, 793),
                         (894, 1193, 340, 793)],
    "victory (100%)": [(165, 464, 374, 827), (530, 829, 374, 827),
                       (894, 1193, 374, 827)],
}


class _FakeReader:
    """Stands in for ScreenReader, reporting the prompt on demand."""

    def __init__(self):
        self.visible = False

    def find_template_by_name(self, screenshot, name, threshold=None):
        return (0, 0) if self.visible else None


class RewardPickerTest(unittest.TestCase):
    def setUp(self):
        self.reader = _FakeReader()
        self.picker = RewardPicker(self.reader)
        self.frame = np.zeros((SCREEN_H, SCREEN_W, 3), dtype=np.uint8)

    def _prompt(self, frames=1):
        """Show the prompt for `frames` consecutive ticks, then hide it."""
        self.reader.visible = True
        for _ in range(frames):
            self.picker.check(self.frame)
        self.reader.visible = False
        self.picker.check(self.frame)

    def test_order_is_last_last_first(self):
        with mock.patch("logic.reward_picker.tap") as tap:
            for _ in range(3):
                self._prompt()
        self.assertEqual(
            [(SLOT_X[2], SLOT_Y), (SLOT_X[2], SLOT_Y), (SLOT_X[0], SLOT_Y)],
            [call.args for call in tap.call_args_list],
            "first two prompts take the last card, the third takes the first",
        )

    def test_one_prompt_counts_once_however_long_it_lingers(self):
        """The panel stays up for a few frames after the tap. Counting those
        would spend the whole order on a single prompt."""
        with mock.patch("logic.reward_picker.tap") as tap:
            self._prompt(frames=3)
        self.assertEqual(1, self.picker.taken)
        self.assertEqual([(SLOT_X[2], SLOT_Y)], [c.args for c in tap.call_args_list])

    def test_a_stuck_panel_is_tapped_again_without_advancing(self):
        """A swallowed tap must not strand the attack behind the panel, but
        the retry has to reuse the same card or it eats the next choice."""
        from logic.reward_picker import RETRY_EVERY_TICKS

        with mock.patch("logic.reward_picker.tap") as tap:
            self._prompt(frames=RETRY_EVERY_TICKS)
        self.assertEqual(1, self.picker.taken, "a retry is not a new prompt")
        self.assertEqual(
            [(SLOT_X[2], SLOT_Y)] * 2, [c.args for c in tap.call_args_list],
            "the retry taps the same card",
        )

    def test_nothing_happens_when_the_prompt_is_absent(self):
        with mock.patch("logic.reward_picker.tap") as tap:
            for _ in range(5):
                self.assertFalse(self.picker.check(self.frame))
        tap.assert_not_called()
        self.assertEqual(0, self.picker.taken)

    def test_a_battle_cut_short_restarts_the_order(self):
        """Only a 100% finish brings up the third prompt, so most battles end
        part-way through the order. The next attack starts from the top."""
        with mock.patch("logic.reward_picker.tap") as tap:
            self._prompt()
            self.picker.reset()
            self._prompt()
        self.assertEqual(1, self.picker.taken)
        self.assertEqual(
            [(SLOT_X[2], SLOT_Y), (SLOT_X[2], SLOT_Y)],
            [c.args for c in tap.call_args_list],
            "the new battle takes the last card again, not the third entry",
        )

    def test_a_fourth_prompt_reuses_the_last_rule(self):
        """Milestones cap a battle at three, but an unanswered panel stalls
        the attack, so an unexpected extra is still taken."""
        with mock.patch("logic.reward_picker.tap") as tap:
            for _ in range(4):
                self._prompt()
        self.assertEqual(
            (SLOT_X[0], SLOT_Y), tap.call_args_list[-1].args,
            "the fourth falls back to the final entry of the order",
        )


class RewardCardGeometryTest(unittest.TestCase):
    """The taps are placed from screen proportions rather than from where
    the template matched, so those proportions have to hold for both of the
    layouts the game actually draws."""

    def test_every_slot_lands_inside_its_card_in_both_layouts(self):
        for layout, boxes in CARD_BOXES.items():
            for slot, (x1, x2, y1, y2) in enumerate(boxes):
                x = int(SLOT_X_FRACTIONS[slot] * SCREEN_W)
                y = int(CARD_Y_FRACTION * SCREEN_H)
                with self.subTest(layout=layout, card=slot + 1):
                    self.assertTrue(
                        x1 < x < x2 and y1 < y < y2,
                        f"{layout} card {slot + 1}: tap ({x}, {y}) is outside "
                        f"x {x1}..{x2} y {y1}..{y2}",
                    )

    def test_each_tap_clears_every_card_edge_by_a_wide_margin(self):
        """A tap near an edge is one layout nudge away from the neighbour,
        and the gap between cards is only 66px."""
        for layout, boxes in CARD_BOXES.items():
            for slot, (x1, x2, y1, y2) in enumerate(boxes):
                x = int(SLOT_X_FRACTIONS[slot] * SCREEN_W)
                y = int(CARD_Y_FRACTION * SCREEN_H)
                margin = min(x - x1, x2 - x, y - y1, y2 - y)
                with self.subTest(layout=layout, card=slot + 1):
                    self.assertGreater(margin, 100, f"only {margin}px of room")

    def test_slots_read_left_to_right(self):
        self.assertEqual(sorted(SLOT_X_FRACTIONS), list(SLOT_X_FRACTIONS))
        self.assertEqual(len(SLOT_X_FRACTIONS), 3)
        for slot in PICK_ORDER:
            self.assertIn(slot % len(SLOT_X_FRACTIONS), range(3))


if __name__ == "__main__":
    unittest.main(verbosity=2)
