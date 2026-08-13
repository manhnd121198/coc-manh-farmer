"""Regression tests for the gesture that actually deploys troops.

Measured on a live device (1350x1080, TH11 barracks army):

* ``input swipe`` deploys NOTHING at any length or duration — 200px/300ms,
  200px/150ms and 300px/300ms all left the troop counter untouched. The
  game starts panning the camera on the first frame because the finger
  moves immediately.
* Pressing down, waiting ~1s for repeat-deploy to start, and only THEN
  dragging lays troops along the path — 21 troops over one 3s gesture.

So the sweep rules must drive DOWN / sleep / MOVE… / UP, and must never
reach for ``quick_swipe`` to deploy.
"""

import pathlib
import unittest
from unittest import mock

from logic.skills.human_touch import HumanTouchSkill

# Fast config — the SHAPE of the gesture is what these tests pin down.
FAST_CFG = {
    "tap_jitter_px": 0,
    "deploy_pattern": {
        "deploy_hold_ms": 1, "deploy_step_ms": 0, "deploy_steps": 4,
        "inter_action_min_ms": 0, "inter_action_max_ms": 0,
    },
}


class DeployGestureTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("logic.skills.human_touch._adb_run")
        self.adb = patcher.start()
        self.addCleanup(patcher.stop)
        self.adb.side_effect = lambda args, timeout=15: None

    def motion(self):
        """The motionevent calls, as (ACTION, x, y) triples."""
        out = []
        for call in self.adb.call_args_list:
            args = call.args[0]
            if len(args) >= 6 and args[2] == "motionevent":
                out.append((args[3], args[4], args[5]))
        return out

    def swipes(self):
        return [c.args[0] for c in self.adb.call_args_list
                if len(c.args[0]) > 2 and c.args[0][2] == "swipe"]

    def test_deploy_path_holds_before_moving_then_releases(self):
        HumanTouchSkill().deploy_path([(100, 200), (500, 200)], FAST_CFG, steps_per_leg=4)
        actions = [a for a, _x, _y in self.motion()]
        self.assertEqual(["DOWN"] + ["MOVE"] * 4 + ["UP"], actions)

    def test_deploy_path_walks_from_start_to_end(self):
        HumanTouchSkill().deploy_path([(100, 200), (500, 200)], FAST_CFG, steps_per_leg=4)
        motion = self.motion()
        self.assertEqual(("100", "200"), motion[0][1:])
        self.assertEqual(("500", "200"), motion[-1][1:])
        xs = [int(x) for _a, x, _y in motion]
        self.assertEqual(sorted(xs), xs, "moves must progress along the path")

    def test_deploy_path_never_uses_input_swipe(self):
        """A swipe would pan the camera and deploy nothing."""
        HumanTouchSkill().deploy_path([(100, 200), (500, 200)], FAST_CFG, steps_per_leg=3)
        self.assertEqual([], self.swipes())

    def test_deploy_path_releases_even_when_a_move_fails(self):
        """A stuck DOWN makes every later gesture part of one phantom drag."""
        state = {"n": 0}

        def flaky(args, timeout=15):
            state["n"] += 1
            if state["n"] == 2:            # the first MOVE after the DOWN
                raise RuntimeError("adb died mid-drag")

        self.adb.side_effect = flaky
        with self.assertRaises(RuntimeError):
            HumanTouchSkill().deploy_path([(10, 20), (300, 20)], FAST_CFG, steps_per_leg=3)

        actions = [a for a, _x, _y in self.motion()]
        self.assertEqual("UP", actions[-1], "the finger must always be released")


class SweepRulesUseDeployLineTest(unittest.TestCase):
    def test_no_sweep_rule_deploys_with_quick_swipe(self):
        """``quick_swipe`` pans the camera and drops nothing — a sweep rule
        that reaches for it would run a whole attack deploying no troops."""
        for name in ("ring_sweep_rule.py", "perimeter_sweep_rule.py"):
            source = pathlib.Path("logic/rules", name).read_text(encoding="utf-8")
            self.assertNotIn(
                "quick_swipe", source,
                f"{name} still deploys with a swipe, which drops no troops",
            )

    def test_perimeter_sweep_drags(self):
        source = pathlib.Path("logic/rules/perimeter_sweep_rule.py").read_text(encoding="utf-8")
        self.assertIn("deploy_path", source)

    def test_ring_sweep_holds_each_side(self):
        """Ring sweep deploys by holding one spot per side, so it must not
        fall back to the drag primitive."""
        source = pathlib.Path("logic/rules/ring_sweep_rule.py").read_text(encoding="utf-8")
        self.assertNotIn("deploy_path", source)
        self.assertIn("skills.touch.long_press(x, y, hold_ms, cfg", source)


if __name__ == "__main__":
    unittest.main()
