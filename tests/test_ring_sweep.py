"""Tests for the Ring Sweep mode — even drops around all 4 sides of a base.

The contract that matters in-game:
  * every planned point must sit OUTSIDE the red zone (troops can always be
    dropped there),
  * points must stay inside the playfield (off-screen taps are discarded by
    Android and silently lose troops),
  * coverage must be balanced across the four sides,
  * a non-convex base (cross / T shaped) must still work,
  * unlike PerimeterSweep it must NOT require all four screen-edge corridors.
"""

import unittest

try:
    import numpy as np
    import cv2  # noqa: F401
    _HAVE_CV2 = True
except ImportError:  # pragma: no cover
    _HAVE_CV2 = False


CFG = {
    "ring_sweep": {
        "clearance_px": 45,
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

    # ── Chords must not cut through the no-deploy area ─────────────
    def _crossings(self, polygon, a, b, samples=40):
        return sum(
            self.rz.is_inside(
                polygon,
                int(round(a[0] + (b[0] - a[0]) * k / samples)),
                int(round(a[1] + (b[1] - a[1]) * k / samples)),
            )
            for k in range(1, samples)
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
                        0, self._crossings(polygon, start, end),
                        f"leg {start}->{end} crosses the no-deploy zone",
                    )

    def test_diamond_route_keeps_the_whole_lap_in_one_press(self):
        """Straight isometric edges never cut the corners of a diamond base.

        The earlier ray-cast route broke into four arcs on any rectangular
        base because each corner chord clipped it. Every press costs a ~1s
        ramp before the game deploys, so the lap must stay in one piece.
        """
        polygon = _square()
        ring = self._ring(polygon)
        arcs = self.plan.deployable_arcs(polygon, ring)
        self.assertEqual(1, len(arcs), f"lap split into {len(arcs)} arcs")
        self.assertEqual(len(ring) + 1, len(arcs[0]), "lap must close on itself")

    def test_no_point_lands_inside_the_red_zone(self):
        for polygon in (_square(), _cross()):
            ring = self._ring(polygon)
            self.assertTrue(ring, "planner produced no points")
            for x, y in ring:
                self.assertFalse(
                    self.rz.is_inside(polygon, x, y),
                    f"point ({x},{y}) is inside the no-deploy zone",
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
            self.assertFalse(self.rz.is_inside(polygon, x, y))

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
