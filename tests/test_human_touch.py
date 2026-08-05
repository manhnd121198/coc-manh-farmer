import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "logic" / "skills" / "human_touch.py"

adb_stub = types.ModuleType("core.adb_handler")
adb_stub._run = lambda *_args, **_kwargs: None
adb_stub.tap_raw = lambda *_args, **_kwargs: None
BATCH_CALLS: list[tuple] = []
adb_stub.tap_batch = lambda points, gap_ms=0, chunk_size=6: BATCH_CALLS.append(
    (list(points), gap_ms, chunk_size),
)
adb_stub.DEFAULT_SCREEN_WIDTH = 2340
adb_stub.DEFAULT_SCREEN_HEIGHT = 1080

logger_stub = types.ModuleType("core.logger")
logger_stub.BotLogger = types.SimpleNamespace(
    get=lambda _name: types.SimpleNamespace(debug=lambda *_args: None, info=lambda *_args: None),
)

SPEC = importlib.util.spec_from_file_location("human_touch_for_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
with patch.dict(sys.modules, {"core.adb_handler": adb_stub, "core.logger": logger_stub}):
    SPEC.loader.exec_module(MODULE)

HumanTouchSkill = MODULE.HumanTouchSkill


class HumanTouchSkillTest(unittest.TestCase):
    def test_tap_uses_android_tap_event_instead_of_zero_length_swipe(self):
        config = {
            "tap_jitter_px": 0,
            "deploy_pattern": {
                "inter_action_min_ms": 0,
                "inter_action_max_ms": 0,
            },
        }

        with patch.object(MODULE, "_adb_tap") as adb_tap:
            HumanTouchSkill().tap(321, 654, config)

        adb_tap.assert_called_once_with(321, 654)


class TapBurstTest(unittest.TestCase):
    """A burst is the same taps, minus the per-tap Python pause and the
    per-tap ADB round-trip."""

    def setUp(self):
        BATCH_CALLS.clear()

    def test_points_go_out_in_one_batch_with_the_configured_knobs(self):
        config = {
            "tap_jitter_px": 0,
            "deploy_pattern": {"tap_batch_size": 4, "tap_burst_gap_ms": 25},
        }

        HumanTouchSkill().tap_burst([(10, 20), (30, 40)], config)

        self.assertEqual(1, len(BATCH_CALLS))
        points, gap, chunk = BATCH_CALLS[0]
        self.assertEqual([(10, 20), (30, 40)], points)
        self.assertEqual(25, gap)
        self.assertEqual(4, chunk)

    def test_every_point_is_jittered(self):
        config = {"tap_jitter_px": 8, "deploy_pattern": {}}

        HumanTouchSkill().tap_burst([(500, 500)] * 6, config)

        points = BATCH_CALLS[0][0]
        for (x, y) in points:
            self.assertLessEqual(abs(x - 500), 8)
            self.assertLessEqual(abs(y - 500), 8)
        self.assertGreater(len(set(points)), 1, "jitter should vary per point")

    def test_defaults_are_no_gap_and_a_small_chunk(self):
        HumanTouchSkill().tap_burst([(1, 1)], {})

        _points, gap, chunk = BATCH_CALLS[0]
        self.assertEqual(0, gap)
        self.assertEqual(6, chunk)

    def test_empty_burst_sends_nothing(self):
        HumanTouchSkill().tap_burst([], {})

        self.assertEqual([], BATCH_CALLS)


if __name__ == "__main__":
    unittest.main()
