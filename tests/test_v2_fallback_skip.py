"""When the V2 orchestrator cannot plan an attack, Home Village presses
Next instead of dumping the army with the legacy planner.

Parsed with ``ast`` — importing the real modules would drag in PyQt5,
cv2 and torch, which the test suite deliberately avoids.
"""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "core" / "bot_engine.py"
HV_PATH = ROOT / "logic" / "home_village.py"
V2_PATH = ROOT / "logic" / "smart_v2_logic.py"
SETTINGS_PATH = ROOT / "core" / "settings.py"


def _func(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}")


class _NullLog:
    def info(self, *_args):
        pass


class _FakeEngine:
    def __init__(self):
        self._attack_count = 0
        self._skip_count = 0
        self.emitted: list[tuple[int, int]] = []
        self.stats_changed = type(
            "Sig", (), {"emit": lambda _s, a, b: self.emitted.append((a, b))},
        )()


def _bind(engine, name: str):
    node = _func(ENGINE_PATH, name)
    namespace = {"log": _NullLog()}
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, str(ENGINE_PATH), "exec"), namespace)
    setattr(engine, name, namespace[name].__get__(engine))


class RecordAttackSkippedTest(unittest.TestCase):
    def _engine(self):
        eng = _FakeEngine()
        for name in ("record_attack", "record_skip", "record_attack_skipped"):
            _bind(eng, name)
        return eng

    def test_moves_the_battle_from_attacks_to_skips(self):
        eng = self._engine()
        eng.record_attack()

        eng.record_attack_skipped()

        self.assertEqual(0, eng._attack_count)
        self.assertEqual(1, eng._skip_count)
        self.assertEqual((0, 1), eng.emitted[-1])

    def test_never_drives_the_attack_count_negative(self):
        eng = self._engine()

        eng.record_attack_skipped()

        self.assertEqual(0, eng._attack_count)
        self.assertEqual(1, eng._skip_count)


class HomeVillageSkipPathTest(unittest.TestCase):
    """The skip only works if every piece of the path is wired: the
    setting is honoured, Next is tapped, and the tally is corrected."""

    def setUp(self):
        self.source = HV_PATH.read_text(encoding="utf-8")

    def test_v2_failure_routes_to_the_skip_helper(self):
        body = ast.unparse(_func(HV_PATH, "_execute_full_attack"))

        self.assertIn("v2_skip_on_fallback", body)
        self.assertIn("allow_legacy=not skip_on_fallback", body)
        self.assertIn("self._skip_unplannable_base(screenshot)", body)

    def test_skip_helper_taps_next_and_corrects_the_tally(self):
        body = ast.unparse(_func(HV_PATH, "_skip_unplannable_base"))

        self.assertIn("'next_button'", body)
        self.assertIn("self._engine.record_attack_skipped()", body)
        self.assertIn("self._attack_active = False", body)

    def test_ranked_still_deploys_because_there_is_no_next_button(self):
        body = ast.unparse(_func(HV_PATH, "_skip_unplannable_base"))

        self.assertIn("self._v2.run_legacy(ss)", body)

    def test_the_loot_skip_path_is_untouched(self):
        # Loot-below-threshold keeps its own counter; the V2 skip must
        # not have been folded into it.
        self.assertEqual(1, self.source.count("self._engine.record_skip()"))
        self.assertEqual(1, self.source.count("self._engine.record_attack_skipped()"))


class SmartV2ExecuteContractTest(unittest.TestCase):
    def test_execute_reports_failure_when_legacy_is_disabled(self):
        body = ast.unparse(_func(V2_PATH, "execute"))

        self.assertIn("allow_legacy", body)
        self.assertIn("if not allow_legacy", body)

    def test_legacy_stays_reachable_as_a_public_entry(self):
        self.assertIsNotNone(_func(V2_PATH, "run_legacy"))


class SettingDefaultTest(unittest.TestCase):
    def test_skip_on_fallback_defaults_to_on(self):
        source = SETTINGS_PATH.read_text(encoding="utf-8")

        self.assertIn('"v2_skip_on_fallback": True', source)


if __name__ == "__main__":
    unittest.main()
