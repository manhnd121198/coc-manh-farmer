"""
AirAttackRule — fan-deploy air troops along the corridor with the
fewest air defenses. Drops the tank (lava_hound) first when present,
then the DPS air troops in stagger waves, then heroes, then spells.

Trigger: any selected troop has profile.kind == "air".
"""

from __future__ import annotations

import math
import time

from core.adb_handler import screencap
from core.logger import BotLogger
from logic.rules.base_rule import AttackContext, AttackRule
from vision.skills.safe_corridor import SafeCorridorSkill

log = BotLogger.get("v2.rule.air_attack")


class AirAttackRule(AttackRule):
    name = "air_attack"
    priority = 30

    def matches(self, profile: dict, screenshot) -> bool:
        return True

    def execute(self, ctx: AttackContext) -> bool:
        cfg = ctx.config
        troop_profiles = ctx.troop_profiles
        ss = ctx.screenshot
        skills = ctx.skills

        corridors = skills.corridor.map(ss, ctx.polygon, ctx.ui_cutoff, cfg)
        if not corridors:
            return False

        side = skills.corner.pick_for_air(ss, corridors, cfg) \
            or SafeCorridorSkill.widest(corridors)
        rect = corridors[side]
        fan_points = skills.fan.plan(rect, count=9)

        validated: list[tuple[int, int]] = []
        for (px, py) in fan_points:
            ok = self._find_safe_deployable(ctx, ss, (px, py), cfg)
            if ok is not None:
                validated.append(ok)
            else:
                log.warning(
                    "AirAttack: no valid terrain outside red zone near (%d,%d) — skipped.",
                    px, py,
                )
        if not validated:
            log.warning("AirAttack: no troop point safely outside the red zone.")
            return False

        cluster = validated[len(validated) // 2]
        target = ctx.base_centroid or SafeCorridorSkill.center(rect)

        air_troops = [
            t for t in self._selected_troops(ctx)
            if self._troop_kind(t, ctx) == "air"
        ]
        air_troops.sort(
            key=lambda t: (
                0 if troop_profiles.get(t, {}).get("drop_first") else 1,
                t,
            )
        )

        hero_memory = self._deploy_air_troops(ctx, air_troops, validated, cluster)
        if self._interrupted(ctx):
            self._stamp_engine_post_deploy(ctx, hero_memory)
            return True

        hero_memory = self._deploy_heroes(ctx, cluster, hero_memory)
        if self._interrupted(ctx):
            self._stamp_engine_post_deploy(ctx, hero_memory)
            return True

        self._wait_for_engagement(ctx)
        if self._interrupted(ctx):
            self._stamp_engine_post_deploy(ctx, hero_memory)
            return True

        self._fire_hero_abilities(ctx, hero_memory)
        if self._interrupted(ctx):
            self._stamp_engine_post_deploy(ctx, hero_memory)
            return True

        self._deploy_spells(ctx, cluster, target)
        self._stamp_engine_post_deploy(ctx, hero_memory)
        return True

    def _deploy_air_troops(
        self,
        ctx: AttackContext,
        air_troops: list[str],
        fan_points: list[tuple[int, int]],
        cluster: tuple[int, int],
    ) -> list:
        skills = ctx.skills
        cfg = ctx.config
        ss = ctx.screenshot
        for troop in air_troops:
            if self._interrupted(ctx):
                break
            card = skills.target.find_one(ss, troop)
            if card is None:
                log.warning(
                    "AirAttack: troop '%s' card not visible — skipped; refresh its template.",
                    troop,
                )
                continue
            skills.touch.tap(card[0], card[1], cfg)
            skills.touch.pre_select_settle(cfg)
            style = ctx.troop_profiles.get(troop, {}).get("style", "fan")
            if style == "stack":
                skills.touch.long_press(cluster[0], cluster[1], None, cfg)
            else:
                troop_profile = ctx.troop_profiles.get(troop, {})
                stagger_ms = int(troop_profile.get("stagger_ms", 220))
                deploy_taps = max(1, int(troop_profile.get("deploy_taps", len(fan_points))))
                for index in range(deploy_taps):
                    if self._interrupted(ctx):
                        break
                    px, py = fan_points[index % len(fan_points)]
                    if not self._is_safe_deploy_point(ctx, (px, py)):
                        log.warning(
                            "AirAttack: skipped troop tap inside red zone at (%d,%d).",
                            px, py,
                        )
                        continue
                    skills.touch.tap(px, py, cfg)
                    time.sleep(stagger_ms / 1000.0)
            skills.touch.post_deploy_settle(cfg)
        return []

    def _deploy_heroes(
        self,
        ctx: AttackContext,
        cluster: tuple[int, int],
        hero_memory: list,
    ) -> list:
        skills = ctx.skills
        cfg = ctx.config
        ss = ctx.screenshot
        heroes = self._selected_heroes(ctx)
        cards: list[tuple[str, int, int]] = []
        for hero in heroes:
            hit = skills.target.find_one(ss, hero)
            if hit is not None:
                cards.append((hero, hit[0], hit[1]))
        plans = skills.hero.plan_drops(cluster, cards)
        for (name, card_xy, drop_xy) in plans:
            if self._interrupted(ctx):
                break
            if not self._is_safe_deploy_point(ctx, drop_xy):
                if not self._is_safe_deploy_point(ctx, cluster):
                    # Named after the rule that actually ran: Ring Sweep
                    # inherits this method, and a hardcoded "AirAttack"
                    # sends anyone reading the log after the wrong rule.
                    log.warning(
                        "%s: hero '%s' has no point safely outside red zone — skipped.",
                        self.name, name,
                    )
                    continue
                log.warning(
                    "%s: hero '%s' jitter entered red zone; using cluster (%d,%d).",
                    self.name, name, cluster[0], cluster[1],
                )
                drop_xy = cluster
            skills.touch.tap(card_xy[0], card_xy[1], cfg)
            skills.touch.pre_select_settle(cfg)
            skills.touch.tap(drop_xy[0], drop_xy[1], cfg)
            skills.touch.post_deploy_settle(cfg)
            hero_memory.append((name, card_xy, drop_xy))
        return hero_memory

    @staticmethod
    def _is_safe_deploy_point(
        ctx: AttackContext,
        point: tuple[int, int],
        margin_px: int | None = None,
    ) -> bool:
        """Whether a drop lands clear of the no-deploy shape.

        Judged against ``ctx.deploy_guard`` when the rule set one, else the
        red-zone polygon. That matters because the two are not the same
        shape: the red-zone polygon is the hull of every red pixel in the
        base, so it reaches past the real boundary, while the YOLO base hull
        traces the buildings. A rule that planned its drops against one of
        them must be checked against that same one — measuring the drop off
        the other rejects points sitting on perfectly good grass.
        """
        polygon = getattr(ctx, "deploy_guard", None)
        if polygon is None:
            polygon = getattr(ctx, "polygon", None)
        if polygon is None or len(polygon) < 3:
            return True
        if margin_px is None:
            margin_px = int(getattr(ctx, "deploy_guard_margin_px", 25))
        x, y = point
        return not ctx.skills.red_zone.is_inside(
            polygon, int(x), int(y), margin=margin_px,
        )

    def _find_safe_deployable(
        self,
        ctx: AttackContext,
        screenshot,
        point: tuple[int, int],
        config: dict,
        max_rings: int = 12,
        step_px: int = 20,
    ) -> tuple[int, int] | None:
        x, y = point
        shape = getattr(screenshot, "shape", None)
        if shape is not None:
            screen_h, screen_w = shape[:2]
            ui_cutoff = int(getattr(ctx, "ui_cutoff", screen_h))
        else:
            screen_h = screen_w = ui_cutoff = None
        offsets = [(0, 0)]
        for ring in range(1, max_rings + 1):
            step = ring * step_px
            offsets.extend([
                (0, step), (0, -step), (step, 0), (-step, 0),
                (step, step), (step, -step), (-step, step), (-step, -step),
            ])
        for dx, dy in offsets:
            candidate = (x + dx, y + dy)
            if screen_w is not None and (
                candidate[0] < 60
                or candidate[0] >= screen_w - 60
                or candidate[1] < 110
                or candidate[1] >= ui_cutoff - 80
            ):
                continue
            if not self._is_safe_deploy_point(ctx, candidate):
                continue
            if ctx.skills.obstacle.is_deployable(
                screenshot, candidate[0], candidate[1], config,
            ):
                return candidate
        return None

    def _wait_for_engagement(self, ctx: AttackContext) -> None:
        # Roll lại mỗi trận. Vẫn chờ kể cả khi tắt bấm kỹ năng — spell
        # thả sau đó cần quân đã giao chiến rồi.
        delay = ctx.skills.hero.ability_delay_seconds(ctx.config)
        log.info("Waiting %.1fs for the army to engage.", delay)
        end = time.time() + max(0.0, delay)
        while time.time() < end:
            if self._interrupted(ctx):
                return
            time.sleep(0.25)

    def _fire_hero_abilities(self, ctx: AttackContext, hero_memory: list) -> None:
        skills = ctx.skills
        cfg = ctx.config
        if not skills.hero.ability_enabled(cfg):
            if hero_memory:
                log.info(
                    "Hero abilities are off — leaving the %d hero(es) to fire "
                    "their own when their health runs low.", len(hero_memory),
                )
            return
        gap_ms = skills.hero.ability_double_tap_gap_ms(cfg)
        for (_name, card_xy, _drop_xy) in hero_memory:
            if self._interrupted(ctx):
                return
            skills.touch.double_tap(card_xy[0], card_xy[1], gap_ms=gap_ms, config=cfg)

    def _deploy_spells(
        self,
        ctx: AttackContext,
        cluster: tuple[int, int],
        target: tuple[int, int],
    ) -> None:
        """Drop every selected spell using its configured placement and
        optional wave schedule.

        Robustness rules:
            • Refresh the screenshot first — the spell bar's page may
              have changed after the troop dump.
            • Use prefix expansion (`rage_spell` matches `rage_spell_5`)
              so card lookup never fails on level-suffixed templates.
            • Spells without a profile in v2_spell_profiles.json get a
              sensible "ahead of army" default (60 % along cluster→target),
              rather than being silently skipped.
            • Inter-spell delay is the default tap-settle only — no extra
              pre/post settles between spells, so the whole sequence
              feels like one batch (the user requested دفعة واحدة).
        """
        skills = ctx.skills
        cfg = ctx.config

        selected = self._selected_spells(ctx)
        if not selected:
            return

        # Fresh screenshot — the spell bar may have moved page after the
        # troop dump, so reading from the stale ctx.screenshot can miss
        # cards that are actually visible right now.
        fresh = screencap()
        ss = fresh if fresh is not None else ctx.screenshot

        # "advancing_line" spells (Rage/Heal) fire together as a rolling
        # barrage: one line of drops in front of the army per wave, Rage
        # then Heal on the same front, wait, then the next wave lands
        # further along the push. Every other spell keeps its own schedule.
        barrage = [
            s for s in selected
            if str((ctx.spell_profiles.get(s, {}) or {}).get("placement", ""))
            == "advancing_line"
        ]
        others = [s for s in selected if s not in barrage]

        if barrage:
            self._deploy_barrage(ctx, barrage, cluster, target, ss)
        for spell in others:
            if self._interrupted(ctx):
                break
            self._deploy_single_spell(ctx, spell, cluster, target, ss)

        # One final settle so the engine post-deploy stamp is clean.
        skills.touch.post_deploy_settle(cfg)

    def _deploy_single_spell(
        self,
        ctx: AttackContext,
        spell: str,
        cluster: tuple[int, int],
        target: tuple[int, int],
        ss,
    ) -> None:
        """One spell on its own placement + wave schedule (classic path)."""
        skills = ctx.skills
        cfg = ctx.config

        candidates = skills.target.expand_prefix(spell) or [spell]
        hit = skills.target.find_first_of(ss, candidates)
        if hit is None:
            log.info(
                "Spell '%s': card not visible on the bar (tried %d variants) — skipped.",
                spell, len(candidates),
            )
            return
        _, card_x, card_y = hit

        # For "carpet the interior" placements, prefer the YOLO base hull
        # (the actual building cluster) over the HSV red-line polygon: the
        # red line sits out on the grass, so spells planned against it would
        # land short of the buildings.
        spell_profile = ctx.spell_profiles.get(spell, {}) or {}
        poly = ctx.polygon
        if str(spell_profile.get("placement", "")).startswith("inside_base"):
            yolo_base = skills.red_zone.yolo_base_polygon()
            if yolo_base is not None:
                poly = yolo_base

        drops = skills.spell.plan_spell(
            ss, spell, cluster, target, cfg, ctx.spell_profiles, poly,
        ) or [self._default_spell_drop(cluster, target)]

        drops_per_wave = max(1, int(spell_profile.get("drops_per_wave", len(drops))))
        wave_interval_sec = max(0.0, float(spell_profile.get("wave_interval_sec", 0.0)))
        drop_interval_sec = max(0.0, float(spell_profile.get("drop_interval_ms", 0))) / 1000.0

        # Debug overlay of the planned carpet (only for interior spreads,
        # where `poly` is the base the drops were planned against).
        if str(spell_profile.get("placement", "")).startswith("inside_base"):
            skills.red_zone.dump_spell_plan(ss, poly, drops, drops_per_wave, cfg, spell)

        log.info("Spell '%s': selecting once, then %d drop(s) %s", spell, len(drops), drops)
        skills.touch.tap(card_x, card_y, cfg)
        skills.touch.pre_select_settle(cfg)
        schedule_started_at = time.monotonic()
        for index, (sx, sy) in enumerate(drops):
            if self._interrupted(ctx):
                return
            wave_index = index // drops_per_wave
            if index % drops_per_wave == 0:
                deadline = schedule_started_at + wave_index * wave_interval_sec
                if not self._wait_until(ctx, deadline):
                    return
                log.info(
                    "Spell '%s': wave %d, drops %d-%d",
                    spell, wave_index + 1, index + 1,
                    min(index + drops_per_wave, len(drops)),
                )
            # CoC keeps the same card selected while its quantity remains, so
            # re-tapping before every drop can lose selection when the bar
            # animates — tap the card once, then only the drop points.
            skills.touch.tap(sx, sy, cfg)
            if drop_interval_sec > 0 and index + 1 < len(drops):
                time.sleep(drop_interval_sec)

    def _deploy_barrage(
        self,
        ctx: AttackContext,
        spells: list[str],
        cluster: tuple[int, int],
        target: tuple[int, int],
        ss,
    ) -> None:
        """Rolling barrage: per wave, drop one line for each barrage spell
        (Rage then Heal on the same front), then wait ``wave_interval_sec``
        so the next wave lands further along the push.

        The front is ESTIMATED geometrically — it advances along the
        deploy→base-centre line each wave (see ``_advancing_line``). The bot
        does not see the troops, so if they stall at a wall the line still
        moves on. Swap this estimate for real troop detection later if
        needed.
        """
        skills = ctx.skills
        cfg = ctx.config

        # Plan each barrage spell's full point list up front (wave-major:
        # the first ``drops_per_wave`` points are wave 1's line, etc.).
        plans: dict[str, tuple[list, int]] = {}
        num_waves = 1
        interval = 0.0
        for spell in spells:
            prof = ctx.spell_profiles.get(spell, {}) or {}
            poly = skills.red_zone.yolo_base_polygon()
            if poly is None:
                poly = ctx.polygon
            pts = skills.spell.plan_spell(
                ss, spell, cluster, target, cfg, ctx.spell_profiles, poly,
            ) or []
            per_wave = max(1, int(prof.get("drops_per_wave", 5)))
            plans[spell] = (pts, per_wave)
            if pts:
                num_waves = max(num_waves, math.ceil(len(pts) / per_wave))
                skills.red_zone.dump_spell_plan(ss, poly, pts, per_wave, cfg, spell)
            interval = max(interval, float(prof.get("wave_interval_sec", 0.0)))

        log.info(
            "Spell barrage: %s over %d wave(s), %.1fs apart.",
            " + ".join(spells), num_waves, interval,
        )

        started_at = time.monotonic()
        for wave in range(num_waves):
            if self._interrupted(ctx):
                return
            if not self._wait_until(ctx, started_at + wave * interval):
                return
            # Refresh so the spell bar page is current for this wave.
            fresh = screencap()
            wave_ss = fresh if fresh is not None else ss
            for spell in spells:
                pts, per_wave = plans[spell]
                line = pts[wave * per_wave:(wave + 1) * per_wave]
                if not line:
                    continue
                candidates = skills.target.expand_prefix(spell) or [spell]
                hit = skills.target.find_first_of(wave_ss, candidates)
                if hit is None:
                    log.info(
                        "Barrage wave %d: spell '%s' card not visible — skipped.",
                        wave + 1, spell,
                    )
                    continue
                _, card_x, card_y = hit
                log.info(
                    "Barrage wave %d: spell '%s' → %d drop(s) %s",
                    wave + 1, spell, len(line), line,
                )
                skills.touch.tap(card_x, card_y, cfg)
                skills.touch.pre_select_settle(cfg)
                for (sx, sy) in line:
                    if self._interrupted(ctx):
                        return
                    skills.touch.tap(sx, sy, cfg)

    def _wait_until(self, ctx: AttackContext, deadline: float) -> bool:
        while True:
            if self._interrupted(ctx):
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(0.25, remaining))

    @staticmethod
    def _default_spell_drop(
        cluster: tuple[int, int],
        target: tuple[int, int],
    ) -> tuple[int, int]:
        """In-front-of-army fallback when the planner returns nothing."""
        cx, cy = cluster
        tx, ty = target
        return (
            int(round(cx + (tx - cx) * 0.60)),
            int(round(cy + (ty - cy) * 0.60)),
        )
