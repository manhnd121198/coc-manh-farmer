"""Đọc kho của CHÍNH MÌNH khi vừa quay về làng.

Hai thứ dễ nhầm và dễ hỏng:

* Bảng góc trên TRÁI là chiến lợi phẩm của ĐỐI THỦ, chỉ hiện lúc đi soi
  và lúc đánh. Kho của mình nằm góc trên PHẢI. ``read_loot`` đọc cái
  trước, ``read_own_storage`` đọc cái sau — lẫn hai cái là ra con số của
  người khác.
* Một lần đọc tốn ~1.8 giây OCR. Nếu gọi mỗi tick khi đang ở làng thì nó
  nuốt trọn vòng lặp tìm base, nên nó chỉ được chạy đúng lúc BƯỚC VÀO.
"""

import unittest
from unittest import mock

import numpy as np

from core.state_machine import GameState
from logic.home_village import HomeVillageLogic


def _logic(ocr):
    logic = HomeVillageLogic({}, mock.Mock(), mock.Mock(), ocr)
    logic._engine = None
    return logic


class StorageOnEntryTest(unittest.TestCase):
    def setUp(self):
        self.ocr = mock.Mock()
        self.ocr.read_own_storage.return_value = {
            "gold": 12948175, "elixir": 11161530, "dark_elixir": 200000,
        }
        self.logic = _logic(self.ocr)
        self.frame = np.zeros((1080, 1350, 3), dtype=np.uint8)
        # Chặn cả bốn nhánh: các test ở đây chỉ nói về chuyện ĐỌC KHO,
        # không được kéo theo cả đường đánh thật.
        for name in ("_handle_home", "_handle_opponent_found",
                     "_handle_in_battle", "_handle_battle_ended"):
            patcher = mock.patch.object(self.logic, name)
            patcher.start()
            self.addCleanup(patcher.stop)

        # Và chặn cả nhánh nâng tường. Đọc kho xong là cân nhắc nâng
        # tường, mà số vàng giả ở dưới vượt ngưỡng thật trong
        # profiles/settings.json — nên nếu không chặn, bộ test sẽ CHẠM
        # VÀO MÁY THẬT: bấm ô thợ xây, vuốt cuộn 25 lần, bấm Back, cho
        # mỗi test một lần. Đã xảy ra: bộ test từ 0.3 giây vọt lên 154
        # giây và điều khiển máy trong lúc bot đang chạy.
        consider = mock.patch.object(self.logic, "_consider_wall_upgrade")
        self.consider = consider.start()
        self.addCleanup(consider.stop)

    def _handle(self, *states):
        for s in states:
            self.logic.handle(self.frame, s)

    def test_reads_once_on_arriving_home(self):
        self._handle(GameState.HOME)
        self.ocr.read_own_storage.assert_called_once()

    def test_does_not_read_again_while_still_home(self):
        """Đây là điểm mấu chốt: 1.8s mỗi tick sẽ giết vòng lặp."""
        self._handle(GameState.HOME, GameState.HOME, GameState.HOME)
        self.ocr.read_own_storage.assert_called_once()

    def test_reads_again_after_leaving_and_coming_back(self):
        self._handle(GameState.HOME, GameState.IN_BATTLE, GameState.HOME)
        self.assertEqual(2, self.ocr.read_own_storage.call_count)

    def test_never_reads_outside_the_village(self):
        self._handle(GameState.IN_BATTLE, GameState.OPPONENT_FOUND,
                     GameState.BATTLE_ENDED)
        self.ocr.read_own_storage.assert_not_called()

    def test_a_failed_read_does_not_break_the_tick(self):
        """OCR hỏng thì chỉ mất dòng log, không được làm sập cả lượt."""
        self.ocr.read_own_storage.side_effect = RuntimeError("EasyOCR chet")
        self._handle(GameState.HOME)          # không được ném ra ngoài
        self.logic._handle_home.assert_called_once()


class StorageReadTest(unittest.TestCase):
    def test_no_ocr_engine_gives_zeros_not_a_crash(self):
        from vision import ocr_reader
        frame = np.zeros((1080, 1350, 3), dtype=np.uint8)
        with mock.patch.object(ocr_reader, "_get_reader", return_value=None):
            got = ocr_reader.OCRReader().read_own_storage(frame)
        self.assertEqual({"gold": 0, "elixir": 0, "dark_elixir": 0}, got)

    def test_the_box_spans_the_home_row_pitch_not_the_scouting_one(self):
        """Ba dòng được chia đều làm ba dải, nên chiều cao vùng cắt phải
        vừa khít khoảng cách dòng của MÀN LÀNG NHÀ.

        Lần đầu tôi hiệu chỉnh trên màn soi base (dòng cách nhau ~50px)
        rồi dùng cho màn làng nhà (~80px). Kết quả trong log thật:
        'Vàng 23277 | Elixir 0 | Elixir đen 57761485' — các dải rơi vào
        khoảng trống giữa hai dòng và ghép số từ dòng sai.
        """
        from vision import ocr_reader as o
        height = (o.STORAGE_Y_END - o.STORAGE_Y_START) * 1080
        # Ba dòng ở y 34..62, 116..140, 194..218 -> cần khoảng 200px,
        # và phải dừng trước ô ngọc ở y~270.
        self.assertGreater(height, 180)
        self.assertLess(o.STORAGE_Y_END * 1080, 260)
        self.assertLess(o.STORAGE_Y_START * 1080, 34)

    def test_each_third_lands_on_exactly_one_row(self):
        """Chốt trực tiếp phép chia ba: mỗi dải phải ôm trọn một dòng và
        không chạm dòng nào khác."""
        from vision import ocr_reader as o
        top = o.STORAGE_Y_START * 1080
        strip = (o.STORAGE_Y_END - o.STORAGE_Y_START) * 1080 / 3
        rows = [(34, 62), (116, 140), (194, 218)]        # đo trên máy thật
        for i, (r0, r1) in enumerate(rows):
            lo, hi = top + i * strip, top + (i + 1) * strip
            self.assertLessEqual(lo, r0, "dải %d cắt mất đầu dòng" % i)
            self.assertGreaterEqual(hi, r1, "dải %d cắt mất đuôi dòng" % i)

    def test_the_box_sits_right_of_centre_and_above_the_middle(self):
        """Kho của mình ở góc trên PHẢI — nếu hằng số bị sửa nhầm sang
        vùng của bảng chiến lợi phẩm (trái) thì bắt được ở đây."""
        from vision import ocr_reader as o
        self.assertGreater(o.STORAGE_X_START, 0.5)
        self.assertLessEqual(o.STORAGE_X_END, 1.0)
        self.assertLess(o.STORAGE_Y_END, 0.5)
        self.assertLess(o.STORAGE_Y_START, o.STORAGE_Y_END)


if __name__ == "__main__":
    unittest.main()
