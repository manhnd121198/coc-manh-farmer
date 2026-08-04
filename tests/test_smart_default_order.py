import ast
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "logic" / "rules" / "smart_default_rule.py"


class SmartDefaultOrderTest(unittest.TestCase):
    def test_troop_deploys_before_totem_spell_phase(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        execute = next(
            item
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "SmartDefaultRule"
            for item in node.body
            if isinstance(item, ast.FunctionDef) and item.name == "execute"
        )
        calls = [
            (node.func.attr, node.lineno)
            for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        dragon_deploy_line = next(line for name, line in calls if name == "long_press")
        spell_phase_line = next(line for name, line in calls if name == "_deploy_spells")

        self.assertLess(dragon_deploy_line, spell_phase_line)


if __name__ == "__main__":
    unittest.main()
