import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[1] / "logic" / "skills" / "spell_planner.py"

logger_stub = types.ModuleType("core.logger")
logger_stub.BotLogger = types.SimpleNamespace(get=lambda _name: object())

target_locator_stub = types.ModuleType("vision.skills.target_locator")
target_locator_stub.TargetLocatorSkill = object


class _RedZonePolygonSkill:
    @staticmethod
    def bbox(polygon):
        points = np.asarray(polygon).reshape(-1, 2)
        x_lo, y_lo = points.min(axis=0)
        x_hi, y_hi = points.max(axis=0)
        return int(x_lo), int(y_lo), int(x_hi - x_lo + 1), int(y_hi - y_lo + 1)

    @staticmethod
    def centroid(polygon):
        points = np.asarray(polygon).reshape(-1, 2)
        return tuple(np.rint(points.mean(axis=0)).astype(int))

    @staticmethod
    def is_inside(polygon, x, y):
        points = np.asarray(polygon).reshape(-1, 2)
        return bool(
            points[:, 0].min() <= x <= points[:, 0].max()
            and points[:, 1].min() <= y <= points[:, 1].max()
        )


red_zone_stub = types.ModuleType("vision.skills.red_zone_polygon")
red_zone_stub.RedZonePolygonSkill = _RedZonePolygonSkill

SPEC = importlib.util.spec_from_file_location("spell_planner_for_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
with patch.dict(
    sys.modules,
    {
        "core.logger": logger_stub,
        "vision.skills.red_zone_polygon": red_zone_stub,
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

    def test_inside_base_random_returns_100_varied_interior_points(self):
        planner = MODULE.SpellPlannerSkill(target_locator_stub.TargetLocatorSkill())
        polygon = np.array(
            [[100, 100], [500, 100], [500, 500], [100, 500]],
            dtype=np.int32,
        )
        MODULE.random.seed(12345)

        drops = planner.plan_spell(
            screenshot=None,
            spell_name="totem_spell",
            cluster_xy=(700, 300),
            target_xy=(300, 300),
            spell_profiles={
                "totem_spell": {
                    "placement": "inside_base_random",
                    "inner_scale": 0.8,
                    "drop_count": 100,
                }
            },
            base_polygon=polygon,
        )

        self.assertEqual(100, len(drops))
        self.assertGreater(len(set(drops)), 90)
        self.assertTrue(all(140 <= x <= 460 and 140 <= y <= 460 for x, y in drops))

    def test_follow_army_path_orders_random_points_from_entry_to_core(self):
        planner = MODULE.SpellPlannerSkill(target_locator_stub.TargetLocatorSkill())
        polygon = np.array(
            [[100, 100], [500, 100], [500, 500], [100, 500]],
            dtype=np.int32,
        )
        MODULE.random.seed(54321)

        drops = planner.plan_spell(
            screenshot=None,
            spell_name="totem_spell",
            cluster_xy=(700, 300),
            target_xy=(300, 300),
            spell_profiles={
                "totem_spell": {
                    "placement": "follow_army_path",
                    "inner_scale": 0.95,
                    "drop_count": 100,
                }
            },
            base_polygon=polygon,
        )

        self.assertEqual(100, len(drops))
        self.assertGreater(len(set(drops)), 90)
        self.assertTrue(all(110 <= x <= 490 and 110 <= y <= 490 for x, y in drops))
        self.assertEqual(sorted((x for x, _y in drops), reverse=True), [x for x, _y in drops])


if __name__ == "__main__":
    unittest.main()
