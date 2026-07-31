"""Random-start perimeter sweep deployment rule."""

from __future__ import annotations

from core.logger import BotLogger
from logic.rules.air_attack_rule import AirAttackRule
from logic.rules.base_rule import AttackContext

log = BotLogger.get("v2.rule.perimeter_sweep")


class PerimeterSweepRule(AirAttackRule):
    name = "perimeter_sweep"
    priority = 80

    def matches(self, profile: dict, screenshot) -> bool:
        return True

    def execute(self, ctx: AttackContext) -> bool:
        cfg = ctx.config
        skills = ctx.skills
        sweep_cfg = cfg.get("perimeter_sweep", {})
        corridor_cfg = dict(cfg)
        corridor_cfg["stand_off_px"] = int(sweep_cfg.get("clearance_px", 30))
        corridor_cfg["min_corridor_width_px"] = int(
            sweep_cfg.get("min_corridor_width_px", 16),
        )
        corridors = skills.corridor.map(
            ctx.screenshot, ctx.polygon, ctx.ui_cutoff, corridor_cfg,
        )
        perimeter = skills.perimeter.plan(corridors, cfg)
        if not perimeter:
            log.info("PerimeterSweep: four safe map edges are not available.")
            return False

        duration_ms = int(sweep_cfg.get("swipe_duration_ms", 280))
        deployed_any = False
        hero_drop = perimeter[0]

        for troop in self._selected_troops(ctx):
            if self._interrupted(ctx):
                self._stamp_engine_post_deploy(ctx, [])
                return deployed_any
            card = skills.target.find_one(ctx.screenshot, troop)
            if card is None:
                continue

            route = skills.perimeter.randomize_route(perimeter)
            hero_drop = route[0]
            start_index = perimeter.index(route[0])
            clockwise_next = perimeter[(start_index + 1) % len(perimeter)]
            direction = "clockwise" if route[1] == clockwise_next else "counter-clockwise"
            log.info(
                "PerimeterSweep: troop=%s start=%s direction=%s segments=%d",
                troop,
                route[0],
                direction,
                len(route),
            )
            skills.touch.tap(card[0], card[1], cfg)
            skills.touch.pre_select_settle(cfg)
            for index, start in enumerate(route):
                if self._interrupted(ctx):
                    break
                end = route[(index + 1) % len(route)]
                skills.touch.quick_swipe(
                    start[0], start[1], end[0], end[1], duration_ms, cfg,
                )
            skills.touch.post_deploy_settle(cfg)
            deployed_any = True

        if not deployed_any:
            log.info("PerimeterSweep: no selected troop cards were found.")
            return False

        target = ctx.base_centroid or (
            ctx.screenshot.shape[1] // 2,
            ctx.ui_cutoff // 2,
        )
        hero_memory = self._deploy_heroes(ctx, hero_drop, [])
        if self._interrupted(ctx):
            self._stamp_engine_post_deploy(ctx, hero_memory)
            return True
        self._wait_for_engagement(ctx)
        self._fire_hero_abilities(ctx, hero_memory)
        self._deploy_spells(ctx, hero_drop, target)
        self._stamp_engine_post_deploy(ctx, hero_memory)
        return True
