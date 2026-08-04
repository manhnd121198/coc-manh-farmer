import ast
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "logic" / "smart_v2_logic.py"


class LegacyFallbackDeploymentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        cls.deploy_full = next(
            item
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SmartV2Logic"
            for item in node.body
            if isinstance(item, ast.FunctionDef) and item.name == "_deploy_full"
        )

    def test_fallback_never_uses_swipe_or_hold_for_troops(self):
        call_names = [
            node.func.id
            for node in ast.walk(self.deploy_full)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]

        self.assertNotIn("swipe", call_names)
        self.assertIn("tap_raw", call_names)

    def test_spell_loop_uses_configured_drop_count_not_fixed_seven(self):
        loops = [node for node in ast.walk(self.deploy_full) if isinstance(node, ast.For)]
        configured_loop = next(
            node
            for node in loops
            if isinstance(node.iter, ast.Call)
            and isinstance(node.iter.func, ast.Name)
            and node.iter.func.id == "range"
            and len(node.iter.args) == 1
            and isinstance(node.iter.args[0], ast.Name)
            and node.iter.args[0].id == "drop_count"
        )

        self.assertIsNotNone(configured_loop)


if __name__ == "__main__":
    unittest.main()
