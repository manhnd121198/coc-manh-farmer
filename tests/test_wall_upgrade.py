"""Tự nâng tường: chọn tài nguyên nào, và những chốt chặn trước khi tiêu.

Mỗi lần chạy tiêu vài triệu vàng thật và không hoàn lại được, nên phần
lớn test ở đây là về chuyện KHÔNG bấm: bấm nhầm nút elixir, chọn quá số
đoạn, hay bấm Ok trên một hộp thoại nói về công trình khác.
"""

import unittest
from unittest import mock

import numpy as np

from logic.home_village import HomeVillageLogic
from logic.wall_upgrader import WallUpgrader, is_wall_text


def _settings(**over):
    values = {
        "wall_upgrade_enabled": True,
        "wall_upgrade_min_gold": 12_000_000,
        "wall_upgrade_min_elixir": 12_000_000,
        "wall_upgrade_use_elixir": True,
        "wall_upgrade_segments": 5,
    }
    values.update(over)
    s = mock.Mock()
    s.get.side_effect = lambda k, d=None: values.get(k, d)
    return s


class CurrencyChoiceTest(unittest.TestCase):
    def _pick(self, gold, elixir, **over):
        logic = HomeVillageLogic({}, mock.Mock(), mock.Mock(), mock.Mock())
        with mock.patch("logic.home_village.Settings",
                        return_value=_settings(**over)):
            return logic._wall_currency({"gold": gold, "elixir": elixir})

    def test_off_by_default_whatever_the_storage(self):
        self.assertIsNone(self._pick(20_000_000, 20_000_000,
                                     wall_upgrade_enabled=False))

    def test_gold_is_preferred_when_both_are_over(self):
        """Elixir cũng dùng để nuôi quân, vàng thì hay nằm đầy kho."""
        self.assertEqual("gold", self._pick(13_000_000, 13_000_000))

    def test_elixir_takes_over_when_gold_is_short(self):
        self.assertEqual("elixir", self._pick(2_000_000, 13_000_000))

    def test_elixir_can_be_refused(self):
        self.assertIsNone(self._pick(2_000_000, 13_000_000,
                                     wall_upgrade_use_elixir=False))

    def test_nothing_happens_below_both_thresholds(self):
        self.assertIsNone(self._pick(11_999_999, 11_999_999))

    def test_a_failed_read_is_not_treated_as_empty(self):
        """0/0 nghĩa là OCR hỏng. Ngưỡng 0 cộng khung hình lỗi không được
        biến thành một lệnh tiêu tiền."""
        self.assertIsNone(self._pick(0, 0, wall_upgrade_min_gold=0,
                                     wall_upgrade_min_elixir=0))


class WallTextTest(unittest.TestCase):
    """Bộ nhận chữ phải chịu được đúng những gì OCR thực sự trả về."""

    REAL_READS = [
        "Tuong THaNH (cap 13)",          # tiêu đề, 1 đoạn
        "3 Tong tHaNH (cap 13)",         # tiêu đề, 3 đoạn — mất chữ 'u'
        "Tuong +HanH (cap 14)",          # tiêu đề sau khi nâng
        "(Tuong hanh x6?",               # dòng trong danh sách
        "ban co thuc su muon nang cap cac buong thanh da chon bang 80ooooo vang khong?",
    ]
    # Những dòng có thật trong CÙNG danh sách, tuyệt đối không được khớp.
    DECOYS = [
        "Thap san Tuong",                # có 'ong'
        "Hoi truong Tuong",              # có 'ong'
        "Doanh trai Quan doi x4",        # có 'anh'
        "May nem banh",                  # có 'anh'
        "Bom Khong lo x3",               # có 'ong'
        "Xuong che tao",                 # có 'ong'
        "Phong thi nghiem",              # có 'ong'
    ]

    def test_every_real_reading_is_recognised(self):
        for text in self.REAL_READS:
            self.assertTrue(is_wall_text(text), text)

    def test_no_other_row_in_the_list_is_mistaken_for_walls(self):
        for text in self.DECOYS:
            self.assertFalse(is_wall_text(text), text)


class _Bar:
    """Một hàng nút giả: mỗi nút một ô, có thể gắn màu tiền tệ."""

    W, H = 126, 125

    def __init__(self, centres, gold_at=None, elixir_at=None, red_at=()):
        self.img = np.zeros((1080, 1350, 3), dtype=np.uint8)
        self.buttons = []
        for i, cx in enumerate(centres):
            b = (cx, 880, self.W, self.H)
            self.buttons.append(b)
            l, t = cx - self.W // 2, 880 - self.H // 2
            self.img[t:t + self.H, l:l + self.W] = (235, 235, 235)
            icon = self.img[t + 6:t + 34, l + 98:l + self.W]
            if i == gold_at:
                icon[:] = (0, 200, 255)          # vàng
            elif i == elixir_at:
                icon[:] = (230, 0, 200)          # hồng
            if i in red_at:
                self.img[t + 2:t + 30, l + 8:l + 80] = (0, 0, 255)


class ButtonReadingTest(unittest.TestCase):
    def test_currency_comes_from_colour_not_position(self):
        """Đây là chốt quan trọng nhất: hai nút 'Nâng cấp' giống hệt nhau,
        ảnh mẫu cả nút khớp 0.940 vào nút sai. Chỉ màu mới tách được."""
        bar = _Bar([371, 529, 675, 824, 971], gold_at=3, elixir_at=4)
        wu = WallUpgrader(None, None)
        found = wu.upgrade_buttons(bar.img)
        self.assertEqual(824, found["gold"][0])
        self.assertEqual(971, found["elixir"][0])

    def test_the_same_works_when_the_row_loses_a_button(self):
        """Thanh nút căn giữa: bớt một nút là cả hàng dịch ~72px."""
        bar = _Bar([452, 602, 750, 898], gold_at=2, elixir_at=3)
        found = WallUpgrader(None, None).upgrade_buttons(bar.img)
        self.assertEqual(750, found["gold"][0])
        self.assertEqual(898, found["elixir"][0])

    def test_a_red_price_is_seen(self):
        bar = _Bar([824, 971], gold_at=0, elixir_at=1, red_at=(0,))
        wu = WallUpgrader(None, None)
        self.assertTrue(wu.price_is_red(bar.img, bar.buttons[0]))
        self.assertFalse(wu.price_is_red(bar.img, bar.buttons[1]))

    def test_add_button_is_anchored_to_the_gold_button(self):
        """'+1' và '+10' không phân biệt được (khớp chéo 0.882), nên nó
        được lấy theo nút vàng — thứ duy nhất nhận diện chắc chắn."""
        bar = _Bar([371, 529, 675, 824, 971], gold_at=3, elixir_at=4)
        wu = WallUpgrader(None, None)
        add = wu._add_button(bar.img, bar.buttons)
        self.assertEqual(675, add[0])


class SafetyGateTest(unittest.TestCase):
    """Không tiêu tiền khi có bất cứ điều gì không khớp."""

    def _upgrader(self, text):
        wu = WallUpgrader(None, None)
        wu.confirm_text = lambda _ss: text
        return wu

    def _confirm(self, text, currency="gold"):
        wu = self._upgrader(text)
        frame = np.zeros((1080, 1350, 3), dtype=np.uint8)
        with mock.patch("logic.wall_upgrader.screencap", return_value=frame), \
                mock.patch.object(WallUpgrader, "_tap_fraction") as tapped, \
                mock.patch("logic.wall_upgrader.time.sleep"):
            ok = wu._confirm(currency)
        pressed = [c.args[1] for c in tapped.call_args_list]
        return ok, pressed

    def test_the_real_dialog_is_accepted(self):
        from logic.wall_upgrader import CONFIRM_OK
        ok, pressed = self._confirm(
            "ban co thuc su muon nang cap cac buong thanh da chon "
            "bang 80ooooo vang khong?")
        self.assertTrue(ok)
        self.assertIn(CONFIRM_OK, pressed)

    def test_cancels_when_the_dialog_names_the_other_resource(self):
        """Bấm nhầm nút elixir ở bước trước vẫn phải chặn được ở đây."""
        from logic.wall_upgrader import CONFIRM_CANCEL
        ok, pressed = self._confirm(
            "nang cap cac buong thanh bang 6000000 tien duoc khong?",
            currency="gold")
        self.assertFalse(ok)
        self.assertIn(CONFIRM_CANCEL, pressed)

    def test_cancels_when_the_dialog_is_not_about_walls(self):
        from logic.wall_upgrader import CONFIRM_CANCEL
        ok, pressed = self._confirm(
            "nang cap phao dai bang 10000000 vang khong?")
        self.assertFalse(ok)
        self.assertIn(CONFIRM_CANCEL, pressed)

    def test_cancels_when_the_dialog_cannot_be_read(self):
        from logic.wall_upgrader import CONFIRM_CANCEL
        ok, pressed = self._confirm("")
        self.assertFalse(ok)
        self.assertIn(CONFIRM_CANCEL, pressed)


class ListSearchTest(unittest.TestCase):
    """Dòng tường thành KHÔNG cố định ở cuối danh sách.

    Danh sách sắp theo giá, mà giá một đoạn tường tăng theo cấp, nên dòng
    đó trôi dần qua danh sách. Hôm dựng tính năng nó tình cờ nằm áp chót.
    Nếu chỉ đọc màn cuối thì không chỉ bỏ sót — bên gọi coi "không thấy
    tường" là "tường đã max" và tắt hẳn cấu hình.
    """

    def _search(self, screens):
        """``screens`` là kết quả find_wall_row cho từng màn lần lượt."""
        wu = WallUpgrader(None, None)
        frame = np.zeros((1080, 1350, 3), dtype=np.uint8)
        seq = list(screens)
        reads = []

        def fake_find(_ss):
            r = seq.pop(0) if seq else (None, 4)
            reads.append(r)
            return r

        wu.find_wall_row = fake_find
        # Danh sách "chạy hết" khi seq cạn.
        wu._panel_settled = lambda _b, _a: not seq
        with mock.patch("logic.wall_upgrader.screencap", return_value=frame), \
                mock.patch("logic.wall_upgrader.time.sleep"), \
                mock.patch.object(WallUpgrader, "_scroll"):
            row, lines = wu.search_for_wall_row()
        return row, lines, len(reads)

    def test_a_row_on_the_first_screen_is_taken_without_scrolling_past(self):
        row, _lines, reads = self._search([((600, 300), 12)])
        self.assertEqual((600, 300), row)
        self.assertEqual(1, reads)

    def test_a_row_in_the_middle_is_found(self):
        """Đây là ca mà bản cũ bỏ sót hoàn toàn."""
        row, _lines, reads = self._search([
            (None, 12), (None, 12), ((600, 568), 11), (None, 12),
        ])
        self.assertEqual((600, 568), row)
        self.assertEqual(3, reads)

    def test_a_row_only_on_the_last_screen_is_still_found(self):
        row, _lines, _ = self._search([(None, 12), ((600, 568), 11)])
        self.assertEqual((600, 568), row)

    def test_the_settled_screen_is_not_read_twice(self):
        """Cuộn không nhúc nhích nghĩa là vẫn màn cũ — đọc lại vừa tốn
        thêm một lượt OCR vừa đếm trùng số dòng."""
        _row, _lines, reads = self._search([(None, 12), (None, 11)])
        self.assertEqual(2, reads)

    def test_lines_are_totalled_across_every_screen(self):
        """Số dòng đọc được quyết định có tắt cấu hình hay không, nên nó
        phải là tổng cả danh sách chứ không phải riêng màn cuối."""
        _row, lines, _ = self._search([(None, 12), (None, 11), (None, 9)])
        self.assertEqual(32, lines)

    def test_only_reports_missing_after_the_whole_list(self):
        row, lines, _ = self._search([(None, 12), (None, 11)])
        self.assertIsNone(row)
        self.assertGreaterEqual(lines, 23)


class WallsFinishedTest(unittest.TestCase):
    """Hết tường để nâng thì tự tắt cấu hình — nhưng đọc hụt thì không."""

    def _missing(self, lines):
        wu = WallUpgrader(None, None)
        s = _settings()
        with mock.patch("logic.wall_upgrader.Settings", return_value=s):
            wu._handle_missing_row(lines)
        return s

    def test_a_legible_list_without_walls_turns_the_feature_off(self):
        s = self._missing(23)
        s.set.assert_called_once_with("wall_upgrade_enabled", False)
        s.save.assert_called_once()

    def test_the_change_is_written_to_disk(self):
        """set() chỉ đổi trong bộ nhớ; không save() thì mở lại là bật lại."""
        s = self._missing(23)
        self.assertTrue(s.save.called)

    def test_an_unreadable_list_leaves_the_setting_alone(self):
        """OCR đã từng đọc 'Tường' thành 'Tong' — một khung hình xui không
        được phép tắt tính năng người dùng vừa bật."""
        for lines in (0, 1, 4):
            s = self._missing(lines)
            s.set.assert_not_called()
            s.save.assert_not_called()

    def test_the_run_stops_and_spends_nothing(self):
        wu = WallUpgrader(None, None)
        frame = np.zeros((1080, 1350, 3), dtype=np.uint8)
        wu.search_for_wall_row = lambda: (None, 23)
        with mock.patch("logic.wall_upgrader.screencap", return_value=frame), \
                mock.patch("logic.wall_upgrader.Settings",
                           return_value=_settings()), \
                mock.patch("logic.wall_upgrader.tap") as tapped, \
                mock.patch.object(WallUpgrader, "_tap_fraction"), \
                mock.patch("logic.wall_upgrader.time.sleep"), \
                mock.patch("logic.wall_upgrader._adb_run"):
            self.assertFalse(wu.run(5, "gold"))
        tapped.assert_not_called()


class SelectionLoopTest(unittest.TestCase):
    """Số đoạn được ĐỌC LẠI sau mỗi lần bấm, không phải đếm số lần bấm."""

    def _grow(self, counts, want=5):
        wu = WallUpgrader(None, None)
        frame = np.zeros((1080, 1350, 3), dtype=np.uint8)
        seq = list(counts)
        wu.selected_count = lambda _ss: seq.pop(0) if seq else None
        wu.find_action_buttons = lambda _ss: [(675, 880, 126, 125)]
        wu._add_button = lambda _ss, _b: (675, 880, 126, 125)
        with mock.patch("logic.wall_upgrader.screencap", return_value=frame), \
                mock.patch("logic.wall_upgrader.tap") as tapped, \
                mock.patch("logic.wall_upgrader.time.sleep"):
            ok = wu._grow_selection(want)
        return ok, tapped.call_count

    def test_stops_as_soon_as_the_title_reaches_the_target(self):
        ok, taps = self._grow([1, 2, 2, 3, 3, 4, 4, 5, 5])
        self.assertTrue(ok)
        self.assertEqual(4, taps)          # 1 -> 5 là bốn lần bấm

    def test_a_press_that_does_nothing_aborts_instead_of_retrying(self):
        """Bấm hụt mà cứ bấm lại thì có ngày trúng '+10'."""
        ok, _ = self._grow([1, 1])
        self.assertFalse(ok)

    def test_overshooting_aborts_rather_than_spending(self):
        """Lỡ trúng '+10' thì dừng hẳn, không nâng 11 đoạn."""
        ok, _ = self._grow([1, 11])
        self.assertFalse(ok)

    def test_an_unreadable_title_aborts(self):
        ok, taps = self._grow([None])
        self.assertFalse(ok)
        self.assertEqual(0, taps)


if __name__ == "__main__":
    unittest.main()
