from __future__ import annotations

from core.logger import BotLogger
from logic.rules.air_attack_rule import AirAttackRule
from logic.rules.base_rule import AttackContext

log = BotLogger.get("v2.rule.fixed_ring")


class FixedRingRule(AirAttackRule):
    name = "fixed_ring"
    priority = 86
    needs_polygon = False

    def matches(self, profile: dict, screenshot) -> bool:
        return True

    @staticmethod
    def _route(ring_cfg: dict, screen_w: int, screen_h: int) -> list[tuple[int, int]]:
        ref_w, ref_h = ring_cfg.get("reference_size", [screen_w, screen_h])
        sx, sy = screen_w / float(ref_w), screen_h / float(ref_h)
        return [
            (int(round(x * sx)), int(round(y * sy)))
            for x, y in ring_cfg.get("route", []) or []
        ]

    @staticmethod
    def _tap_config(cfg: dict, ring_cfg: dict) -> dict:
        lo, hi = ring_cfg.get("tap_gap_ms", [400, 600])
        pattern = dict(cfg.get("deploy_pattern", {}) or {})
        pattern["inter_action_min_ms"] = int(lo)
        pattern["inter_action_max_ms"] = int(hi)
        return {
            **cfg,
            "tap_jitter_px": int(ring_cfg.get("jitter_px", 10)),
            "deploy_pattern": pattern,
        }

    def execute(self, ctx: AttackContext) -> bool:
        cfg = ctx.config
        skills = ctx.skills
        ring_cfg = cfg.get("fixed_ring", {}) or {}

        screen_h, screen_w = ctx.screenshot.shape[:2]
        route = self._route(ring_cfg, screen_w, screen_h)
        if not route:
            log.warning("FixedRing: config.fixed_ring.route is empty — nothing to tap.")
            return False

        tap_cfg = self._tap_config(cfg, ring_cfg)
        centre = (
            sum(x for x, _ in route) // len(route),
            sum(y for _, y in route) // len(route),
        )
        deployed_any = False

        for troop in self._selected_troops(ctx):
            if self._interrupted(ctx):
                self._stamp_engine_post_deploy(ctx, [])
                return deployed_any
            card = skills.target.find_one(ctx.screenshot, troop)
            if card is None:
                log.warning("FixedRing: troop '%s' card not visible — skipped.", troop)
                continue

            log.info("FixedRing: troop=%s tapping %d route point(s)", troop, len(route))
            skills.touch.tap(card[0], card[1], cfg)
            skills.touch.pre_select_settle(cfg)
            for x, y in route:
                if self._interrupted(ctx):
                    break
                skills.touch.tap(x, y, tap_cfg)
            skills.touch.post_deploy_settle(cfg)
            deployed_any = True

        if not deployed_any:
            log.info("FixedRing: no selected troop cards were found.")
            return False

        hero_memory = self._deploy_heroes(ctx, route[0], [])
        if self._interrupted(ctx):
            self._stamp_engine_post_deploy(ctx, hero_memory)
            return True
        self._wait_for_engagement(ctx)
        self._fire_hero_abilities(ctx, hero_memory)
        self._deploy_spells(ctx, route[0], centre)
        self._stamp_engine_post_deploy(ctx, hero_memory)
        return True
