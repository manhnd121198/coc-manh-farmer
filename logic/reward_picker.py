"""Answer the three-card reward prompt that interrupts a battle.

Why this exists
---------------
The game offers a row of three reward cards as the attack passes 33%, 66%
and 100% destruction, and waits. Nothing else on screen can be reached
until one is taken, so an attack that ignores the prompt stalls there.

Which card to take is a standing preference, not a judgement about what is
on offer: the first two prompts take the last card, the third takes the
first. Cards are chosen by position; their contents are never read.

Only a 100% finish brings up the third prompt, so a battle may well end
after one or two. The order is therefore keyed to how many prompts this
battle has already answered, and resets with every attack.

Finding it
----------
Almost everything about the panel changes between appearances. The rewards
differ every time; so does the base drawn behind the cards; the leading
card is sometimes framed in gold and sometimes not; and the "Chọn một phần
thưởng!" banner is 852x77 mid-battle but 655x60 on the victory screen,
where the result ribbon pushes it down. Measured against two real prompts,
a banner template taken from the first scored 0.432 on the third — a miss.

The card frame is the one piece that holds still: 299x453 in both, same
grey border, and its top edge carries no reward art. That strip scores
0.983 and 1.000 on the two prompts against a worst case of 0.777 across
the home village, attack menu, single-player map, battle and victory
screens, hence the threshold below.

Where the cards are
-------------------
The row is centred by the game, and both prompts put the cards at exactly
the same x: 165..464, 530..829 and 894..1193 on a 1350-wide screen. Only y
shifts, by 34px, between the mid-battle and victory layouts. So the taps
are placed from screen proportions rather than from wherever the template
happened to match — which also means it does not matter that all three
card borders look alike and the matcher could lock onto any of them.
"""

from __future__ import annotations

import numpy as np

from core.adb_handler import get_active_resolution, tap
from core.logger import BotLogger
from vision.screen_reader import ScreenReader

log = BotLogger.get("reward_picker")

TEMPLATE_NAME = "reward_prompt_card"

# Real prompts score 0.98-1.00 and the busiest other screen reaches 0.777,
# so this sits in the gap. The default UI threshold of 0.70 is below that
# noise floor and would see the prompt on the attack menu.
MATCH_THRESHOLD = 0.90

# Card centres as a fraction of screen width: 314, 679 and 1043 of 1350.
SLOT_X_FRACTIONS: tuple[float, ...] = (0.2326, 0.5030, 0.7726)

# Vertical centre, splitting the difference between the two layouts (566
# mid-battle, 600 on the victory screen, of 1080). The cards are 453 tall,
# so either way the tap lands more than 200px clear of both edges.
CARD_Y_FRACTION = 0.5398

# The card each prompt takes, as an index into SLOT_X_FRACTIONS: last,
# last, first. Destruction milestones cap a battle at three prompts; a
# fourth would reuse the final entry rather than leave the panel up, since
# an unanswered panel stalls the attack.
PICK_ORDER: tuple[int, ...] = (-1, -1, 0)

# A tap can be swallowed by the panel's own slide-in. If the panel is still
# up this many ticks later, the same card is tapped again. Re-tapping the
# same slot is safe: the count does not move, so a retry cannot eat the
# choice meant for the next prompt.
RETRY_EVERY_TICKS = 4


class RewardPicker:
    """Picks a reward card, counting prompts within one battle."""

    def __init__(self, screen_reader: ScreenReader):
        self._sr = screen_reader
        self.reset()

    def reset(self) -> None:
        """Begin a new battle. The order restarts for every attack."""
        self._taken = 0
        self._visible_ticks = 0
        self._slot = PICK_ORDER[0]

    @property
    def taken(self) -> int:
        """How many prompts this battle has already answered."""
        return self._taken

    def check(self, screenshot: np.ndarray) -> bool:
        """Look for the prompt and take a card. True if a tap was sent."""
        if self._sr.find_template_by_name(
            screenshot, TEMPLATE_NAME, MATCH_THRESHOLD,
        ) is None:
            self._visible_ticks = 0
            return False

        self._visible_ticks += 1
        first_frame = self._visible_ticks == 1
        if first_frame:
            self._slot = PICK_ORDER[min(self._taken, len(PICK_ORDER) - 1)]
            self._taken += 1
        elif self._visible_ticks % RETRY_EVERY_TICKS != 0:
            # The prompt we already answered; give that tap time to land.
            return False

        x, y = self._slot_point(screenshot)
        log.info(
            "Reward prompt %d this battle%s — taking card %d at (%d,%d).",
            self._taken, "" if first_frame else " (still up, re-tapping)",
            self._slot % len(SLOT_X_FRACTIONS) + 1, x, y,
        )
        tap(x, y)
        return True

    def _slot_point(self, screenshot: np.ndarray) -> tuple[int, int]:
        """Centre of the chosen card, in screen pixels.

        The screenshot is the authority on size; the cached ADB resolution
        is only a fallback for a caller that hands over something odd.
        """
        height, width = screenshot.shape[:2]
        if width <= 0 or height <= 0:
            width, height = get_active_resolution()
        return (
            int(SLOT_X_FRACTIONS[self._slot] * width),
            int(CARD_Y_FRACTION * height),
        )
