import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "logic" / "rules" / "air_attack_rule.py"


class _AttackRule:
    @staticmethod
    def _selected_spells(ctx):
        return list(ctx.profile.get("selected_spells", []))

    @staticmethod
    def _selected_heroes(ctx):
        return list(ctx.profile.get("selected_heroes", []))

    @staticmethod
    def _interrupted(_ctx):
        return False


base_rule_stub = types.ModuleType("logic.rules.base_rule")
base_rule_stub.AttackRule = _AttackRule
base_rule_stub.AttackContext = object

adb_stub = types.ModuleType("core.adb_handler")
adb_stub.screencap = lambda: None

logger_stub = types.ModuleType("core.logger")
logger_stub.BotLogger = types.SimpleNamespace(
    get=lambda _name: types.SimpleNamespace(
        info=lambda *_args: None,
        warning=lambda *_args: None,
    ),
)

corridor_stub = types.ModuleType("vision.skills.safe_corridor")
corridor_stub.SafeCorridorSkill = object

SPEC = importlib.util.spec_from_file_location("air_attack_rule_for_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
with patch.dict(
    sys.modules,
    {
        "logic.rules.base_rule": base_rule_stub,
        "core.adb_handler": adb_stub,
        "core.logger": logger_stub,
        "vision.skills.safe_corridor": corridor_stub,
    },
):
    SPEC.loader.exec_module(MODULE)


class _Touch:
    def __init__(self):
        self.taps = []

    def tap(self, x, y, _config):
        self.taps.append((x, y))

    def pre_select_settle(self, _config):
        pass

    def post_deploy_settle(self, _config):
        pass


class SpellDeploymentTest(unittest.TestCase):
    def test_selects_totem_once_then_taps_all_100_drop_points(self):
        touch = _Touch()
        drops = [(200 + index, 300) for index in range(100)]
        target = types.SimpleNamespace(
            expand_prefix=lambda _spell: ["totem_spell"],
            find_first_of=lambda _ss, _candidates: ("totem_spell", 800, 950),
        )
        spell = types.SimpleNamespace(plan_spell=lambda *_args: drops)
        ctx = types.SimpleNamespace(
            profile={"selected_spells": ["totem_spell"]},
            screenshot=object(),
            config={},
            spell_profiles={"totem_spell": {"drop_count": 100}},
            polygon=None,
            skills=types.SimpleNamespace(target=target, spell=spell, touch=touch),
        )

        MODULE.AirAttackRule()._deploy_spells(ctx, (100, 100), (500, 500))

        self.assertEqual((800, 950), touch.taps[0])
        self.assertEqual(drops, touch.taps[1:])
        self.assertEqual(101, len(touch.taps))

    def test_totem_drops_in_five_waves_over_first_90_seconds(self):
        clock = [0.0]

        class _TimedTouch(_Touch):
            def __init__(self):
                super().__init__()
                self.drop_times = []

            def tap(self, x, y, config):
                super().tap(x, y, config)
                if y == 300:
                    self.drop_times.append(clock[0])

        touch = _TimedTouch()
        drops = [(200 + index, 300) for index in range(100)]
        target = types.SimpleNamespace(
            expand_prefix=lambda _spell: ["totem_spell"],
            find_first_of=lambda _ss, _candidates: ("totem_spell", 800, 950),
        )
        spell = types.SimpleNamespace(plan_spell=lambda *_args: drops)
        ctx = types.SimpleNamespace(
            profile={"selected_spells": ["totem_spell"]},
            screenshot=object(),
            config={},
            spell_profiles={
                "totem_spell": {
                    "drops_per_wave": 20,
                    "wave_interval_sec": 18,
                    "drop_interval_ms": 0,
                }
            },
            polygon=None,
            skills=types.SimpleNamespace(target=target, spell=spell, touch=touch),
        )

        def advance(seconds):
            clock[0] += seconds

        with patch.object(MODULE.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(MODULE.time, "sleep", side_effect=advance):
            MODULE.AirAttackRule()._deploy_spells(ctx, (100, 100), (500, 500))

        wave_starts = [touch.drop_times[index] for index in (0, 20, 40, 60, 80)]
        self.assertEqual([0.0, 18.0, 36.0, 54.0, 72.0], wave_starts)

    @patch.object(MODULE.time, "sleep", return_value=None)
    def test_dragon_cycles_fan_until_configured_deploy_taps(self, _sleep):
        touch = _Touch()
        target = types.SimpleNamespace(find_one=lambda _ss, _troop: (300, 950))
        ctx = types.SimpleNamespace(
            screenshot=object(),
            config={},
            troop_profiles={
                "dragon": {
                    "style": "fan",
                    "stagger_ms": 0,
                    "deploy_taps": 50,
                }
            },
            skills=types.SimpleNamespace(target=target, touch=touch),
        )
        fan_points = [(100, 200), (120, 220), (140, 240)]

        MODULE.AirAttackRule()._deploy_air_troops(
            ctx, ["dragon"], fan_points, fan_points[1],
        )

        self.assertEqual((300, 950), touch.taps[0])
        self.assertEqual(50, len(touch.taps[1:]))
        self.assertEqual(fan_points * 16 + fan_points[:2], touch.taps[1:])

    def test_hero_jitter_inside_red_zone_falls_back_to_safe_cluster(self):
        touch = _Touch()
        polygon = [(0, 0), (1, 0), (1, 1)]
        red_zone = types.SimpleNamespace(
            is_inside=lambda _polygon, x, _y, margin=0: x + margin >= 500,
        )
        hero = types.SimpleNamespace(
            plan_drops=lambda _cluster, _cards: [
                ("queen", (300, 950), (510, 400)),
            ],
        )
        target = types.SimpleNamespace(find_one=lambda _ss, _hero: (300, 950))
        ctx = types.SimpleNamespace(
            screenshot=object(),
            profile={"selected_heroes": ["queen"]},
            mode_key="hv",
            config={},
            polygon=polygon,
            skills=types.SimpleNamespace(
                red_zone=red_zone,
                hero=hero,
                target=target,
                touch=touch,
            ),
        )

        memory = MODULE.AirAttackRule()._deploy_heroes(ctx, (400, 400), [])

        self.assertEqual([(300, 950), (400, 400)], touch.taps)
        self.assertEqual((400, 400), memory[0][2])

    @patch.object(MODULE.time, "sleep", return_value=None)
    def test_air_troop_tap_inside_red_zone_is_skipped(self, _sleep):
        touch = _Touch()
        red_zone = types.SimpleNamespace(
            is_inside=lambda _polygon, x, _y, margin=0: x + margin >= 500,
        )
        target = types.SimpleNamespace(find_one=lambda _ss, _troop: (300, 950))
        ctx = types.SimpleNamespace(
            screenshot=object(),
            config={},
            polygon=[(0, 0), (1, 0), (1, 1)],
            troop_profiles={
                "dragon": {"style": "fan", "stagger_ms": 0, "deploy_taps": 2},
            },
            skills=types.SimpleNamespace(
                red_zone=red_zone,
                target=target,
                touch=touch,
            ),
        )

        MODULE.AirAttackRule()._deploy_air_troops(
            ctx, ["dragon"], [(400, 200), (510, 200)], (400, 200),
        )

        self.assertEqual([(300, 950), (400, 200)], touch.taps)


if __name__ == "__main__":
    unittest.main()
