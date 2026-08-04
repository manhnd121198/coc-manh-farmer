import ast
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "vision" / "screen_reader.py"


def _load_scaled_bar_height():
    """Pull `_scaled_bar_height` out without importing cv2/torch."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_scaled_bar_height":
            fn = ast.FunctionDef(
                name=node.name,
                args=node.args,
                body=node.body,
                decorator_list=[],          # drop @staticmethod
                returns=node.returns,
                type_comment=None,
                type_params=[],
            )
            module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
            namespace: dict = {}
            exec(compile(module, str(MODULE_PATH), "exec"), namespace)
            return namespace["_scaled_bar_height"]
    raise AssertionError("_scaled_bar_height not found")


scaled_bar_height = _load_scaled_bar_height()

# The bundled troops_bar template, captured on a ~2400-wide screen.
BUNDLED_BAR = (2330, 418)


class UiCutoffScalingTest(unittest.TestCase):
    def test_narrower_screen_gets_a_shorter_bar(self):
        # 1920x1080: the bar is really ~344px, not the stored 418px.
        height = scaled_bar_height(BUNDLED_BAR, 1080, 1920 / 1080)

        self.assertEqual(344, height)
        self.assertEqual(736, 1080 - height)   # playfield keeps 74 more rows

    def test_capture_resolution_is_unchanged(self):
        # On the screen it was captured from, the bar keeps its size.
        height = scaled_bar_height(BUNDLED_BAR, 1080, 2400 / 1080)

        self.assertAlmostEqual(418, height, delta=15)

    def test_wider_screen_gets_a_taller_bar(self):
        narrow = scaled_bar_height(BUNDLED_BAR, 1080, 1350 / 1080)
        wide = scaled_bar_height(BUNDLED_BAR, 1080, 2400 / 1080)

        self.assertLess(narrow, wide)

    def test_degenerate_aspect_does_not_produce_a_zero_bar(self):
        self.assertGreaterEqual(scaled_bar_height(BUNDLED_BAR, 1080, 0.0), 1)


if __name__ == "__main__":
    unittest.main()
