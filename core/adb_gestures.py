"""
Human-like camera helpers for Smart Vision V2.

There is no pinch-zoom here, and there cannot be one over plain ADB. A
pinch needs two pointers down at the same time, and every single-pointer
tool the shell has refuses to supply them: ``input swipe`` fired from two
threads is two INDEPENDENT one-finger drags (CoC pans the camera instead
of zooming — measured on this device, the base spanned 868px before two
"pinches" and 867px after), and ``input motionevent`` carries one pointer
per event with no POINTER_DOWN.

The real multi-touch route — writing MT protocol B events straight to the
touchscreen node — is closed too: /dev/input/event9 is
``u:object_r:input_device:s0`` and SELinux is Enforcing, so ``sendevent``
from the shell user gets "Permission denied" even though it is in the
``input`` group. That needs root or an injected app running with
``UiAutomation``.
"""

import time

from core.adb_handler import swipe, get_active_resolution
from core.logger import BotLogger

log = BotLogger.get("gestures")


def pan_camera(
    direction: str,
    distance_px: int = 280,
    duration_ms: int = 550,
    center_x: int | None = None,
    center_y: int | None = None,
) -> None:
    """Pan the in-game camera with a single, slow, slightly noisy swipe.

    `direction` ∈ {"up","down","left","right"} — moves the VIEWPORT in
    that direction (i.e., contents move opposite). Slow + jitter so the
    motion looks human rather than instant teleportation.
    """
    scr_w, scr_h = get_active_resolution()
    cx = center_x if center_x is not None else scr_w // 2
    cy = center_y if center_y is not None else scr_h // 2
    d = max(100, distance_px)

    if direction == "up":
        sx, sy, ex, ey = cx, cy + d // 2, cx, cy - d // 2
    elif direction == "down":
        sx, sy, ex, ey = cx, cy - d // 2, cx, cy + d // 2
    elif direction == "left":
        sx, sy, ex, ey = cx + d // 2, cy, cx - d // 2, cy
    elif direction == "right":
        sx, sy, ex, ey = cx - d // 2, cy, cx + d // 2, cy
    else:
        return

    # Keep every endpoint on-screen: on narrow devices (e.g. 1350x1080) a
    # half-distance offset from centre can otherwise fall outside the panel.
    sx = max(0, min(sx, scr_w - 1)); ex = max(0, min(ex, scr_w - 1))
    sy = max(0, min(sy, scr_h - 1)); ey = max(0, min(ey, scr_h - 1))

    swipe(sx, sy, ex, ey, duration_ms=duration_ms)
    time.sleep(0.20)
