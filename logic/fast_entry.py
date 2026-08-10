"""Blind-tap the Attack → Find a Match → Attack! chain.

Why this exists
---------------
Those three buttons always sit in the same place and always follow each
other, yet the normal path pays full vision for each one. Measured on the
target device: ``screencap`` 999ms, ``detect_state`` 1847ms, plus the
action chain's own ``scan_for_confirmations`` 595ms and a 1s settle — about
4.4 s per button, ~13 s to start one attack.

Tapping the known coordinates instead costs only the taps and the settles
the game itself needs.

The trade-off is real: nothing is verified, so an ad, a "your army is not
ready" prompt, or a Ranked tab left selected will swallow a tap and the
sequence lands somewhere unintended. The engine's next tick re-reads the
screen and recovers, but the attempt is wasted. That is why this is opt-in
and why it refuses to run off its calibrated resolution.
"""

from __future__ import annotations

import time
from typing import Callable

from core.adb_handler import get_active_resolution, tap
from core.logger import BotLogger
from core.settings import Settings

log = BotLogger.get("fast_entry")

# The screen these coordinates were measured on. They are raw pixels, so
# they mean nothing on any other screen — see `is_available`.
#
# The device reports this as ``Override size: 1080x1350`` (portrait
# notation) while CoC runs landscape and ``screencap`` returns 1350x1080.
# Same panel, two ways of writing it, so the check below compares the two
# dimensions regardless of which order they arrive in.
CALIBRATED_W, CALIBRATED_H = 1350, 1080
CALIBRATED_DIMS = frozenset((CALIBRATED_W, CALIBRATED_H))


def calibrated_label() -> str:
    """How the device itself reports this screen, for the UI."""
    return f"{min(CALIBRATED_DIMS)}x{max(CALIBRATED_DIMS)}"

# (x, y, seconds to wait for the next screen).
#
# Each point is the CENTRE of its button, measured by matching the button's
# own template against a screenshot of the live screen. Centre matters more
# than it looks: these taps are never verified, so a point near an edge is
# one layout shift away from landing on the neighbour.
#
#   step  button                     box on a 1350x1080 screen   centre
#   1     Attack!      (home)        x 22..175   y 1008..1053    (98, 1030)
#   2     Find a Match (multiplayer) x 88..427   y 690..800      (258, 744)
#   3     Attack!      (army panel)  x 1024..1259 y 768..845     (1141, 806)
#
# Step 3 used to be (1093, 777) — 9 px below the top edge of the button and
# only 7 px under the gem-count button that sits right above it. That is the
# worst place on this screen to be off by a few pixels: a miss there opens
# the gem purchase dialog instead of starting the attack.
#
# The waits are the panel-open animations, not think-time — they only need
# to outlast the slide-in. 0.5 s is deliberately tight: if a panel is still
# animating the tap lands on nothing and the whole chain misses, so this is
# the first thing to raise again if attacks stop starting.
#
# The last wait is the safest of the three to cut. Nothing follows it inside
# this module; it only delays handing control back, and the engine's next
# tick re-reads the screen properly either way.
STEPS: tuple[tuple[int, int, float], ...] = (
    (98, 1030, 0.5),    # Attack!            (home village)
    (258, 744, 0.5),    # Find a Match       (multiplayer panel)
    (1141, 806, 0.5),   # Attack!            (army panel)
)


def is_available() -> bool:
    """True when fast entry is enabled AND the screen matches calibration."""
    if not bool(Settings().get("hv_fast_entry", False)):
        return False
    width, height = get_active_resolution()
    if {width, height} != CALIBRATED_DIMS:
        log.warning(
            "Fast entry is on but the screen is %dx%d, not the calibrated "
            "%s — using normal detection instead.",
            width, height, calibrated_label(),
        )
        return False
    return True


def run(interrupted: Callable[[], bool] | None = None) -> bool:
    """Tap the three buttons in order. Returns False if it was cut short."""
    for index, (x, y, settle) in enumerate(STEPS, start=1):
        if interrupted is not None and interrupted():
            log.info("Fast entry interrupted after %d/%d taps.", index - 1, len(STEPS))
            return False
        log.info("Fast entry %d/%d → tap (%d,%d)", index, len(STEPS), x, y)
        tap(x, y)
        time.sleep(settle)
    return True
