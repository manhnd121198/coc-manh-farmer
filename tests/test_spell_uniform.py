"""Tests for the inside_base_uniform spell carpet (30 Rage / 30 Heal event).

Contract:
  * ``drop_count`` points, all inside the base polygon,
  * spread out — no two carpet points sit on top of each other,
  * ordered so an early prefix (one wave) already spans the base, not one
    corner, so waves land broadly and only get denser,
  * a degenerate / missing base yields nothing (caller falls back).
"""

import unittest

try:
    import numpy as np
    import cv2  # noqa: F401
    _HAVE_CV2 = True
except ImportError:  # pragma: no cover
    _HAVE_CV2 = False

if _HAVE_CV2:
    from logic.skills.spell_planner import SpellPlannerSkill
    from vision.skills.red_zone_polygon import RedZonePolygonSkill


def _base():
    # A convex base hull like YOLO returns (bbox ~700x560).
    return np.array(
        [[500, 250], [1200, 300], [1250, 780], [560, 820], [430, 520]],
        dtype=np.int32,
    )


def _bbox_span(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (max(xs) - min(xs), max(ys) - min(ys))


@unittest.skipUnless(_HAVE_CV2, "requires numpy + OpenCV")
class UniformInsideBaseTest(unittest.TestCase):
    def test_missing_base_returns_nothing(self):
        self.assertEqual(
            SpellPlannerSkill._uniform_inside_base(None, (0, 0), 30, 0.85), [],
        )

    def test_produces_the_requested_count_all_inside(self):
        base = _base()
        pts = SpellPlannerSkill._uniform_inside_base(base, (850, 520), 30, 0.9)
        self.assertEqual(len(pts), 30)
        for p in pts:
            self.assertTrue(
                RedZonePolygonSkill.is_inside(base, p[0], p[1]),
                f"{p} landed outside the base",
            )

    def test_points_do_not_stack(self):
        base = _base()
        pts = SpellPlannerSkill._uniform_inside_base(base, (850, 520), 30, 0.9)
        self.assertEqual(len(set(pts)), len(pts), "duplicate carpet points")
        # Farthest-point sampling keeps a real gap between neighbours.
        nearest_sq = []
        for i, a in enumerate(pts):
            d = min(
                (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
                for j, b in enumerate(pts) if j != i
            )
            nearest_sq.append(d)
        # Every point is at least ~20px from its closest neighbour.
        self.assertGreater(min(nearest_sq), 20 ** 2)

    def test_first_wave_already_spans_the_base(self):
        base = _base()
        pts = SpellPlannerSkill._uniform_inside_base(base, (850, 520), 30, 0.9)
        full_w, full_h = _bbox_span(pts)
        wave_w, wave_h = _bbox_span(pts[:5])   # first wave of 5
        # The opening wave should cover most of the footprint, not huddle.
        self.assertGreater(wave_w, 0.6 * full_w)
        self.assertGreater(wave_h, 0.6 * full_h)

    def test_plan_spell_routes_rage_through_the_uniform_carpet(self):
        base = _base()
        planner = SpellPlannerSkill(target_locator=None)
        profiles = {
            "rage_spell": {
                "placement": "inside_base_uniform",
                "drop_count": 30,
                "drops_per_wave": 5,
                "wave_interval_sec": 3,
                "inner_scale": 0.85,
            }
        }
        drops = planner.plan_spell(
            screenshot=None,
            spell_name="rage_spell",
            cluster_xy=(200, 900),
            target_xy=(850, 520),
            config={},
            spell_profiles=profiles,
            base_polygon=base,
        )
        self.assertEqual(len(drops), 30)
        for p in drops:
            self.assertTrue(RedZonePolygonSkill.is_inside(base, p[0], p[1]))


@unittest.skipUnless(_HAVE_CV2, "requires numpy + OpenCV")
class SpellCarpetDumpTest(unittest.TestCase):
    def test_dump_writes_a_file_when_debug_dir_is_set(self):
        import os
        import tempfile

        base = _base()
        drops = SpellPlannerSkill._uniform_inside_base(base, (850, 520), 30, 0.9)
        img = np.zeros((900, 1400, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"polygon": {"debug_dump": tmp}}
            RedZonePolygonSkill.dump_spell_plan(img, base, drops, 5, cfg, "rage_spell")
            files = [f for f in os.listdir(tmp) if f.startswith("spellcarpet_rage_spell_")]
            self.assertEqual(len(files), 1, f"expected one dump, got {files}")

    def test_dump_is_a_no_op_without_a_debug_dir(self):
        import os
        import tempfile

        base = _base()
        drops = SpellPlannerSkill._uniform_inside_base(base, (850, 520), 12, 0.9)
        img = np.zeros((900, 1400, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"polygon": {"debug_dump": ""}}
            RedZonePolygonSkill.dump_spell_plan(img, base, drops, 5, cfg, "heal_spell")
            self.assertEqual(os.listdir(tmp), [])


if __name__ == "__main__":
    unittest.main()
