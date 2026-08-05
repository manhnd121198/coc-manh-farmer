"""Fan drops belong on the map rim, along the side — not strung across
the gap between the base and the screen edge.

Two separate things are pinned here:
  1. Orientation comes from the side name. A left/right corridor can be
     wider than it is tall (579x546 on 16:9); reading that as
     "horizontal" ran the fan from the base outwards and put the first
     drops on the decorations hugging the base.
  2. ``edge_bias`` slides the fan across the corridor to the outer rim,
     which is the part of the map that cannot hold buildings.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "logic" / "skills" / "fan_planner.py"


class _SafeCorridorStub:
    @staticmethod
    def center(rect):
        x, y, w, h = rect
        return int(x + w / 2), int(y + h / 2)

    @staticmethod
    def is_horizontal(rect):
        return rect[2] >= rect[3]


corridor_stub = types.ModuleType("vision.skills.safe_corridor")
corridor_stub.SafeCorridorSkill = _SafeCorridorStub
corridor_stub.Rect = tuple

logger_stub = types.ModuleType("core.logger")
logger_stub.BotLogger = types.SimpleNamespace(
    get=lambda _name: types.SimpleNamespace(debug=lambda *_a: None, info=lambda *_a: None),
)

SPEC = importlib.util.spec_from_file_location("fan_planner_for_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
with patch.dict(sys.modules, {
    "vision.skills.safe_corridor": corridor_stub,
    "core.logger": logger_stub,
}):
    SPEC.loader.exec_module(MODULE)

FanPlanner = MODULE.FanPlannerSkill

# Straight from a real run: right corridor of a 1920x1080 screen.
RIGHT = (1281, 110, 579, 546)
LEFT = (60, 110, 299, 546)
TOP = (60, 110, 1800, 200)


class OrientationTest(unittest.TestCase):
    def test_side_corridor_fans_along_the_side_even_when_wider_than_tall(self):
        self.assertTrue(_SafeCorridorStub.is_horizontal(RIGHT), "precondition")

        points = FanPlanner().plan(RIGHT, count=9, side="right")

        self.assertEqual(1, len({x for x, _ in points}), "x must stay fixed")
        self.assertEqual(9, len({y for _, y in points}), "y must spread")

    def test_top_and_bottom_corridors_fan_across(self):
        points = FanPlanner().plan(TOP, count=5, side="top")

        self.assertEqual(1, len({y for _, y in points}))
        self.assertEqual(5, len({x for x, _ in points}))

    def test_without_a_side_the_old_aspect_rule_still_applies(self):
        points = FanPlanner().plan(RIGHT, count=9)

        self.assertEqual(1, len({y for _, y in points}))
        self.assertEqual(9, len({x for x, _ in points}))


class EdgeBiasTest(unittest.TestCase):
    def test_bias_zero_keeps_the_corridor_centre(self):
        points = FanPlanner().plan(RIGHT, count=9, side="right", edge_bias=0.0)

        self.assertEqual(1281 + 579 // 2, points[0][0])

    def test_bias_one_pushes_to_the_outer_rim(self):
        points = FanPlanner().plan(
            RIGHT, count=9, side="right", edge_bias=1.0, edge_margin_px=60,
        )

        self.assertEqual(1281 + 579 - 60, points[0][0])

    def test_left_corridor_rim_is_the_low_side(self):
        points = FanPlanner().plan(
            LEFT, count=9, side="left", edge_bias=1.0, edge_margin_px=60,
        )

        self.assertEqual(60 + 60, points[0][0])

    def test_half_bias_lands_between_centre_and_rim(self):
        centre = FanPlanner().plan(RIGHT, count=9, side="right", edge_bias=0.0)[0][0]
        rim = FanPlanner().plan(
            RIGHT, count=9, side="right", edge_bias=1.0, edge_margin_px=60)[0][0]

        half = FanPlanner().plan(
            RIGHT, count=9, side="right", edge_bias=0.5, edge_margin_px=60)[0][0]

        self.assertEqual(round(centre + (rim - centre) * 0.5), half)

    def test_an_oversized_margin_never_crosses_the_centre_line(self):
        # margin bigger than the corridor would otherwise flip the drops
        # to the wrong side — straight into the base.
        points = FanPlanner().plan(
            RIGHT, count=9, side="right", edge_bias=1.0, edge_margin_px=5000,
        )

        self.assertGreaterEqual(points[0][0], 1281 + 579 // 2)

    def test_bias_is_ignored_without_a_side(self):
        points = FanPlanner().plan(RIGHT, count=9, edge_bias=1.0)

        self.assertEqual(110 + 546 // 2, points[0][1])

    def test_bias_is_clamped_to_the_unit_range(self):
        rim = FanPlanner().plan(
            RIGHT, count=9, side="right", edge_bias=1.0, edge_margin_px=60)[0][0]

        over = FanPlanner().plan(
            RIGHT, count=9, side="right", edge_bias=9.0, edge_margin_px=60)[0][0]
        under = FanPlanner().plan(
            RIGHT, count=9, side="right", edge_bias=-3.0, edge_margin_px=60)[0][0]

        self.assertEqual(rim, over)
        self.assertEqual(1281 + 579 // 2, under)


if __name__ == "__main__":
    unittest.main()
