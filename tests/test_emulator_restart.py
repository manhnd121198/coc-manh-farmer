"""Giả lập treo thì bot phải lùi lại, không nện tiếp.

Trên máy thật, đường screencap-trả-None từng chạy 8 phút liền: nó chỉ
``return`` nên bộ đếm 10-lỗi ở ``run()`` không tăng, và cứ 60 giây bot
lại bắn thêm một cặp lệnh mở app vào con máy đang ngộp.

Test ở đây bám vào phần quyết định — đếm mấy lần thì lùi, mấy lần thì
khởi động lại, mấy lần thì bỏ cuộc — chứ không đụng tới ADB hay máy ảo.
"""

import unittest
from unittest import mock

from core import bot_engine as be


class _StubSM:
    def __init__(self):
        self.states = []

    def transition(self, state):
        self.states.append(state)

    def reset(self):
        self.states.append("RESET")


def _engine():
    """BotEngine trần, không chạy __init__ (nó dựng cả QThread lẫn OCR)."""
    eng = be.BotEngine.__new__(be.BotEngine)
    eng._running = True
    eng._paused = False
    eng._stall_strikes = 0
    eng._sm = _StubSM()
    eng.state_changed = mock.Mock()
    eng.error_occurred = mock.Mock()
    eng.session_note = mock.Mock()
    return eng


class DeviceStallTest(unittest.TestCase):
    def test_first_failures_are_quiet(self):
        """Một hai lần chụp hụt là chuyện thường — đừng báo động."""
        eng = _engine()
        with mock.patch.object(be.time, "sleep") as slept:
            for _ in range(be.DEVICE_STALL_STRIKES - 1):
                eng._handle_device_stall()
        self.assertEqual([], eng._sm.states)
        slept.assert_not_called()
        self.assertTrue(eng._running)

    def test_reports_disconnected_then_backs_off(self):
        eng = _engine()
        with mock.patch.object(be.time, "sleep") as slept:
            for _ in range(be.DEVICE_STALL_STRIKES):
                eng._handle_device_stall()
        self.assertIn(be.GameState.DISCONNECTED, eng._sm.states)
        slept.assert_called_once_with(be.DEVICE_STALL_BACKOFF)

    def test_gives_up_instead_of_hammering_forever(self):
        """Không bật tự khởi động lại thì phải dừng, không chạy mãi."""
        eng = _engine()
        settings = mock.Mock()
        settings.get.return_value = False          # emulator_auto_restart off
        with mock.patch.object(be, "Settings", return_value=settings), \
                mock.patch.object(be.time, "sleep"):
            for _ in range(be.DEVICE_STALL_GIVE_UP):
                eng._handle_device_stall()
        self.assertFalse(eng._running)
        eng.error_occurred.emit.assert_called()

    def test_restarts_emulator_when_enabled(self):
        eng = _engine()
        settings = mock.Mock()
        settings.get.return_value = True           # emulator_auto_restart on
        with mock.patch.object(be, "Settings", return_value=settings), \
                mock.patch.object(be.emulator, "is_available", return_value=True), \
                mock.patch.object(eng, "_restart_emulator") as restart, \
                mock.patch.object(be.time, "sleep"):
            for _ in range(be.DEVICE_STALL_RESTART):
                eng._handle_device_stall()
        restart.assert_called_once()
        self.assertTrue(eng._running)

    def test_no_restart_without_ldconsole(self):
        """Bật công tắc mà không có ldconsole.exe thì phải rơi về 'bỏ cuộc',
        chứ không được im lặng chạy tiếp mãi."""
        eng = _engine()
        settings = mock.Mock()
        settings.get.return_value = True
        with mock.patch.object(be, "Settings", return_value=settings), \
                mock.patch.object(be.emulator, "is_available", return_value=False), \
                mock.patch.object(eng, "_restart_emulator") as restart, \
                mock.patch.object(be.time, "sleep"):
            for _ in range(be.DEVICE_STALL_GIVE_UP):
                eng._handle_device_stall()
        restart.assert_not_called()
        self.assertFalse(eng._running)


class TestButtonTest(unittest.TestCase):
    """Nút Chạy thử phải đi qua đúng đường thật, và không được dừng bot."""

    def test_flag_is_consumed_once(self):
        eng = _engine()
        eng._executing_sequence = False
        eng._last_game_check = be.time.time()
        eng.request_test_emulator_restart()
        self.assertTrue(eng._test_emulator_restart)

        # Tick thứ hai phải đi tiếp chứ không tắt/bật lại lần nữa; chặn nó
        # ở check_connection cho khỏi chạm vào ADB thật.
        with mock.patch.object(eng, "_restart_emulator") as restart, \
                mock.patch.object(be, "check_connection", return_value=False), \
                mock.patch.object(be.time, "sleep"):
            eng._tick()
            eng._tick()

        # Đúng một lần: cờ còn sót lại thì bot tắt/bật giả lập mãi mãi.
        restart.assert_called_once()
        self.assertFalse(eng._test_emulator_restart)

    def test_failed_test_run_does_not_stop_the_bot(self):
        eng = _engine()
        with mock.patch.object(be.emulator, "restart", return_value=False):
            eng._restart_emulator(stop_on_failure=False)
        self.assertTrue(eng._running)
        eng.error_occurred.emit.assert_called()

    def test_failed_real_recovery_does_stop_the_bot(self):
        eng = _engine()
        with mock.patch.object(be.emulator, "restart", return_value=False):
            eng._restart_emulator()
        self.assertFalse(eng._running)


class ForegroundCheckTest(unittest.TestCase):
    """dumpsys hỏng KHÁC với 'game đã tắt'."""

    def _engine_for_check(self):
        eng = be.BotEngine.__new__(be.BotEngine)
        eng.game_not_installed = mock.Mock()
        return eng

    def test_unreadable_focus_does_not_launch(self):
        eng = self._engine_for_check()
        with mock.patch.object(be, "is_app_installed", return_value=True), \
                mock.patch.object(be, "get_focused_package", return_value=None), \
                mock.patch.object(be, "launch_app") as launch:
            self.assertFalse(eng._ensure_game_running(allow_launch=True))
        launch.assert_not_called()

    def test_another_app_focused_does_launch(self):
        eng = self._engine_for_check()
        settings = mock.Mock()
        settings.get.side_effect = lambda k, d=None: {
            "game_package": "com.supercell.clashofclans",
            "auto_launch_game": True,
        }.get(k, d)
        with mock.patch.object(be, "Settings", return_value=settings), \
                mock.patch.object(be, "is_app_installed", return_value=True), \
                mock.patch.object(be, "get_focused_package", return_value="com.ldmnq.launcher3"), \
                mock.patch.object(be, "launch_app", return_value=False) as launch:
            eng._ensure_game_running(allow_launch=True)
        launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
