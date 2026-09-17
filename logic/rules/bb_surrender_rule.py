"""
BBSurrenderRule — drop ONE unit of the first selected troop, then
surrender. Farms Builder Base elixir without spending the army.

The surrender button only exists once a troop is on the field, and it
shows up in the very frame the drop lands. So the button doubles as the
proof of deployment: after each drop tap one screenshot is checked, and
if the button is missing the tap fell outside the playable grass and the
next candidate point is tried. The point that worked is tried first on
the next battle.

Blind "emergency" taps are never used here: with a troop card selected,
a tap on the map deploys it, which turns a surrender into a real attack.

It always returns True: returning False makes the orchestrator chain
into a full attack, which is exactly what this rule exists to avoid.

The rule instance lives as long as the engine, so its battle counter is
per run: ``bb_surrender_max_battles`` (0 = no limit) stops the bot once
that many battles have been surrendered.
"""

from __future__ import annotations

import time

from core.adb_handler import screencap
from core.logger import BotLogger
from core.settings import Settings
from logic.rules.base_rule import AttackContext, AttackRule
from vision.screen_reader import ScreenReader

log = BotLogger.get("v2.rule.bb_surrender")

SURRENDER_KEYS = ("surrender_button", "end_battle_button")
CONFIRM_KEY = "end_battle_confirm"
LINE_CANDIDATES = 4


class BBSurrenderRule(AttackRule):
    name = "bb_surrender"
    priority = 90
    needs_polygon = False

    def __init__(self) -> None:
        self._good_drop: tuple[int, int] | None = None
        self._confirm_xy: tuple[int, int] | None = None
        self._battles = 0

    def matches(self, profile: dict, screenshot) -> bool:
        return True

    def execute(self, ctx: AttackContext) -> bool:
        rule_cfg = ctx.config.get("bb_surrender", {}) or {}

        troops = self._selected_troops(ctx)
        if not troops:
            log.warning("BBSurrender: no troop selected — nothing to drop, cannot surrender.")
            return True

        button = self._drop_one(ctx, troops[0], rule_cfg)
        if button is None or self._interrupted(ctx):
            return True
        self._surrender(ctx, button, rule_cfg)
        self._count_battle(ctx)
        return True

    # ── Drop ────────────────────────────────────────────────────────────
    def _drop_one(self, ctx: AttackContext, troop: str, rule_cfg: dict):
        card = ctx.skills.target.find_one(ctx.screenshot, troop)
        if card is None:
            log.warning("BBSurrender: card '%s' not visible — no drop.", troop)
            return None

        ctx.skills.touch.tap(card[0], card[1], ctx.config)
        ctx.skills.touch.pre_select_settle(ctx.config)
        check_s = self._ms(rule_cfg, "drop_check_ms", 300)

        for x, y in self._drop_candidates(ctx):
            if self._interrupted(ctx):
                return None
            ctx.skills.touch.tap(x, y, ctx.config)
            time.sleep(check_s)
            shot = screencap()
            hit = ctx.skills.target.find_first_of(shot, list(SURRENDER_KEYS)) if shot is not None else None
            if hit is not None:
                log.info("BBSurrender: 1x %s landed @ (%d,%d).", troop, x, y)
                self._good_drop = (x, y)
                return hit[1], hit[2]
            log.info("BBSurrender: drop @ (%d,%d) did not land — trying the next point.", x, y)
            if (x, y) == self._good_drop:
                self._good_drop = None

        log.warning("BBSurrender: no drop point landed — leaving this battle to run out.")
        return None

    def _drop_candidates(self, ctx: AttackContext) -> list[tuple[int, int]]:
        h, w = ctx.screenshot.shape[:2]
        line, _ = ScreenReader.get_focused_deployment_line(ctx.screenshot, ctx.ui_cutoff, 15)
        mid = len(line) // 2
        ordered = sorted(range(len(line)), key=lambda i: abs(i - mid))[:LINE_CANDIDATES]
        points = [line[i] for i in ordered]
        points.append((int(w * 0.60), int(h * 0.65)))
        if self._good_drop:
            points.insert(0, self._good_drop)

        out: list[tuple[int, int]] = []
        for x, y in points:
            p = (max(100, min(int(x), w - 100)), max(100, min(int(y), ctx.ui_cutoff - 50)))
            if p not in out:
                out.append(p)
        return out

    # ── Surrender ───────────────────────────────────────────────────────
    def _surrender(self, ctx: AttackContext, button: tuple[int, int], rule_cfg: dict) -> None:
        log.info("BBSurrender: surrendering.")
        ctx.skills.touch.tap(button[0], button[1], ctx.config)
        time.sleep(self._ms(rule_cfg, "confirm_delay_ms", 400))

        if self._confirm_xy:
            ctx.skills.touch.tap(*self._confirm_xy, ctx.config)
            time.sleep(self._ms(rule_cfg, "verify_delay_ms", 600))
            shot = screencap()
            if shot is None or ctx.skills.target.find_one(shot, CONFIRM_KEY) is None:
                return
            log.warning("BBSurrender: remembered confirm spot missed — locating it again.")
            self._confirm_xy = None

        shot = screencap()
        confirm = ctx.skills.target.find_one(shot, CONFIRM_KEY) if shot is not None else None
        if confirm is None:
            log.warning("BBSurrender: confirm button not found — battle not surrendered.")
            return
        ctx.skills.touch.tap(confirm[0], confirm[1], ctx.config)
        self._confirm_xy = (confirm[0], confirm[1])

    # ── Battles per run ─────────────────────────────────────────────────
    def _count_battle(self, ctx: AttackContext) -> None:
        self._battles += 1
        limit = int(Settings().get("bb_surrender_max_battles", 0) or 0)
        if limit <= 0:
            log.info("BBSurrender: %d battle(s) this run.", self._battles)
            return
        log.info("BBSurrender: %d/%d battle(s) this run.", self._battles, limit)
        if self._battles >= limit and ctx.engine is not None:
            log.info("BBSurrender: battle limit reached — stopping the bot.")
            ctx.engine.stop_bot()

    @staticmethod
    def _ms(rule_cfg: dict, key: str, default: int) -> float:
        return max(0, int(rule_cfg.get(key, default))) / 1000.0
