"""detect_state phải nhanh hơn mà vẫn trả lời y hệt.

Hai thay đổi được chốt ở đây:

1. Chín template ``bb_*`` chỉ được hỏi khi bot thật sự đang chơi Builder
   Base. Ở làng nhà chúng là chín lần trượt chắc chắn, ~137ms mỗi lần đo
   trên máy thật — hơn một giây mỗi tick để chứng minh điều đã biết.

2. ``_match_ui`` lọc thô ở nửa độ phân giải trước. Cái này chỉ đúng khi
   vòng lọc KHÔNG BAO GIỜ biến một cú trúng thành trượt: đo trên 18
   template UI, điểm ở 0.5x luôn cao hơn điểm ở 1.0x (lệch +0.000 đến
   +0.078), nên điểm thô thấp là bằng chứng điểm tinh cũng thấp.
"""

import unittest
from unittest import mock

import cv2
import numpy as np

from vision import screen_reader as srmod
from vision.screen_reader import ScreenReader


class VillageModeTest(unittest.TestCase):
    """Chỉ hỏi template của làng đang chơi."""

    def _names_asked(self, mode: str) -> list[str]:
        sr = ScreenReader()
        sr.set_village_mode(mode)
        asked: list[str] = []

        def spy(_ss, name, threshold=None):
            asked.append(name)
            return None                      # trượt hết -> đi tới cuối

        frame = np.zeros((1080, 1350, 3), dtype=np.uint8)
        with mock.patch.object(sr, "find_template_by_name", side_effect=spy), \
                mock.patch.object(sr, "scan_for_confirmations", return_value=[]), \
                mock.patch.object(sr, "_get_cached_template", return_value=None):
            sr.detect_state(frame)
        return asked

    def test_home_village_never_asks_builder_base_templates(self):
        asked = self._names_asked("home_village")
        self.assertEqual([], [n for n in asked if n.startswith("bb_")])
        # Vẫn phải hỏi những cái của làng nhà, nếu không là đã chặn nhầm.
        self.assertIn("attack_button", asked)

    def test_builder_base_mode_still_asks_them(self):
        asked = self._names_asked("builder_base")
        self.assertIn("bb_find_match", asked)
        self.assertIn("bb_side_label", asked)
        self.assertIn("bb_battle_hud", asked)

    def test_default_is_home_village(self):
        """Mặc định phải là làng nhà — chế độ mọi người dùng."""
        self.assertEqual([], [n for n in self._names_asked(None)
                              if n.startswith("bb_")])


class CoarseScreenTest(unittest.TestCase):
    """Vòng lọc thô không được bỏ sót thứ đường cũ tìm ra."""

    @staticmethod
    def _scene_with(patch: np.ndarray, at: tuple[int, int]) -> np.ndarray:
        rng = np.random.default_rng(7)
        scene = rng.integers(0, 255, (1080, 1350), dtype=np.uint8)
        x, y = at
        scene[y:y + patch.shape[0], x:x + patch.shape[1]] = patch
        return scene

    def _both_paths(self, gray_ss, tmpl, threshold):
        half = cv2.resize(gray_ss, None, fx=0.5, fy=0.5,
                          interpolation=cv2.INTER_AREA)
        old_margin = srmod.COARSE_MARGIN
        try:
            srmod.COARSE_MARGIN = 99.0        # tắt lọc = đường cũ
            slow = ScreenReader._ui_match_once(gray_ss, half, tmpl, threshold)
            srmod.COARSE_MARGIN = old_margin
            fast = ScreenReader._ui_match_once(gray_ss, half, tmpl, threshold)
        finally:
            srmod.COARSE_MARGIN = old_margin
        return slow, fast

    def test_a_present_button_is_found_identically(self):
        rng = np.random.default_rng(3)
        tmpl = rng.integers(0, 255, (72, 173), dtype=np.uint8)
        scene = self._scene_with(tmpl, (606, 780))
        slow, fast = self._both_paths(scene, tmpl, 0.70)

        self.assertGreaterEqual(fast[0], 0.70)
        self.assertAlmostEqual(slow[0], fast[0], places=5)
        self.assertEqual(slow[1], fast[1])

    def test_an_absent_button_stays_absent(self):
        rng = np.random.default_rng(4)
        tmpl = rng.integers(0, 255, (72, 173), dtype=np.uint8)
        scene = self._scene_with(np.zeros((72, 173), np.uint8), (100, 100))
        slow, fast = self._both_paths(scene, tmpl, 0.70)

        self.assertLess(slow[0], 0.70)
        self.assertLess(fast[0], 0.70)

    def test_a_template_too_small_to_halve_skips_the_screen(self):
        """Mẫu 6px chia đôi còn 3px — lọc thô ở cỡ đó là nhiễu, phải bỏ qua."""
        rng = np.random.default_rng(5)
        tmpl = rng.integers(0, 255, (6, 6), dtype=np.uint8)
        scene = self._scene_with(tmpl, (400, 400))
        slow, fast = self._both_paths(scene, tmpl, 0.70)
        self.assertAlmostEqual(slow[0], fast[0], places=5)

    def test_margin_covers_the_measured_drift(self):
        """Biên phải rộng hơn hẳn mức lệch tệ nhất đo được (+0.078)."""
        self.assertGreater(srmod.COARSE_MARGIN, 0.078 * 1.5)


if __name__ == "__main__":
    unittest.main()
