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


if __name__ == "__main__":
    unittest.main()
