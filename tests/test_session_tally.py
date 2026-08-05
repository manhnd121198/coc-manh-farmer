import ast
import unittest
from pathlib import Path


ENGINE_PATH = Path(__file__).resolve().parents[1] / "core" / "bot_engine.py"
HV_PATH = Path(__file__).resolve().parents[1] / "logic" / "home_village.py"
BB_PATH = Path(__file__).resolve().parents[1] / "logic" / "builder_base.py"


def _tally_methods():
    """Build a stand-in for BotEngine carrying only the tally methods —
    importing the real one would drag in PyQt5, cv2 and torch."""
    tree = ast.parse(ENGINE_PATH.read_text(encoding="utf-8"))
    wanted = {"record_attack", "record_skip", "reset_stats"}
    body = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    assert len(body) == len(wanted), f"missing tally methods: {wanted}"

    namespace = {"log": _NullLog()}
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    exec(compile(module, str(ENGINE_PATH), "exec"), namespace)
    return {name: namespace[name] for name in wanted}


class _NullLog:
    def info(self, *_args):
        pass


class _FakeEngine:
    """Same attributes the real methods touch, with a recording signal."""

    def __init__(self):
        self._attack_count = 0
        self._skip_count = 0
        self.emitted: list[tuple[int, int]] = []
        self.stats_changed = type(
            "Sig", (), {"emit": lambda _s, a, b: self.emitted.append((a, b))},
        )()


METHODS = _tally_methods()


def _engine():
    eng = _FakeEngine()
    for name, fn in METHODS.items():
        setattr(eng, name, fn.__get__(eng))
    return eng


class SessionTallyTest(unittest.TestCase):
    def test_attacks_and_skips_count_separately(self):
        eng = _engine()

        eng.record_attack()
        eng.record_attack()
        eng.record_skip()

        self.assertEqual(2, eng._attack_count)
        self.assertEqual(1, eng._skip_count)
        self.assertEqual((2, 1), eng.emitted[-1])

    def test_every_change_is_published(self):
        eng = _engine()

        eng.record_attack()
        eng.record_skip()

        self.assertEqual([(1, 0), (1, 1)], eng.emitted)

    def test_reset_clears_both_and_publishes(self):
        eng = _engine()
        eng.record_attack()
        eng.record_skip()

        eng.reset_stats()

        self.assertEqual(0, eng._attack_count)
        self.assertEqual(0, eng._skip_count)
        self.assertEqual((0, 0), eng.emitted[-1])


class TallyCallSitesTest(unittest.TestCase):
    """The tally is only correct if every path that starts a battle goes
    through it — a missed call is silent, so pin the call sites."""

    def test_home_village_counts_all_three_entry_paths(self):
        source = HV_PATH.read_text(encoding="utf-8")

        # loot-skip, loot-OK, and the ranked auto-activation.
        self.assertEqual(3, source.count("self._count_attack()"))
        self.assertEqual(1, source.count("self._engine.record_skip()"))

    def test_builder_base_counts_the_battle_not_each_stage(self):
        source = BB_PATH.read_text(encoding="utf-8")

        self.assertEqual(1, source.count("self._engine.record_attack()"))
        self.assertIn("self._current_stage == 1 and self._engine is not None", source)

    def test_start_resets_the_tally(self):
        source = ENGINE_PATH.read_text(encoding="utf-8")
        start = source.index("def start_bot")
        stop = source.index("def stop_bot")

        self.assertIn("self.reset_stats()", source[start:stop])


if __name__ == "__main__":
    unittest.main()
