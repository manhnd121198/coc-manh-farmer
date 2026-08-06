"""
HumanTouchSkill — humanized actuators that respect CoC's touch grammar.

What actually deploys, measured on a live device (1350x1080, TH11):
    • tap                     — deploys exactly one troop.  ✔
    • hold in place for 2.5 s — mass-deploys (~17 troops).  ✔
    • hold ~1 s, THEN drag while still down — lays troops along the drag
      path (21 troops over 3 s, spread across the route).  ✔
    • ``input swipe`` of any length or duration — deploys NOTHING. It
      starts moving on the first frame, so the game never enters
      repeat-deploy and reads the gesture as a camera pan. Verified at
      200 px / 300 ms, 200 px / 150 ms and 300 px / 300 ms: the troop
      counter did not move once.

The deciding factor is the initial dwell, not speed or distance. Troop
count then follows how long the finger stays down (~7 per second), which
is why ``deploy_line`` drives an explicit DOWN / sleep / MOVE… / UP
sequence via ``input motionevent`` instead of a single swipe.

Taps also need breathing room: fired back to back, 2 of 10 were
swallowed, and 4 of 10 when issued from a single shell loop. ``tap``
settles between calls, which is what keeps every drop landing.

This skill exposes:
    tap(x, y)                       — single deploy / card select.
    long_press(x, y, dur_ms)        — mass deploy at one point.
    deploy_line(x1, y1, x2, y2)     — hold, then drag: deploy along a path.
    quick_swipe(x1, y1, x2, y2)     — camera pan ONLY. Never deploys.
    settle(min_ms, max_ms)          — randomized inter-action pause.

All primitives jitter coordinates ±tap_jitter_px and clamp to screen
bounds. Hold durations are randomized within ±15% to avoid
machine-perfect repetition.
"""

from __future__ import annotations

import random
import time
from typing import Sequence

from core.adb_handler import (
    _run as _adb_run,
    tap_raw as _adb_tap,
    DEFAULT_SCREEN_WIDTH,
    DEFAULT_SCREEN_HEIGHT,
)
from core.logger import BotLogger

log = BotLogger.get("v2.human_touch")


class HumanTouchSkill:
    name = "human_touch"

    def tap(self, x: int, y: int, config: dict | None = None) -> None:
        cfg = self._cfg(config)
        hx, hy = self._jitter(x, y, cfg["tap_jitter_px"])
        log.debug("v2.tap (%d,%d)→(%d,%d)", x, y, hx, hy)
        _adb_tap(hx, hy)
        self.settle(config)

    def long_press(
        self, x: int, y: int, dur_ms: int | None = None, config: dict | None = None,
        min_ms: int = 1500,
    ) -> None:
        """Hold one spot to mass-deploy (~7 troops per second held).

        ``min_ms`` is the floor that keeps a "dump everything here" press
        from degenerating into a tap. Callers that deliberately want a
        short, metered press — spreading one card over several spots —
        lower it.
        """
        cfg = self._cfg(config)
        hx, hy = self._jitter(x, y, cfg["tap_jitter_px"])
        base = int(dur_ms) if dur_ms is not None else int(cfg["long_press_ms"])
        dur = max(min_ms, int(base * random.uniform(0.92, 1.10)))
        ox = random.choice([-1, 1]) * random.randint(6, 10)
        oy = random.choice([-1, 1]) * random.randint(6, 10)
        max_w, max_h = self._screen_bounds()
        ex = max(0, min(hx + ox, max_w - 1))
        ey = max(0, min(hy + oy, max_h - 1))
        log.info("v2.long_press (%d,%d)→(%d,%d) dur=%dms", x, y, ex, ey, dur)
        _adb_run(["shell", "input", "swipe",
                  str(hx), str(hy), str(ex), str(ey), str(dur)])
        self.settle(config)

    def deploy_path(
        self, points: Sequence[tuple[int, int]],
        config: dict | None = None, steps_per_leg: int | None = None,
    ) -> float:
        """Hold on ``points[0]``, then drag through the rest. Returns seconds held.

        The finger must rest on the first point long enough for the game to
        begin its repeat-deploy (~1 s) — only then does moving it lay troops
        along the path. ``input swipe`` starts moving immediately, which is
        why it deploys nothing at all.

        The whole path is ONE press: troop count follows how long the finger
        stays down (~7/s on a TH11 barracks army), so re-pressing per leg
        would pay the ~1 s ramp again and again for nothing.
        """
        if not points:
            return 0.0
        cfg = self._cfg(config)
        hold_s = max(0, int(cfg["deploy_hold_ms"])) / 1000.0
        dwell_s = max(0, int(cfg["deploy_step_ms"])) / 1000.0
        legs = max(1, int(steps_per_leg if steps_per_leg is not None
                          else cfg["deploy_steps"]))
        jitter = cfg["tap_jitter_px"]

        route = [self._jitter(x, y, jitter) for x, y in points]
        started = time.time()
        _adb_run(["shell", "input", "motionevent", "DOWN",
                  str(route[0][0]), str(route[0][1])])
        try:
            # Wait for the repeat-deploy to kick in before moving at all.
            time.sleep(hold_s * random.uniform(0.9, 1.15))
            for (sx, sy), (ex, ey) in zip(route, route[1:]):
                for i in range(1, legs + 1):
                    t = i / float(legs)
                    _adb_run(["shell", "input", "motionevent", "MOVE",
                              str(int(round(sx + (ex - sx) * t))),
                              str(int(round(sy + (ey - sy) * t)))])
                    time.sleep(dwell_s * random.uniform(0.85, 1.15))
        finally:
            # A stuck DOWN leaves the game holding a phantom finger and every
            # later gesture is read as part of that drag, so release always.
            _adb_run(["shell", "input", "motionevent", "UP",
                      str(route[-1][0]), str(route[-1][1])])

        held = time.time() - started
        log.info(
            "v2.deploy_path %s→%s points=%d held=%.1fs",
            route[0], route[-1], len(route), held,
        )
        self.settle(config)
        return held

    def quick_swipe(
        self, x1: int, y1: int, x2: int, y2: int,
        dur_ms: int | None = None, config: dict | None = None,
    ) -> None:
        """Drag the camera. This does NOT deploy troops — use deploy_line."""
        cfg = self._cfg(config)
        sx, sy = self._jitter(x1, y1, cfg["tap_jitter_px"])
        ex, ey = self._jitter(x2, y2, cfg["tap_jitter_px"])
        base = int(dur_ms) if dur_ms is not None else int(cfg["quick_swipe_ms"])
        dur = max(120, int(base * random.uniform(0.85, 1.15)))
        log.debug("v2.quick_swipe (%d,%d)→(%d,%d) dur=%dms", sx, sy, ex, ey, dur)
        _adb_run(["shell", "input", "swipe",
                  str(sx), str(sy), str(ex), str(ey), str(dur)])
        self.settle(config)

    def double_tap(
        self, x: int, y: int, gap_ms: int = 120, config: dict | None = None,
    ) -> None:
        self.tap(x, y, config)
        time.sleep(max(0.05, gap_ms / 1000.0))
        self.tap(x, y, config)

    def settle(self, config: dict | None = None) -> None:
        cfg = self._cfg(config)
        lo = max(0, int(cfg["inter_action_min_ms"])) / 1000.0
        hi = max(lo, int(cfg["inter_action_max_ms"])) / 1000.0
        time.sleep(random.uniform(lo, hi))

    def pre_select_settle(self, config: dict | None = None) -> None:
        cfg = self._cfg(config)
        ms = max(0, int(cfg.get("pre_select_settle_ms", 180)))
        time.sleep(ms / 1000.0)

    def post_deploy_settle(self, config: dict | None = None) -> None:
        cfg = self._cfg(config)
        ms = max(0, int(cfg.get("post_deploy_settle_ms", 300)))
        time.sleep(ms / 1000.0)

    @staticmethod
    def _cfg(config: dict | None) -> dict:
        c = config or {}
        dp = c.get("deploy_pattern", {}) if isinstance(c, dict) else {}
        return {
            "tap_jitter_px":         int(c.get("tap_jitter_px", 12)),
            "tap_hold_min_ms":       int(dp.get("tap_hold_min_ms", 60)),
            "tap_hold_max_ms":       int(dp.get("tap_hold_max_ms", 110)),
            "long_press_ms":         int(dp.get("long_press_ms", 2500)),
            "quick_swipe_ms":        int(dp.get("quick_swipe_ms", 350)),
            "deploy_hold_ms":        int(dp.get("deploy_hold_ms", 1100)),
            "deploy_step_ms":        int(dp.get("deploy_step_ms", 180)),
            "deploy_steps":          int(dp.get("deploy_steps", 6)),
            "inter_action_min_ms":   int(dp.get("inter_action_min_ms", 150)),
            "inter_action_max_ms":   int(dp.get("inter_action_max_ms", 400)),
            "pre_select_settle_ms":  int(dp.get("pre_select_settle_ms", 180)),
            "post_deploy_settle_ms": int(dp.get("post_deploy_settle_ms", 300)),
        }

    @staticmethod
    def _screen_bounds() -> tuple[int, int]:
        """Current device resolution, falling back to the module defaults.

        Clamping against ``DEFAULT_SCREEN_WIDTH`` (2340) instead of the real
        resolution silently pushes jittered taps off-screen on any narrower
        device — e.g. at 1350x1080 roughly a third of the taps near the right
        edge land outside 0..1349 and are dropped by the OS, so troops never
        deploy. Always ask the ADB layer for the live size.
        """
        try:
            from core.adb_handler import get_active_resolution
            w, h = get_active_resolution()
            if w > 0 and h > 0:
                return int(w), int(h)
        except Exception:
            pass
        return DEFAULT_SCREEN_WIDTH, DEFAULT_SCREEN_HEIGHT

    @staticmethod
    def _jitter(x: int, y: int, jitter: int) -> tuple[int, int]:
        max_w, max_h = HumanTouchSkill._screen_bounds()
        if jitter <= 0:
            return (max(0, min(int(x), max_w - 1)),
                    max(0, min(int(y), max_h - 1)))
        jx = random.randint(-jitter, jitter)
        jy = random.randint(-jitter, jitter)
        nx = max(0, min(int(x) + jx, max_w - 1))
        ny = max(0, min(int(y) + jy, max_h - 1))
        return nx, ny
