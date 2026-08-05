"""
Base contracts for V2 attack rules.

A Rule is a strategy. The orchestrator picks ONE rule per attack and
runs its `execute` method. Rules access Skills through the
`AttackContext.skills` bundle and Config through `AttackContext.config`.

A rule MUST implement two methods:
    matches(profile, screenshot)  → bool
    execute(ctx)                  → bool

`execute` returns True if the rule actually carried out a deploy (or at
least started one), False if pre-conditions failed and the orchestrator
should chain to the next fallback (typically SmartDefault, then legacy
V36). Rules that early-return without deploying MUST return False so the
orchestrator can recover.

A rule SHOULD be deterministic: given the same screenshot and profile
it should produce the same drops (modulo human-touch jitter).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np

from core.adb_handler import screencap
from core.logger import BotLogger
from logic.skills.fan_planner import FanPlannerSkill
from logic.skills.funnel_planner import FunnelPlannerSkill
from logic.skills.hero_planner import HeroPlannerSkill
from logic.skills.human_touch import HumanTouchSkill
from logic.skills.perimeter_planner import PerimeterPlannerSkill
from logic.skills.ring_planner import RingPlannerSkill
from logic.skills.spell_planner import SpellPlannerSkill
from vision.skills.corner_selector import CornerSelectorSkill
from vision.skills.isometric_grid import IsometricGridSkill
from vision.skills.obstacle_detector import ObstacleDetectorSkill
from vision.skills.red_zone_polygon import RedZonePolygonSkill
from vision.skills.safe_corridor import SafeCorridorSkill
from vision.skills.target_locator import TargetLocatorSkill

log = BotLogger.get("v2.rule")


@dataclass
class SkillBundle:
    red_zone:   RedZonePolygonSkill
    iso_grid:   IsometricGridSkill
    corridor:   SafeCorridorSkill
    obstacle:   ObstacleDetectorSkill
    target:     TargetLocatorSkill
    corner:     CornerSelectorSkill
    touch:      HumanTouchSkill
    fan:        FanPlannerSkill
    funnel:     FunnelPlannerSkill
    spell:      SpellPlannerSkill
    hero:       HeroPlannerSkill
    perimeter:  PerimeterPlannerSkill
    ring:       RingPlannerSkill


@dataclass
class AttackContext:
    screenshot:     np.ndarray
    profile:        dict
    config:         dict
    troop_profiles: dict
    spell_profiles: dict
    skills:         SkillBundle
    mode_key:       str
    target_key:     str
    ui_cutoff:      int
    engine:         Any | None = None
    polygon:        np.ndarray | None = None
    base_centroid:  tuple[int, int] | None = None


class AttackRule(ABC):
    """Abstract attack strategy."""

    name: str = "base_rule"
    priority: int = 100

    @abstractmethod
    def matches(self, profile: dict, screenshot: np.ndarray) -> bool: ...

    @abstractmethod
    def execute(self, ctx: AttackContext) -> bool: ...

    def _interrupted(self, ctx: AttackContext) -> bool:
        eng = ctx.engine
        if eng is None:
            return False
        return (not getattr(eng, "_running", False)) or getattr(eng, "_paused", False)

    @staticmethod
    def _selected_troops(ctx: AttackContext) -> list[str]:
        key = "bb_selected_troops" if ctx.mode_key == "bb" else "selected_troops"
        return list(ctx.profile.get(key, []) or [])

    @staticmethod
    def _selected_heroes(ctx: AttackContext) -> list[str]:
        key = "bb_selected_heroes" if ctx.mode_key == "bb" else "selected_heroes"
        return list(ctx.profile.get(key, []) or [])

    @staticmethod
    def _selected_spells(ctx: AttackContext) -> list[str]:
        key = "bb_selected_spells" if ctx.mode_key == "bb" else "selected_spells"
        return list(ctx.profile.get(key, []) or [])

    @staticmethod
    def _troop_kind(troop_name: str, ctx: AttackContext) -> str:
        prof = ctx.troop_profiles.get(troop_name, {}) if ctx.troop_profiles else {}
        return str(prof.get("kind", "ground"))

    @staticmethod
    def _has_kind(troop_names: list[str], kind: str, ctx: AttackContext) -> bool:
        return any(AttackRule._troop_kind(t, ctx) == kind for t in troop_names)

    # ── Plan overlay (diagnostics) ──────────────────────────────────
    # The existing polygon debug dump only fires when a pass FAILS
    # sanity. The dangerous case is the opposite one: a polygon that
    # passes sanity but is smaller than the real no-deploy zone. Nothing
    # downstream can notice, because the corridor map, the safety check
    # and the nudge search all consult that same wrong polygon — so the
    # bot taps the red area and the game answers with a banner the bot
    # never reads. This writes the accepted plan out so it can be seen.

    @staticmethod
    def _dump_plan(
        ctx: AttackContext,
        points: list[tuple[int, int]],
        corridor: tuple[int, int, int, int] | None = None,
        label: str = "plan",
    ) -> None:
        """Write ``<debug_dump>/plan_<label>_<ts>.png`` with the accepted
        polygon, the chosen corridor and every planned drop drawn on the
        exact frame the plan was built from. No-op unless
        ``polygon.debug_dump`` names a directory."""
        cfg = (ctx.config or {}).get("polygon", {}) or {}
        out_dir = str(cfg.get("debug_dump", "") or "")
        if not out_dir:
            return
        try:
            import os
            import time as _time

            import cv2

            canvas = ctx.screenshot.copy()
            if ctx.polygon is not None and len(ctx.polygon) >= 3:
                cv2.polylines(
                    canvas, [ctx.polygon.reshape(-1, 1, 2).astype("int32")],
                    True, (0, 0, 255), 3,
                )
            if corridor is not None:
                cx, cy, cw, ch = corridor
                cv2.rectangle(canvas, (cx, cy), (cx + cw, cy + ch), (0, 255, 255), 2)
            if ctx.base_centroid is not None:
                cv2.drawMarker(
                    canvas, tuple(int(v) for v in ctx.base_centroid),
                    (255, 0, 255), cv2.MARKER_CROSS, 30, 2,
                )
            for order, (px, py) in enumerate(points):
                cv2.circle(canvas, (int(px), int(py)), 12, (0, 255, 0), 2)
                cv2.putText(
                    canvas, str(order), (int(px) + 14, int(py) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA,
                )

            os.makedirs(out_dir, exist_ok=True)
            stamp = int(_time.time())
            path = os.path.join(out_dir, f"plan_{label}_{stamp}.png")
            cv2.imwrite(path, canvas)
            # The un-annotated frame too: tuning the HSV bands against an
            # overlay means measuring our own drawing, since the polygon
            # is stroked in exactly the red the mask looks for.
            cv2.imwrite(
                os.path.join(out_dir, f"plan_{label}_{stamp}_raw.png"),
                ctx.screenshot,
            )
            log.info("Plan overlay written to %s", path)
        except Exception as exc:      # diagnostics must never break an attack
            log.debug("Plan overlay failed: %s", exc)

    @staticmethod
    def _with_burst_gap(config: dict, gap_ms: int) -> dict:
        """Copy of ``config`` whose tap bursts pace themselves by
        ``gap_ms`` on the device. Shallow copies only the two dicts we
        touch, so the shared config object is never mutated."""
        cfg = dict(config or {})
        dp = dict(cfg.get("deploy_pattern", {}) or {})
        dp["tap_burst_gap_ms"] = max(0, int(gap_ms))
        cfg["deploy_pattern"] = dp
        return cfg

    # ── Hold-to-dump ────────────────────────────────────────────────
    # CoC deploys continuously while a finger stays down on the map, so
    # holding empties a card far faster than a burst of taps. How long a
    # card takes to empty depends on the army, so instead of guessing a
    # duration we hold in chunks and check the deploy bar between them:
    # an exhausted card is removed from the bar, so "card no longer
    # found" means "all deployed".

    @staticmethod
    def _hold_enabled(ctx: AttackContext, troop: str) -> bool:
        """Per-troop ``deploy_mode`` wins; otherwise the global
        ``deploy_pattern.hold_until_empty`` flag decides."""
        profile = (ctx.troop_profiles or {}).get(troop, {}) or {}
        mode = str(profile.get("deploy_mode", "")).lower()
        if mode in ("hold", "tap"):
            return mode == "hold"
        dp = (ctx.config or {}).get("deploy_pattern", {}) or {}
        return bool(dp.get("hold_until_empty", False))

    def _hold_dump(
        self,
        ctx: AttackContext,
        troop: str,
        points: list[tuple[int, int]],
    ) -> None:
        """Hold on ``points`` (round-robin) until ``troop``'s card leaves
        the bar, or until the configured budget runs out.

        The budget is a safety net, not the normal exit: if a card greys
        out instead of disappearing on this device, we simply hold for the
        whole budget — which is still "dump everything", just slower.
        """
        if not points:
            return
        dp = (ctx.config or {}).get("deploy_pattern", {}) or {}
        chunk = max(600, int(dp.get("hold_chunk_ms", 2000)))
        budget = max(chunk, int(dp.get("hold_max_ms", 24000)))

        spent = 0
        index = 0
        while spent < budget:
            if self._interrupted(ctx):
                return
            px, py = points[index % len(points)]
            index += 1
            ctx.skills.touch.long_press(px, py, chunk, ctx.config)
            spent += chunk

            ss = screencap()
            if ss is None:
                continue
            if ctx.skills.target.find_one(ss, troop) is None:
                log.info("hold-dump '%s': card left the bar after %.1fs — deployed.",
                         troop, spent / 1000.0)
                return

        log.info("hold-dump '%s': stopped at the %.0fs budget, card still on the bar.",
                 troop, budget / 1000.0)

    @staticmethod
    def _stamp_engine_post_deploy(ctx: AttackContext, hero_memory: list) -> None:
        eng = ctx.engine
        if eng is None:
            return
        try:
            import time as _t
            eng._home_logic._post_deploy_time = _t.time()
            eng._home_logic._battle_phase_done = True
            eng._home_logic._hero_memory = [
                (name, card_xy[0], card_xy[1])
                for (name, card_xy, _drop_xy) in hero_memory
            ]
        except Exception:
            pass
