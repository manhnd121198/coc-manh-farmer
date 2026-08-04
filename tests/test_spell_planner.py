import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "logic" / "skills" / "spell_planner.py"

logger_stub = types.ModuleType("core.logger")
logger_stub.BotLogger = types.SimpleNamespace(get=lambda _name: object())

target_locator_stub = types.ModuleType("vision.skills.target_locator")
target_locator_stub.TargetLocatorSkill = object

SPEC = importlib.util.spec_from_file_location("spell_planner_for_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
with patch.dict(
    sys.modules,
    {
        "core.logger": logger_stub,
        "vision.skills.target_locator": target_locator_stub,
    },
):
    SPEC.loader.exec_module(MODULE)


class SpellPlannerTest(unittest.TestCase):
    @patch.object(MODULE.random, "randint", return_value=0)
    def test_on_army_moves_drop_points_inside_toward_target(self, _randint):
        planner = MODULE.SpellPlannerSkill(target_locator_stub.TargetLocatorSkill())

        drops = planner.plan_spell(
            screenshot=None,
            spell_name="totem_spell",
            cluster_xy=(1700, 350),
            target_xy=(900, 550),
            spell_profiles={
                "totem_spell": {
                    "placement": "on_army",
                    "path_fraction": 0.5,
                    "drop_count": 100,
                }
            },
        )

        self.assertEqual(100, len(drops))
        self.assertEqual({(1300, 450)}, set(drops))


if __name__ == "__main__":
    unittest.main()
