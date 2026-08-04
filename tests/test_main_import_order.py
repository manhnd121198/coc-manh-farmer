import ast
import unittest
from pathlib import Path


class MainImportOrderTest(unittest.TestCase):
    def test_torch_loads_before_pyqt_on_windows(self):
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(main_path.read_text(encoding="utf-8"))

        imports = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        torch_index = imports.index("torch")
        pyqt_index = next(
            index for index, module in enumerate(imports)
            if module == "PyQt5" or module.startswith("PyQt5.")
        )
        self.assertLess(torch_index, pyqt_index)


if __name__ == "__main__":
    unittest.main()
