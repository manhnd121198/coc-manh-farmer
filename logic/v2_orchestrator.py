"""
V2 Orchestrator — top-level dispatcher for the CSR (Config + Skills +
Rules) attack system.

Responsibilities:
    1. Load the three JSON config files (attack_rules, troop_profiles,
       spell_profiles) from `config/` with hot-reload support.
    2. Build the Skill bundle (vision + logic).
    3. Choose the active Rule (manual override from Settings, or
       auto-detect from the army composition + target_key).
    4. Build the AttackContext (red-zone polygon, base centroid, ui
       cutoff) and dispatch.
    5. Stamp engine post-deploy hooks so retreat-after-deploy keeps
       working.

The orchestrator is the ONLY V2 entry point. SmartV2Logic now contains
no logic — it just delegates here.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np

from core.adb_handler import screencap
from core.logger import BotLogger
from core.settings import Settings
from logic.rules import (
    AirAttackRule,
    AttackContext,
    AttackRule,
    GroundFunnelRule,
    PerimeterSweepRule,
    RingSweepRule,
    ResourceRaidRule,
    SkillBundle,
    SmartDefaultRule,
    THSnipeRule,
)
from logic.skills import (
    FanPlannerSkill,
    FunnelPlannerSkill,
    HeroPlannerSkill,
    HumanTouchSkill,
    PerimeterPlannerSkill,
    RingSweepPlannerSkill,
    SpellPlannerSkill,
)
from vision.screen_reader import ScreenReader
from vision.skills import (
    CardStateSkill,
    CornerSelectorSkill,
    IsometricGridSkill,
    ObstacleDetectorSkill,
    RedZonePolygonSkill,
    SafeCorridorSkill,
    TargetLocatorSkill,
)

log = BotLogger.get("v2.orchestrator")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

ATTACK_RULES_FILE   = CONFIG_DIR / "v2_attack_rules.json"
TROOP_PROFILES_FILE = CONFIG_DIR / "v2_troop_profiles.json"
SPELL_PROFILES_FILE = CONFIG_DIR / "v2_spell_profiles.json"


class _ConfigLoader:
    """JSON config holder with mtime-based hot reload."""

    def __init__(self) -> None:
        self._attack_rules: dict = {}
        self._troop_profiles: dict = {}
        self._spell_profiles: dict = {}
        self._mtimes: dict[str, float] = {}
        self.reload(force=True)

    def reload(self, force: bool = False) -> bool:
        changed = False
        for path, attr in (
            (ATTACK_RULES_FILE,   "_attack_rules"),
            (TROOP_PROFILES_FILE, "_troop_profiles"),
            (SPELL_PROFILES_FILE, "_spell_profiles"),
        ):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                if force:
                    setattr(self, attr, {})
                    changed = True
                continue
            prev = self._mtimes.get(str(path), 0.0)
            if force or mtime > prev:
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    setattr(self, attr, data)
                    self._mtimes[str(path)] = mtime
                    changed = True
                    log.info("V2 config loaded: %s (%d keys)", path.name, len(data))
                except Exception as exc:
                    log.error("V2 config load failed for %s: %s", path, exc)
        return changed

    @property
    def attack_rules(self) -> dict:
        return self._attack_rules

    @property
    def troop_profiles(self) -> dict:
        return self._troop_profiles

    @property
    def spell_profiles(self) -> dict:
        return self._spell_profiles


class V2Orchestrator:
    """Composes Skills + Rules and runs an attack."""

    def __init__(self, screen_reader: ScreenReader) -> None:
        self._sr = screen_reader
        self._config = _ConfigLoader()
        self._skills = self._build_skills()
        self._rules = self._build_rules()

    def reload_config(self) -> bool:
        return self._config.reload(force=True)

    def attack_rules(self) -> dict:
        return self._config.attack_rules

    def troop_profiles(self) -> dict:
        return self._config.troop_profiles

    def spell_profiles(self) -> dict:
        return self._config.spell_profiles

    def available_rules(self) -> list[str]:
        return [r.name for r in self._rules]

    def execute(
        self,
        screenshot: np.ndarray,
        profile: dict,
        mode_key: str,
        engine,
    ) -> bool:
        """Run V2 attack. Returns True if a rule executed (success or
        partial); False if nothing fit so the caller should fall back to
        legacy V36."""
        self._config.reload(force=False)

        s = Settings()
        manual_rule = str(s.get(f"v2_rule_{mode_key}", "auto"))
        v2_mode = str(s.get(f"v2_mode_{mode_key}", "smart"))
        target_key = str(s.get(f"v2_target_{mode_key}", ""))
        decoration_wait = float(s.get("v2_decoration_wait", 5.0))

        log.info(
            "V2 START | mode=%s key=%s rule=%s target=%s",
            v2_mode, mode_key, manual_rule, target_key or "—",
        )

        if decoration_wait > 0:
            self._sleep_with_interrupt(engine, decoration_wait)
            ss2 = screencap()
            if ss2 is not None:
                screenshot = ss2

        ui_cutoff = self._sr.get_ui_cutoff(screenshot.shape[0])
        polygon = self._skills.red_zone.detect(
            screenshot, ui_cutoff, self._config.attack_rules,
        )
        if polygon is None:
            log.warning(
                "V2 polygon detection FAILED (HSV + inversion both rejected) "
                "— V2 gives up on this base.",
            )
            return False
        base_centroid = self._skills.red_zone.centroid(polygon)

        rule = self._select_rule(
            v2_mode=v2_mode,
            manual_rule=manual_rule,
            target_key=target_key,
            profile=profile,
            screenshot=screenshot,
            mode_key=mode_key,
        )
        if rule is None:
            log.warning("V2 no rule matched — V2 gives up on this base.")
            return False

        ctx = AttackContext(
            screenshot=screenshot,
            profile=profile,
            config=self._config.attack_rules,
            troop_profiles=self._config.troop_profiles,
            spell_profiles=self._config.spell_profiles,
            skills=self._skills,
            mode_key=mode_key,
            target_key=target_key,
            ui_cutoff=ui_cutoff,
            engine=engine,
            polygon=polygon,
            base_centroid=base_centroid,
        )

        log.info("V2 RULE → %s", rule.name)
        try:
            ok = bool(rule.execute(ctx))
        except Exception as exc:
            log.error("V2 rule '%s' raised: %s", rule.name, exc, exc_info=True)
            return False

        if not ok:
            # Last-chance chain: if the chosen rule was NOT SmartDefault,
            # try SmartDefault before bailing to V36. ResourceRaid already
            # chains internally; this catches Air/Funnel/THSnipe falling
            # short (e.g. target not found).
            if not isinstance(rule, SmartDefaultRule):
                fallback = self._find(SmartDefaultRule)
                if fallback is not None and fallback is not rule:
                    log.info("V2 rule '%s' returned False — chaining to SmartDefault.", rule.name)
                    try:
                        ok = bool(fallback.execute(ctx))
                    except Exception as exc:
                        log.error(
                            "V2 SmartDefault fallback raised: %s", exc, exc_info=True,
                        )
                        return False
            if not ok:
                log.warning("V2 rule chain exhausted — V2 gives up on this base.")
                return False

        self._sweep_up(ctx)

        log.info("V2 END   rule=%s", rule.name)
        return True

    # ── Sweep-up ────────────────────────────────────────────────────────
    # Rules deploy to a *plan*, not to a result: Ring Sweep holds each side
    # for a fixed window, and a window shorter than the army leaves troops
    # on the card (a held card empties at roughly 7 per second). Cards the
    # rule could not find are skipped outright. Either way the leftovers are
    # troops already paid for that never fought.
    #
    # So once the rule is done, look at the bar again and empty whatever is
    # still sitting there.

    def _sweep_up(self, ctx) -> None:
        if not bool(Settings().get("sweep_up_enabled", False)):
            return

        cfg = ctx.config
        sweep = cfg.get("sweep_up", {}) or {}
        max_rounds = max(1, int(sweep.get("max_rounds", 2)))
        hold_ms = max(500, int(sweep.get("hold_ms", 4000)))

        drops = self._sweep_up_points(ctx)
        if not drops:
            log.warning("Sweep-up: no safe drop point from the polygon — skipped.")
            return

        troops = ctx.profile.get(
            "bb_selected_troops" if ctx.mode_key == "bb" else "selected_troops", [],
        ) or []

        for round_no in range(1, max_rounds + 1):
            if self._is_interrupted(ctx.engine):
                return
            shot = screencap()
            if shot is None:
                log.warning("Sweep-up: no screenshot — stopping.")
                return

            leftovers = []
            for troop in troops:
                card = ctx.skills.target.find_one(shot, troop)
                if card is None:
                    # A card the bar no longer shows at all is empty enough.
                    continue
                if ctx.skills.card.has_troops_left(shot, card, cfg, troop):
                    leftovers.append((troop, card))

            if not leftovers:
                log.info("Sweep-up: round %d found nothing left. Done.", round_no)
                return

            log.info(
                "Sweep-up: round %d/%d emptying %s",
                round_no, max_rounds, ", ".join(t for t, _ in leftovers),
            )
            for index, (troop, card) in enumerate(leftovers):
                if self._is_interrupted(ctx.engine):
                    return
                # Spread the rounds over the ring instead of piling every
                # leftover on one spot.
                x, y = drops[(round_no + index) % len(drops)]
                ctx.skills.touch.tap(card[0], card[1], cfg)
                ctx.skills.touch.pre_select_settle(cfg)
                ctx.skills.touch.long_press(x, y, hold_ms, cfg)

            self._restamp_post_deploy(ctx)

        log.info(
            "Sweep-up: stopped after %d round(s) — cards may still read as "
            "full. Check the sat/val numbers above against config.sweep_up.",
            max_rounds,
        )

    @staticmethod
    def _sweep_up_points(ctx) -> list[tuple[int, int]]:
        """Where leftovers may land: the same ring the sweep rules use.

        Built from the red-zone polygon, so it is valid whichever rule just
        ran — nothing here assumes Ring Sweep was the one that deployed.
        """
        if ctx.polygon is None:
            return []
        screen_w = ctx.screenshot.shape[1]
        ring = ctx.skills.ring.plan(ctx.polygon, screen_w, ctx.ui_cutoff, ctx.config)
        if not ring:
            return []
        centre = ctx.base_centroid or (screen_w // 2, ctx.ui_cutoff // 2)
        return list(ctx.skills.ring.one_point_per_side(centre, ring)) or list(ring[:1])

    @staticmethod
    def _restamp_post_deploy(ctx) -> None:
        """Push the deploy countdown back to now.

        The rule stamped it when *it* finished; without this, every second
        the sweep-up spends is a second stolen from ``deploy_timer_seconds``
        and the battle ends earlier than the user asked for.
        """
        engine = ctx.engine
        if engine is None:
            return
        try:
            engine._home_logic._post_deploy_time = time.time()
        except Exception:
            pass

    def _build_skills(self) -> SkillBundle:
        target = TargetLocatorSkill(self._sr)
        return SkillBundle(
            red_zone = RedZonePolygonSkill(),
            iso_grid = IsometricGridSkill(),
            corridor = SafeCorridorSkill(),
            obstacle = ObstacleDetectorSkill(),
            target   = target,
            corner   = CornerSelectorSkill(target),
            card     = CardStateSkill(),
            touch    = HumanTouchSkill(),
            fan      = FanPlannerSkill(),
            funnel   = FunnelPlannerSkill(),
            spell    = SpellPlannerSkill(target),
            hero     = HeroPlannerSkill(),
            perimeter = PerimeterPlannerSkill(),
            ring     = RingSweepPlannerSkill(),
        )

    def _build_rules(self) -> list[AttackRule]:
        rules = [
            ResourceRaidRule(),
            THSnipeRule(),
            AirAttackRule(),
            GroundFunnelRule(),
            PerimeterSweepRule(),
            RingSweepRule(),
            SmartDefaultRule(),
        ]
        rules.sort(key=lambda r: r.priority)
        return rules

    def _select_rule(
        self,
        v2_mode: str,
        manual_rule: str,
        target_key: str,
        profile: dict,
        screenshot: np.ndarray,
        mode_key: str = "hv",
    ) -> Optional[AttackRule]:
        manual_rule = (manual_rule or "auto").lower()
        if manual_rule != "auto":
            for r in self._rules:
                if r.name == manual_rule:
                    return r
            log.warning("V2 manual rule '%s' unknown — falling back to auto.", manual_rule)

        if v2_mode == "storage":
            return self._find(ResourceRaidRule)
        if v2_mode == "building" and target_key:
            return self._find(THSnipeRule)

        troops_key = "bb_selected_troops" if mode_key == "bb" else "selected_troops"
        selected_troops = list(
            profile.get(troops_key, []) or profile.get("selected_troops", [])
        )

        scout_present = any(
            self._config.troop_profiles.get(t, {}).get("style") == "scout_pairs"
            for t in selected_troops
        )
        if scout_present and target_key:
            return self._find(ResourceRaidRule)

        air_count = sum(
            1 for t in selected_troops
            if self._config.troop_profiles.get(t, {}).get("kind") == "air"
        )
        ground_count = sum(
            1 for t in selected_troops
            if self._config.troop_profiles.get(t, {}).get("kind") == "ground"
        )
        if air_count > 0 and air_count >= ground_count:
            return self._find(AirAttackRule)
        if ground_count > 0:
            funnel_present = any(
                self._config.troop_profiles.get(t, {}).get("style") == "funnel"
                for t in selected_troops
            )
            if funnel_present:
                return self._find(GroundFunnelRule)
            return self._find(SmartDefaultRule)

        return self._find(SmartDefaultRule)

    def _find(self, rule_class) -> Optional[AttackRule]:
        for r in self._rules:
            if isinstance(r, rule_class):
                return r
        return None

    @staticmethod
    def _sleep_with_interrupt(engine, seconds: float) -> None:
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if V2Orchestrator._is_interrupted(engine):
                return
            time.sleep(0.20)

    @staticmethod
    def _is_interrupted(engine) -> bool:
        if engine is None:
            return False
        return (not getattr(engine, "_running", False)) or getattr(engine, "_paused", False)
