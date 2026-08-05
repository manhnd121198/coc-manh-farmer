"""Holding a card down until it empties, instead of tapping N times.

The exit condition is vision-based (an exhausted card leaves the deploy
bar), so the two ways this can go wrong are: never stopping, or stopping
before the card is empty. Both are pinned here.

Parsed with ``ast`` — base_rule imports the vision skills, i.e. cv2.
"""

import ast
import unittest
from pathlib import Path


BASE_RULE_PATH = Path(__file__).resolve().parents[1] / "logic" / "rules" / "base_rule.py"
WANTED = {"_hold_enabled", "_hold_dump"}


class _NullLog:
    def info(self, *_args):
        pass


class _Touch:
    def __init__(self):
        self.presses: list[tuple[int, int, int]] = []

    def long_press(self, x, y, dur_ms=None, config=None):
        self.presses.append((x, y, dur_ms))


class _Target:
    """``find_one`` returns a position until the card is 'empty'."""

    def __init__(self, empty_after: int | None):
        self.empty_after = empty_after
        self.lookups = 0

    def find_one(self, _screenshot, _key):
        self.lookups += 1
        if self.empty_after is not None and self.lookups >= self.empty_after:
            return None
        return (10, 10)


class _Ctx:
    def __init__(self, config, troop_profiles, empty_after=None):
        self.config = config
        self.troop_profiles = troop_profiles
        self.skills = type("Skills", (), {})()
        self.skills.touch = _Touch()
        self.skills.target = _Target(empty_after)
        self.engine = None


class _Rule:
    def __init__(self, interrupted_after=None):
        self._interrupt_calls = 0
        self._interrupted_after = interrupted_after

    def _interrupted(self, _ctx):
        self._interrupt_calls += 1
        if self._interrupted_after is None:
            return False
        return self._interrupt_calls > self._interrupted_after


def _methods():
    tree = ast.parse(BASE_RULE_PATH.read_text(encoding="utf-8"))
    body = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in WANTED
    ]
    assert len(body) == len(WANTED), f"missing: {WANTED - {n.name for n in body}}"
    return body


METHODS = _methods()


def _rule(interrupted_after=None, screencap=lambda: object()):
    obj = _Rule(interrupted_after)
    namespace = {
        "log": _NullLog(),
        "screencap": screencap,
        # Annotations are evaluated at def time; stubs are enough.
        "AttackContext": object,
    }
    module = ast.fix_missing_locations(ast.Module(body=METHODS, type_ignores=[]))
    exec(compile(module, str(BASE_RULE_PATH), "exec"), namespace)
    for node in METHODS:
        fn = namespace[node.name]
        # _hold_enabled is a @staticmethod in the class; here it is a
        # plain function, so bind only the instance method.
        setattr(obj, node.name,
                fn if node.name == "_hold_enabled" else fn.__get__(obj))
    return obj


HOLD_CFG = {"deploy_pattern": {"hold_until_empty": True,
                               "hold_chunk_ms": 1000, "hold_max_ms": 5000}}


class HoldEnabledTest(unittest.TestCase):
    def test_global_flag_decides_by_default(self):
        rule = _rule()
        on = _Ctx(HOLD_CFG, {"dragon": {}})
        off = _Ctx({"deploy_pattern": {"hold_until_empty": False}}, {"dragon": {}})

        self.assertTrue(rule._hold_enabled(on, "dragon"))
        self.assertFalse(rule._hold_enabled(off, "dragon"))

    def test_absent_flag_keeps_the_old_tap_behaviour(self):
        rule = _rule()
        ctx = _Ctx({}, {})

        self.assertFalse(rule._hold_enabled(ctx, "dragon"))

    def test_per_troop_mode_overrides_the_global_flag(self):
        rule = _rule()
        forced_tap = _Ctx(HOLD_CFG, {"dragon": {"deploy_mode": "tap"}})
        forced_hold = _Ctx(
            {"deploy_pattern": {"hold_until_empty": False}},
            {"dragon": {"deploy_mode": "hold"}},
        )

        self.assertFalse(rule._hold_enabled(forced_tap, "dragon"))
        self.assertTrue(rule._hold_enabled(forced_hold, "dragon"))


class HoldDumpTest(unittest.TestCase):
    def test_stops_as_soon_as_the_card_leaves_the_bar(self):
        rule = _rule()
        ctx = _Ctx(HOLD_CFG, {}, empty_after=3)

        rule._hold_dump(ctx, "dragon", [(100, 200)])

        self.assertEqual(3, len(ctx.skills.touch.presses))
        self.assertTrue(all(p[2] == 1000 for p in ctx.skills.touch.presses))

    def test_keeps_holding_while_the_card_is_still_there(self):
        # Card never disappears → hold for the whole budget, no further.
        rule = _rule()
        ctx = _Ctx(HOLD_CFG, {}, empty_after=None)

        rule._hold_dump(ctx, "dragon", [(100, 200)])

        self.assertEqual(5, len(ctx.skills.touch.presses))  # 5000 / 1000

    def test_spreads_the_hold_over_the_given_points(self):
        rule = _rule()
        ctx = _Ctx(HOLD_CFG, {}, empty_after=None)

        rule._hold_dump(ctx, "dragon", [(1, 1), (2, 2)])

        xs = [p[0] for p in ctx.skills.touch.presses]
        self.assertEqual([1, 2, 1, 2, 1], xs)

    def test_a_missing_screenshot_does_not_end_the_dump_early(self):
        rule = _rule(screencap=lambda: None)
        ctx = _Ctx(HOLD_CFG, {}, empty_after=1)

        rule._hold_dump(ctx, "dragon", [(1, 1)])

        self.assertEqual(5, len(ctx.skills.touch.presses))
        self.assertEqual(0, ctx.skills.target.lookups)

    def test_pause_or_stop_breaks_out(self):
        rule = _rule(interrupted_after=2)
        ctx = _Ctx(HOLD_CFG, {}, empty_after=None)

        rule._hold_dump(ctx, "dragon", [(1, 1)])

        self.assertEqual(2, len(ctx.skills.touch.presses))

    def test_no_points_is_a_no_op(self):
        rule = _rule()
        ctx = _Ctx(HOLD_CFG, {}, empty_after=None)

        rule._hold_dump(ctx, "dragon", [])

        self.assertEqual([], ctx.skills.touch.presses)


class ChunkFloorTest(unittest.TestCase):
    def test_a_silly_small_chunk_is_clamped(self):
        rule = _rule()
        cfg = {"deploy_pattern": {"hold_chunk_ms": 10, "hold_max_ms": 1200}}
        ctx = _Ctx(cfg, {}, empty_after=None)

        rule._hold_dump(ctx, "dragon", [(1, 1)])

        # 600 ms floor — CoC reads anything shorter as a tap, not a hold.
        self.assertTrue(all(p[2] == 600 for p in ctx.skills.touch.presses))


if __name__ == "__main__":
    unittest.main()
