"""Tests for the Ring Sweep mode — even drops around all 4 sides of a base.

The contract that matters in-game:
  * every planned point must sit in the grass corridor between the outer
    and inner red lines,
  * points must stay inside the playfield (off-screen taps are discarded by
    Android and silently lose troops),
  * coverage must be balanced across the four sides,
  * a non-convex base (cross / T shaped) must still work,
  * unlike PerimeterSweep it must NOT require all four screen-edge corridors.
"""

import collections
import json
import pathlib
import unittest

try:
    import numpy as np
    import cv2  # noqa: F401
    _HAVE_CV2 = True
except ImportError:  # pragma: no cover
    _HAVE_CV2 = False


CFG = {
    "ring_sweep": {
        "corridor_width_px": 40,
        "boundary_margin_px": 5,
        "points_per_side": 4,
        "edge_margin_px": 60,
        "min_valid_points": 6,
    },
    "polygon": {"top_ui_exclude_px": 150},
}

SCREEN_W, UI_CUTOFF = 1920, 838
EDGE, TOP = 60, 150
BOTTOM = UI_CUTOFF - EDGE


def _square():
    # Mirrors a real detected base from the logs: bbox (364,150,1102,585).
    return np.array(
        [[364, 200], [1466, 200], [1466, 735], [364, 735]], dtype=np.int32,
    )


def _diamond():
    """A real CoC base as it reads on screen: an isometric diamond.

    Taken from a live 1350x1080 capture (centroid 686,501).
    """
    return np.array(
        [[686, 150], [1136, 520], [780, 866], [195, 500]], dtype=np.int32,
    )


def _sharp_diamond():
    """Convex outline shaped like the live dump that exposed corner spikes."""
    return np.array(
        [[620, 150], [780, 150], [1315, 530], [785, 950],
         [615, 950], [65, 525]], dtype=np.int32,
    )


def _cross():
    return np.array(
        [[600, 200], [900, 200], [900, 350], [1200, 350], [1200, 600],
         [900, 600], [900, 750], [600, 750], [600, 600], [300, 600],
         [300, 350], [600, 350]], dtype=np.int32,
    )


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class RingSweepPlannerTest(unittest.TestCase):
    def setUp(self):
        from logic.skills.ring_sweep_planner import RingSweepPlannerSkill
        from vision.skills.red_zone_polygon import RedZonePolygonSkill
        self.plan = RingSweepPlannerSkill()
        self.rz = RedZonePolygonSkill

    def _ring(self, polygon, screen_w=SCREEN_W):
        return self.plan.plan(polygon, screen_w, UI_CUTOFF, CFG)

    def _blocked_samples(self, polygon, a, b, samples=40):
        inner = self.plan.inner_polygon
        return sum(
            (not self.rz.is_inside(polygon, x, y))
            or self.rz.is_inside(inner, x, y)
            for k in range(1, samples)
            for x, y in [(
                int(round(a[0] + (b[0] - a[0]) * k / samples)),
                int(round(a[1] + (b[1] - a[1]) * k / samples)),
            )]
        )

    def test_deployable_arcs_never_run_through_the_base(self):
        """Points are each valid; the straight leg between them may not be.

        Wherever a point is rejected the route has a gap, and the shortcut
        across it can run over the base. Dragging through the no-deploy
        area deploys nothing, so those legs must be excluded.
        """
        for polygon in (_square(), _cross()):
            ring = self._ring(polygon)
            arcs = self.plan.deployable_arcs(polygon, ring)
            self.assertTrue(arcs, "a usable base must still yield arcs")
            for arc in arcs:
                self.assertGreaterEqual(len(arc), 2, "an arc needs a path")
                for start, end in zip(arc, arc[1:]):
                    self.assertEqual(
                        0, self._blocked_samples(polygon, start, end),
                        f"leg {start}->{end} leaves the deploy corridor",
                    )

    def test_corridor_route_keeps_usable_arcs(self):
        """Skipping a corner may split the lap, but must not erase it."""
        polygon = _square()
        ring = self._ring(polygon)
        arcs = self.plan.deployable_arcs(polygon, ring)
        self.assertTrue(arcs)
        self.assertTrue(all(len(arc) >= 2 for arc in arcs))

    def test_every_point_lands_between_the_two_red_lines(self):
        for polygon in (_square(), _cross()):
            ring = self._ring(polygon)
            self.assertTrue(ring, "planner produced no points")
            inner = self.plan.inner_polygon
            for x, y in ring:
                self.assertTrue(self.rz.is_inside(polygon, x, y))
                self.assertFalse(self.rz.is_inside(inner, x, y))

    def test_inset_does_not_cross_itself_at_sharp_corners(self):
        self.plan.plan(_sharp_diamond(), 1350, 983, CFG)
        inner = self.plan.inner_polygon.reshape((-1, 1, 2))
        self.assertTrue(
            cv2.isContourConvex(inner),
            f"inner corridor boundary crossed itself: {inner.tolist()}",
        )

    def test_points_stay_inside_the_playfield(self):
        for polygon in (_square(), _cross()):
            for x, y in self._ring(polygon):
                self.assertGreaterEqual(x, EDGE)
                self.assertLessEqual(x, SCREEN_W - EDGE)
                self.assertGreaterEqual(y, TOP)
                self.assertLessEqual(y, BOTTOM)

    def test_all_four_sides_are_covered_evenly(self):
        """A diamond base must spread its drops over all four sides."""
        polygon = _diamond()
        ring = self.plan.plan(polygon, 1350, 950, CFG)
        centre = self.rz.centroid(polygon)
        counts = {}
        for point in ring:
            side = self.plan.side_of(centre, point)
            counts[side] = counts.get(side, 0) + 1

        self.assertEqual({"top", "bottom", "left", "right"}, set(counts))
        # Real bases are never perfectly symmetric, so the contract is
        # "no side is starved and none hogs the army", not an exact split.
        self.assertLessEqual(
            max(counts.values()), len(ring) / 2.0,
            f"one side is hogging the drops: {counts}",
        )
        self.assertGreaterEqual(
            min(counts.values()), 2, f"a side is starved: {counts}",
        )

    def test_rectangular_base_still_reaches_every_side(self):
        """A wide rectangle puts more perimeter on its long sides.

        Coverage stays complete; it just is not equal, because the route
        follows the shape of the base rather than sweeping fixed angles.
        """
        polygon = _square()
        centre = self.rz.centroid(polygon)
        self.assertEqual(
            {"top", "bottom", "left", "right"},
            self.plan.sides_covered(centre, self._ring(polygon)),
        )

    def test_non_convex_base_is_supported(self):
        polygon = _cross()
        ring = self._ring(polygon)
        centre = self.rz.centroid(polygon)
        self.assertEqual(
            {"top", "bottom", "left", "right"},
            self.plan.sides_covered(centre, ring),
        )

    def test_works_on_a_narrow_1350_panel(self):
        polygon = np.array(
            [[300, 200], [1050, 200], [1050, 700], [300, 700]], dtype=np.int32,
        )
        ring = self.plan.plan(polygon, 1350, UI_CUTOFF, CFG)
        self.assertGreaterEqual(len(ring), CFG["ring_sweep"]["min_valid_points"])
        for x, y in ring:
            self.assertLessEqual(x, 1350 - EDGE)
            self.assertTrue(self.rz.is_inside(polygon, x, y))
            self.assertFalse(self.rz.is_inside(self.plan.inner_polygon, x, y))

    def test_missing_polygon_is_handled(self):
        self.assertEqual([], self.plan.plan(None, SCREEN_W, UI_CUTOFF, CFG))
        tiny = np.array([[10, 10], [20, 20]], dtype=np.int32)
        self.assertEqual([], self.plan.plan(tiny, SCREEN_W, UI_CUTOFF, CFG))

    def test_route_randomisation_preserves_every_point(self):
        ring = self._ring(_square())
        route = self.plan.randomize_route(ring)
        self.assertEqual(len(ring), len(route))
        self.assertEqual(set(ring), set(route))
        self.assertEqual([], self.plan.randomize_route([]))


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class RingSweepSideDropsTest(unittest.TestCase):
    """One random drop per side is what the attack actually holds."""

    def setUp(self):
        from logic.skills.ring_sweep_planner import RingSweepPlannerSkill
        self.plan = RingSweepPlannerSkill()
        self.centre = (675, 450)
        self.ring = [
            (675, 150), (600, 180),      # top
            (975, 450), (940, 500),      # right
            (675, 750), (700, 720),      # bottom
            (375, 450), (410, 400),      # left
        ]

    def test_one_drop_per_side_and_no_side_repeated(self):
        drops = self.plan.one_point_per_side(self.centre, self.ring)
        sides = [self.plan.side_of(self.centre, d) for d in drops]
        self.assertEqual(4, len(drops))
        self.assertEqual(sorted(sides), sorted(set(sides)))
        for drop in drops:
            self.assertIn(drop, self.ring, "drops must come from the ring")

    def test_a_side_with_no_ring_point_is_simply_absent(self):
        """A base with grass on only two sides still attacks those two."""
        two_sides = [(675, 150), (600, 180), (375, 450), (410, 400)]
        drops = self.plan.one_point_per_side(self.centre, two_sides)
        self.assertEqual(2, len(drops))
        self.assertEqual(
            {"top", "left"},
            {self.plan.side_of(self.centre, d) for d in drops},
        )

    def test_side_order_and_choice_both_vary(self):
        seen = {
            tuple(self.plan.one_point_per_side(self.centre, self.ring))
            for _ in range(60)
        }
        self.assertGreater(
            len(seen), 1,
            "the same base attacked twice must not give the same sequence",
        )


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class RingSweepDropCountTest(unittest.TestCase):
    """How many points a card is emptied into is configurable."""

    def setUp(self):
        from logic.skills.ring_sweep_planner import RingSweepPlannerSkill
        self.plan = RingSweepPlannerSkill()
        self.centre = (675, 450)
        self.ring = [
            (675, 150), (600, 180),      # top
            (975, 450), (940, 500),      # right
            (675, 750), (700, 720),      # bottom
            (375, 450), (410, 400),      # left
        ]

    def _sides(self, drops):
        return [self.plan.side_of(self.centre, d) for d in drops]

    def test_the_default_four_is_still_one_per_side(self):
        drops = self.plan.pick_drops(self.centre, self.ring, 4)
        self.assertEqual(4, len(drops))
        self.assertEqual(4, len(set(self._sides(drops))))

    def test_fewer_points_uses_fewer_sides(self):
        drops = self.plan.pick_drops(self.centre, self.ring, 2)
        self.assertEqual(2, len(drops))
        self.assertEqual(2, len(set(self._sides(drops))), "no side twice")

    def test_more_points_comes_back_round_before_repeating_a_side(self):
        """Six points on a four-sided base is 2+2+1+1, never 3 on one side
        while another has none."""
        for _ in range(30):
            counts = collections.Counter(
                self._sides(self.plan.pick_drops(self.centre, self.ring, 6)),
            )
            self.assertEqual(4, len(counts))
            self.assertEqual([1, 1, 2, 2], sorted(counts.values()))

    def test_drops_are_never_the_same_point_twice(self):
        """Two fingers on one pixel is one finger to the game."""
        for count in (2, 4, 6, 8):
            drops = self.plan.pick_drops(self.centre, self.ring, count)
            self.assertEqual(len(drops), len(set(drops)), count)

    def test_asking_for_more_than_the_ring_holds_returns_the_ring(self):
        drops = self.plan.pick_drops(self.centre, self.ring, 50)
        self.assertEqual(sorted(self.ring), sorted(drops))

    def test_a_two_sided_base_still_honours_the_count(self):
        two_sides = [(675, 150), (600, 180), (375, 450), (410, 400)]
        drops = self.plan.pick_drops(self.centre, two_sides, 4)
        self.assertEqual(4, len(drops))
        self.assertEqual({"top", "left"}, set(self._sides(drops)))

    def test_an_empty_ring_plans_nothing(self):
        self.assertEqual([], self.plan.pick_drops(self.centre, [], 4))

    def test_the_rule_reads_hold_points_from_config(self):
        """The knob has to reach the planner, not just sit in the file."""
        source = pathlib.Path("logic/rules/ring_sweep_rule.py").read_text(encoding="utf-8")
        self.assertIn("wanted = self._hold_points(sweep_cfg, troop)", source)
        self.assertIn("pick_drops(centre, ring, wanted)", source)

    def test_the_shipped_config_defaults_to_four(self):
        cfg = json.loads(pathlib.Path("config/v2_attack_rules.json").read_text(encoding="utf-8"))
        sweep = cfg["ring_sweep"]
        self.assertEqual(4, sweep["hold_points"])
        self.assertEqual(4, sweep["hold_points_by_troop"]["_default"])


class RingSweepHoldPointsTest(unittest.TestCase):
    """How many points a card is split over is set per troop, because an
    army is not uniform — one troop wants every side, another wants one."""

    def _points(self, cfg, troop):
        from logic.rules.ring_sweep_rule import RingSweepRule
        return RingSweepRule._hold_points(cfg, troop)

    def test_per_troop_value_overrides_the_default(self):
        cfg = {"hold_points_by_troop": {"_default": 4, "baba": 2, "dragon": 8}}
        self.assertEqual(2, self._points(cfg, "baba"))
        self.assertEqual(8, self._points(cfg, "dragon"))
        self.assertEqual(4, self._points(cfg, "valkyrie"))

    def test_an_old_config_with_only_the_flat_knob_still_works(self):
        """hold_points shipped before the per-troop table did. A config
        that only has it must not silently revert to 4."""
        self.assertEqual(6, self._points({"hold_points": 6}, "baba"))

    def test_the_table_default_wins_over_the_flat_knob(self):
        cfg = {"hold_points": 6, "hold_points_by_troop": {"_default": 2}}
        self.assertEqual(2, self._points(cfg, "baba"))

    def test_a_troop_entry_wins_over_both(self):
        cfg = {"hold_points": 6, "hold_points_by_troop": {"_default": 2, "baba": 8}}
        self.assertEqual(8, self._points(cfg, "baba"))

    def test_missing_or_broken_config_falls_back_to_four(self):
        for cfg in ({}, {"hold_points_by_troop": {}},
                    {"hold_points": "oops"},
                    {"hold_points_by_troop": {"baba": None}}):
            self.assertEqual(4, self._points(cfg, "baba"), cfg)

    def test_a_key_that_differs_only_in_case_still_matches(self):
        """Card templates are lowercase but the config is hand-written.
        "Dragon" silently getting the default is not a useful failure."""
        cfg = {"hold_points_by_troop": {"_default": 4, "Dragon": 1}}
        self.assertEqual(1, self._points(cfg, "dragon"))

    def test_an_exact_key_wins_over_a_case_insensitive_one(self):
        cfg = {"hold_points_by_troop": {"dragon": 1, "DRAGON": 8}}
        self.assertEqual(1, self._points(cfg, "dragon"))

    def test_the_shipped_troop_keys_all_exist_as_card_templates(self):
        """A key with no template is a line of config that does nothing."""
        cfg = json.loads(pathlib.Path("config/v2_attack_rules.json").read_text(encoding="utf-8"))
        manifest = json.loads(
            pathlib.Path("assets/templates/manifest.json").read_text(encoding="utf-8"),
        )
        known = {name.casefold() for name in manifest}
        for table in ("hold_points_by_troop", "hold_ms_by_troop"):
            for key in cfg["ring_sweep"].get(table, {}):
                if key.startswith("_"):
                    continue
                self.assertIn(key.casefold(), known, f"{table}.{key}")

    def test_zero_and_negative_are_clamped_to_one(self):
        """0 points would tap the card and never deploy it — a troop
        silently left in the army for the whole battle."""
        for value in (0, -3):
            self.assertEqual(
                1, self._points({"hold_points_by_troop": {"baba": value}}, "baba"),
            )


class RingSweepHoldWindowTest(unittest.TestCase):
    """The hold window is per troop and applies at EACH side, re-rolled
    every time, so no two presses of one attack are identical."""

    def _window(self, cfg, troop):
        from logic.rules.ring_sweep_rule import RingSweepRule
        return RingSweepRule._hold_window_ms(cfg, troop)

    def test_per_troop_band_overrides_the_default(self):
        cfg = {"hold_ms_by_troop": {"_default": [5000, 6000], "baba": [800, 800]}}
        self.assertEqual(800, self._window(cfg, "baba"))
        self.assertTrue(5000 <= self._window(cfg, "dragon") <= 6000)

    def test_missing_config_falls_back_to_a_usable_window(self):
        for cfg in ({}, {"hold_ms_by_troop": {}}, {"hold_ms_by_troop": {"x": "oops"}}):
            self.assertTrue(5000 <= self._window(cfg, "x") <= 6000, cfg)

    def test_window_is_randomised_within_the_band(self):
        cfg = {"hold_ms_by_troop": {"_default": [3000, 9000]}}
        self.assertGreater(len({self._window(cfg, "x") for _ in range(50)}), 1)


class RingSweepHoldRoutingTest(unittest.TestCase):
    """Which gesture the sides get, and what happens when it fails."""

    DROPS = [(100, 100), (200, 200), (300, 300), (400, 400)]

    def _run(self, available, multi_ok=True):
        from unittest import mock
        from types import SimpleNamespace
        from logic.rules import ring_sweep_rule

        touch = mock.Mock()
        ctx = SimpleNamespace(
            config={}, skills=SimpleNamespace(touch=touch), engine=None,
        )
        rule = ring_sweep_rule.RingSweepRule()
        with mock.patch.object(rule, "_interrupted", return_value=False), \
             mock.patch.object(ring_sweep_rule.multi_touch, "enabled",
                               return_value=True), \
             mock.patch.object(ring_sweep_rule.multi_touch, "available",
                               return_value=available), \
             mock.patch.object(ring_sweep_rule.multi_touch, "hold_all",
                               return_value=multi_ok) as hold_all:
            rule._hold_sides(ctx, {}, "baba", self.DROPS)
        return hold_all, touch

    def test_multi_touch_presses_every_side_in_one_gesture(self):
        hold_all, touch = self._run(available=True)
        self.assertEqual(1, hold_all.call_count)
        self.assertEqual(self.DROPS, hold_all.call_args.args[0])
        touch.long_press.assert_not_called()

    def test_without_multi_touch_each_side_is_held_in_turn(self):
        hold_all, touch = self._run(available=False)
        hold_all.assert_not_called()
        self.assertEqual(
            self.DROPS,
            [c.args[:2] for c in touch.long_press.call_args_list],
        )

    def test_a_failed_multi_touch_still_deploys(self):
        """Root can disappear mid-session (Magisk denial, adb reconnect).
        Losing the gesture must not mean losing the whole attack."""
        hold_all, touch = self._run(available=True, multi_ok=False)
        self.assertEqual(1, hold_all.call_count)
        self.assertEqual(len(self.DROPS), touch.long_press.call_count)


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class RingSweepRegistrationTest(unittest.TestCase):
    def test_rule_is_registered_and_selectable(self):
        from logic.v2_orchestrator import V2Orchestrator
        from vision.screen_reader import ScreenReader

        orch = V2Orchestrator(ScreenReader())
        self.assertIn("ring_sweep", orch.available_rules())

    def test_rule_is_exposed_in_the_ui_dropdown(self):
        from ui.smart_v2_panel import _RULE_OPTIONS
        self.assertIn("ring_sweep", [value for _label, value in _RULE_OPTIONS])

    def test_config_block_exists(self):
        import json
        from pathlib import Path
        cfg = json.loads(
            (Path(__file__).resolve().parents[1]
             / "config" / "v2_attack_rules.json").read_text(encoding="utf-8")
        )
        self.assertIn("ring_sweep", cfg)
        self.assertIn("ring_sweep", cfg["rule_priorities"])


if __name__ == "__main__":
    unittest.main()
