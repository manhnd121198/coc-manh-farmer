"""CardStateSkill — is there anything left on this troop card?

CoC greys a card out the moment its last unit walks off it: the artwork
loses its colour and dims, exactly the way a dead hero's portrait does.
That is the whole signal used here, borrowed from
``HomeVillageLogic._is_hero_dead`` — measure mean saturation and mean
brightness over the middle of the card and call it empty when BOTH have
fallen.

Why not read the "x36" count instead: those digits are a fraction of the
size of the loot bar's, sit over busy artwork, and would cost an OCR pass
per card per check. Colour answers the only question the sweep-up asks —
*is there anything left* — for the price of a crop and two means.

Why BOTH channels have to drop: a card that is merely *selected* is
drawn brighter, and one sitting in shadow is darker but still colourful.
Either single test alone flips on those; together they only fire on the
grey-and-dim combination that means empty.

The thresholds are per-device by nature (panel, emulator gamma, in-game
brightness), so every check logs what it measured. Watch one attack with
the log open and you have your numbers.
"""

from __future__ import annotations

import cv2
import numpy as np

from core.logger import BotLogger

log = BotLogger.get("v2.card_state")

# Same starting point as the hero sensor, which was tuned on a real device.
DEFAULT_SATURATION = 60.0
DEFAULT_BRIGHTNESS = 140.0
DEFAULT_SAMPLE_PX = 22


class CardStateSkill:
    name = "card_state"

    @staticmethod
    def _cfg(config: dict | None) -> dict:
        block = (config or {}).get("sweep_up", {}) or {}
        return {
            "saturation": float(block.get("empty_saturation", DEFAULT_SATURATION)),
            "brightness": float(block.get("empty_brightness", DEFAULT_BRIGHTNESS)),
            "sample_px": max(4, int(block.get("card_sample_px", DEFAULT_SAMPLE_PX))),
        }

    def measure(
        self, screenshot: np.ndarray, card_xy: tuple[int, int],
        config: dict | None = None,
    ) -> tuple[float, float] | None:
        """Mean (saturation, brightness) over the middle of the card."""
        cfg = self._cfg(config)
        half = cfg["sample_px"]
        h, w = screenshot.shape[:2]
        x, y = card_xy
        roi = screenshot[
            max(0, y - half):min(h, y + half),
            max(0, x - half):min(w, x + half),
        ]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        return float(np.mean(hsv[:, :, 1])), float(np.mean(hsv[:, :, 2]))

    def has_troops_left(
        self, screenshot: np.ndarray, card_xy: tuple[int, int],
        config: dict | None = None, label: str = "",
    ) -> bool:
        """True when the card still looks live.

        Unreadable crop → True. The sweep-up would rather press a card that
        turns out to be empty (the game ignores it) than walk away from one
        that still holds half an army.
        """
        cfg = self._cfg(config)
        measured = self.measure(screenshot, card_xy, config)
        if measured is None:
            log.warning("card '%s': crop out of frame — assuming it still has troops.", label)
            return True

        saturation, brightness = measured
        empty = saturation < cfg["saturation"] and brightness < cfg["brightness"]
        log.info(
            "card '%s' at (%d,%d): sat=%.0f (<%.0f?) val=%.0f (<%.0f?) → %s",
            label, card_xy[0], card_xy[1],
            saturation, cfg["saturation"], brightness, cfg["brightness"],
            "EMPTY" if empty else "still has troops",
        )
        return not empty
