import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "logic" / "skills" / "perimeter_planner.py"
SPEC = importlib.util.spec_from_file_location("perimeter_planner", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
PerimeterPlannerSkill = MODULE.PerimeterPlannerSkill


class _FixedRandom:
    def randrange(self, size):
        return 2

    def choice(self, values):
        return -1


class PerimeterPlannerSkillTest(unittest.TestCase):
    def setUp(self):
        self.corridors = {
            "left": (0, 0, 100, 600),
            "right": (900, 0, 100, 600),
            "top": (0, 0, 1000, 100),
            "bottom": (0, 500, 1000, 100),
        }

    def test_plans_closed_clockwise_route_on_safe_corridor_centres(self):
        route = PerimeterPlannerSkill.plan(
            self.corridors,
            {"perimeter_sweep": {"points_per_side": 2}},
        )

        self.assertEqual(
            [(50, 50), (500, 50), (950, 50), (950, 300),
             (950, 550), (500, 550), (50, 550), (50, 300)],
            route,
        )

    def test_randomizes_start_and_direction_without_changing_points(self):
        route = PerimeterPlannerSkill.plan(self.corridors)

        randomized = PerimeterPlannerSkill.randomize_route(route, _FixedRandom())

        self.assertEqual(route[2], randomized[0])
        self.assertEqual(route[1], randomized[1])
        self.assertCountEqual(route, randomized)

    def test_requires_all_four_safe_edges(self):
        self.corridors.pop("top")

        self.assertEqual([], PerimeterPlannerSkill.plan(self.corridors))

    def test_keeps_top_swipes_below_the_ui_exclusion(self):
        route = PerimeterPlannerSkill.plan(
            self.corridors,
            {"polygon": {"top_ui_exclude_px": 70}, "tap_jitter_px": 10},
        )

        self.assertTrue(route)
        self.assertEqual(80, min(y for _, y in route))


if __name__ == "__main__":
    unittest.main()
