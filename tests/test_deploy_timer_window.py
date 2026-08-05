"""The post-deploy countdown is drawn once per battle from a range.

Drawing it per tick instead would silently collapse the range to its
minimum — the battle would end on the first tick whose draw fell below
the elapsed time — so the "one draw per deployment" property is pinned.

Parsed with ``ast``: importing home_village would pull in cv2 and PyQt5.
"""

import ast
import unittest
from pathlib import Path


HV_PATH = Path(__file__).resolve().parents[1] / "logic" / "home_village.py"

WANTED = {"_deploy_timer_window", "_resolve_deploy_deadline", "_check_deploy_timer"}


class _NullLog:
    def info(self, *_args):
        pass


class _FakeRandom:
    """Hands out a scripted sequence so draws are observable."""

    def __init__(self, values):
        self.values = list(values)
        self.calls: list[tuple[int, int]] = []

    def randint(self, lo, hi):
        self.calls.append((lo, hi))
        return self.values.pop(0) if self.values else hi


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


class _FakeHV:
    def __init__(self, profile, rng, clock):
        self._profile = profile
        self._post_deploy_time = 0.0
        self._deploy_timer_stamp = 0.0
        self._deploy_timer_target = 0
        self._attack_active = True
        self.ended = 0
        self._rng = rng
        self._clock = clock

    def _end_battle(self, _screenshot):
        self.ended += 1


def _methods():
    tree = ast.parse(HV_PATH.read_text(encoding="utf-8"))
    body = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in WANTED
    ]
    assert len(body) == len(WANTED), f"missing: {WANTED - {n.name for n in body}}"
    return body


METHODS = _methods()


def _hv(profile, draws=(), now=1000.0):
    rng, clock = _FakeRandom(draws), _FakeClock()
    clock.now = now
    obj = _FakeHV(profile, rng, clock)
    namespace = {
        "log": _NullLog(), "random": rng, "time": clock,
        "C_RED": "", "C_RESET": "",
        # The signatures are annotated with np.ndarray, evaluated at def
        # time — a stub is enough, numpy itself is not imported here.
        "np": type("np", (), {"ndarray": object}),
    }
    module = ast.fix_missing_locations(ast.Module(body=METHODS, type_ignores=[]))
    exec(compile(module, str(HV_PATH), "exec"), namespace)
    for node in METHODS:
        setattr(obj, node.name, namespace[node.name].__get__(obj))
    return obj, rng, clock


class WindowTest(unittest.TestCase):
    def test_missing_max_means_a_fixed_timer(self):
        hv, rng, _ = _hv({"deploy_timer_seconds": 100})

        self.assertEqual((100, 100), hv._deploy_timer_window())
        hv._post_deploy_time = 1.0
        self.assertEqual(100, hv._resolve_deploy_deadline())
        self.assertEqual([], rng.calls)  # no draw needed

    def test_reversed_bounds_are_ordered(self):
        hv, _, _ = _hv({"deploy_timer_seconds": 120,
                        "deploy_timer_seconds_max": 110})

        self.assertEqual((110, 120), hv._deploy_timer_window())


class SingleDrawPerBattleTest(unittest.TestCase):
    PROFILE = {
        "deploy_timer_enabled": True,
        "deploy_timer_seconds": 110,
        "deploy_timer_seconds_max": 120,
    }

    def test_draw_uses_the_configured_range(self):
        hv, rng, _ = _hv(self.PROFILE, draws=[117])
        hv._post_deploy_time = 1000.0

        self.assertEqual(117, hv._resolve_deploy_deadline())
        self.assertEqual([(110, 120)], rng.calls)

    def test_the_same_deployment_keeps_its_number(self):
        hv, rng, _ = _hv(self.PROFILE, draws=[113, 119])
        hv._post_deploy_time = 1000.0

        first = hv._resolve_deploy_deadline()
        again = hv._resolve_deploy_deadline()

        self.assertEqual(first, again)
        self.assertEqual(1, len(rng.calls))

    def test_a_new_deployment_draws_again(self):
        hv, rng, _ = _hv(self.PROFILE, draws=[113, 119])
        hv._post_deploy_time = 1000.0
        first = hv._resolve_deploy_deadline()

        hv._post_deploy_time = 2000.0  # next battle
        second = hv._resolve_deploy_deadline()

        self.assertEqual(113, first)
        self.assertEqual(119, second)
        self.assertEqual(2, len(rng.calls))


class CountdownBehaviourTest(unittest.TestCase):
    PROFILE = {
        "deploy_timer_enabled": True,
        "deploy_timer_seconds": 110,
        "deploy_timer_seconds_max": 120,
    }

    def test_battle_ends_only_after_the_drawn_delay(self):
        hv, _, clock = _hv(self.PROFILE, draws=[118])
        hv._post_deploy_time = 1000.0

        clock.now = 1000.0 + 117
        self.assertFalse(hv._check_deploy_timer(None))
        self.assertEqual(0, hv.ended)

        clock.now = 1000.0 + 118
        self.assertTrue(hv._check_deploy_timer(None))
        self.assertEqual(1, hv.ended)
        self.assertFalse(hv._attack_active)
        self.assertEqual(0.0, hv._post_deploy_time)

    def test_ticking_never_re_rolls_the_deadline_downwards(self):
        # A draw of 120 must not be undercut by later draws of 110.
        hv, rng, clock = _hv(self.PROFILE, draws=[120, 110, 110, 110])
        hv._post_deploy_time = 1000.0

        for offset in (100, 105, 112, 119):
            clock.now = 1000.0 + offset
            self.assertFalse(hv._check_deploy_timer(None))

        self.assertEqual(1, len(rng.calls))
        self.assertEqual(0, hv.ended)

    def test_disabled_timer_never_fires(self):
        hv, _, clock = _hv({"deploy_timer_enabled": False,
                            "deploy_timer_seconds": 110,
                            "deploy_timer_seconds_max": 120})
        hv._post_deploy_time = 1000.0
        clock.now = 9999.0

        self.assertFalse(hv._check_deploy_timer(None))
        self.assertEqual(0, hv.ended)


if __name__ == "__main__":
    unittest.main()
