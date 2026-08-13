"""Ring Sweep — hold one spot on each side of the base to spread a card.

Difference from ``PerimeterSweepRule``: that rule sweeps the four
*screen-edge* corridors and refuses to run unless all four exist, which is
rarely true. This rule builds its route from the red-zone polygon itself, so
it hugs the base at a constant clearance and still works when only two sides
have room.

Each attack picks ONE random point per side and holds it; the sides are
visited in random order, so the same base attacked twice does not produce
the same four spots in the same sequence. Every candidate point was already
verified to sit outside the no-deploy zone.

All four sides at once, or one at a time
----------------------------------------
With ``multi_touch.enabled`` and root, the four spots are pressed together
and the hold window is spent once — every side starts receiving troops in
the first second, and a small army splits between the sides instead of
draining into whichever one went first.

Without it there is no way to put two pointers down: ``input`` carries one
pointer, two parallel ``input swipe`` processes are two independent drags,
and the touchscreen node cannot be written to as the shell user because
SELinux is Enforcing. The fallback holds each side in turn for the full
window. A held card empties at roughly 7 troops per second, so a 5-6 s
hold is about 35-42 troops per side — size it to the army, because a card
that runs dry part-way leaves the later sides empty.
"""

from __future__ import annotations

import random

from core import multi_touch
from core.logger import BotLogger
from logic.rules.air_attack_rule import AirAttackRule
from logic.rules.base_rule import AttackContext

log = BotLogger.get("v2.rule.ring_sweep")


class RingSweepRule(AirAttackRule):
    name = "ring_sweep"
    priority = 85

    def matches(self, profile: dict, screenshot) -> bool:
        # Universal: works for any army because it only needs a polygon.
        return True

    @staticmethod
    def _troop_entry(table: dict, troop: str, fallback):
        """This troop's row, else ``_default``, else ``fallback``.

        The name is matched case-insensitively. Card templates are all
        lowercase but the config is hand-written, and a key that differs
        only in case fails completely silently — the troop just quietly
        gets the default for the rest of the session.
        """
        if not isinstance(table, dict):
            return fallback
        if troop in table:
            return table[troop]
        wanted = str(troop).casefold()
        for key, value in table.items():
            if key != "_default" and str(key).casefold() == wanted:
                return value
        return table.get("_default", fallback)

    @staticmethod
    def _hold_points(sweep_cfg: dict, troop: str) -> int:
        """How many points THIS troop's card is emptied into.

        4 is one per side of a normal base. More splits the army finer
        (and needs that many fingers to land together), fewer concentrates
        it. Per troop because armies are not uniform: a wall breaker wants
        one spot next to the funnel, dragons want every side at once.

        ``hold_points_by_troop`` keys by troop name — same key as the card
        template — and falls back through ``_default`` to the flat
        ``hold_points``, so an existing config with only the scalar keeps
        behaving exactly as it did.
        """
        table = sweep_cfg.get("hold_points_by_troop", {}) or {}
        flat = sweep_cfg.get("hold_points", 4)
        raw = RingSweepRule._troop_entry(table, troop, flat)
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 4

    @staticmethod
    def _hold_window_ms(sweep_cfg: dict, troop: str) -> int:
        """How long this troop's card is held at ONE side.

        ``hold_ms_by_troop`` maps a troop key to ``[min, max]`` and falls
        back to ``_default``. Re-rolled per side, so neither two sides of
        one attack nor two attacks hold for the same time.
        """
        table = sweep_cfg.get("hold_ms_by_troop", {}) or {}
        band = RingSweepRule._troop_entry(table, troop, None) or [5000, 6000]
        try:
            lo, hi = int(band[0]), int(band[-1])
        except (TypeError, ValueError, IndexError):
            lo, hi = 5000, 6000
        lo, hi = max(300, min(lo, hi)), max(300, max(lo, hi))
        return random.randint(lo, hi)

    def _hold_sides(
        self, ctx: AttackContext, sweep_cfg: dict, troop: str, drops: list,
    ) -> None:
        """Hold every side — all at once with multi-touch, else one by one.

        The two paths are NOT the same attack. Four fingers down together
        spend the window once and all sides receive troops from the first
        second. One finger spends the window on each side in turn, so the
        card empties into the earlier sides first and the last side can
        come up dry on a small army. That is the whole reason multi-touch
        is worth root.
        """
        cfg = ctx.config
        # Say which gesture is about to run, every time. The two paths look
        # identical from outside — the attack happens either way — so
        # without this the only way to tell whether the multi-finger switch
        # is doing anything is to count troops on the far side of the base.
        if multi_touch.enabled():
            if multi_touch.available(cfg):
                held = multi_touch.hold_all(
                    drops, self._hold_window_ms(sweep_cfg, troop), cfg,
                )
                if held:
                    return
                log.warning(
                    "RingSweep: multi-touch hold failed — holding one side "
                    "at a time instead.",
                )
            else:
                log.warning(
                    "RingSweep: multi-finger deploy is ON but this device "
                    "cannot do it (no root) — holding one side at a time.",
                )
        else:
            log.info(
                "RingSweep: multi-finger deploy is OFF — holding one side "
                "at a time. Turn it on in Settings to press all %d together.",
                len(drops),
            )
        for x, y in drops:
            if self._interrupted(ctx):
                return
            # Each side gets its own randomized window, so the four
            # presses of one attack are not identical either.
            hold_ms = self._hold_window_ms(sweep_cfg, troop)
            ctx.skills.touch.long_press(x, y, hold_ms, cfg, min_ms=300)

    def execute(self, ctx: AttackContext) -> bool:
        cfg = ctx.config
        skills = ctx.skills
        sweep_cfg = cfg.get("ring_sweep", {})

        screen_w = ctx.screenshot.shape[1]
        ring = skills.ring.plan(ctx.polygon, screen_w, ctx.ui_cutoff, cfg)

        min_points = max(2, int(sweep_cfg.get("min_valid_points", 6)))
        if len(ring) < min_points:
            log.info(
                "RingSweep: only %d deployable ring point(s), need %d — deferring.",
                len(ring), min_points,
            )
            return False

        centre = ctx.base_centroid or (screen_w // 2, ctx.ui_cutoff // 2)
        covered = skills.ring.sides_covered(centre, ring)
        log.info(
            "RingSweep: %d point(s) around base, sides covered: %s",
            len(ring), ", ".join(sorted(covered)),
        )

        deployed_any = False
        hero_drop = ring[0]

        for troop in self._selected_troops(ctx):
            if self._interrupted(ctx):
                self._stamp_engine_post_deploy(ctx, [])
                return deployed_any
            card = skills.target.find_one(ctx.screenshot, troop)
            if card is None:
                log.warning("RingSweep: troop '%s' card not visible — skipped.", troop)
                continue

            wanted = self._hold_points(sweep_cfg, troop)
            drops = skills.ring.pick_drops(centre, ring, wanted)
            if not drops:
                continue
            hero_drop = drops[0]

            log.info(
                "RingSweep: troop=%s holding %d of %d requested point(s) %s",
                troop, len(drops), wanted, drops,
            )
            skills.touch.tap(card[0], card[1], cfg)
            skills.touch.pre_select_settle(cfg)
            self._hold_sides(ctx, sweep_cfg, troop, drops)
            skills.touch.post_deploy_settle(cfg)
            deployed_any = True

        if not deployed_any:
            log.info("RingSweep: no selected troop cards were found.")
            return False

        hero_memory = self._deploy_heroes(ctx, hero_drop, [])
        if self._interrupted(ctx):
            self._stamp_engine_post_deploy(ctx, hero_memory)
            return True
        self._wait_for_engagement(ctx)
        self._fire_hero_abilities(ctx, hero_memory)
        self._deploy_spells(ctx, hero_drop, centre)
        self._stamp_engine_post_deploy(ctx, hero_memory)
        return True
