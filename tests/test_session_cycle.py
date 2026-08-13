"""Test cho chu kỳ chơi — nghỉ.

Ba thứ phải đúng.

Một: tắt tính năng thì nó phải im hoàn toàn. Đây là tính năng tự ý đóng
game của người dùng, nên rò rỉ một lần thôi cũng đã là mất trận.

Hai: không bao giờ tắt game giữa trận. Tắt lúc đang đánh là mất sạch quân
đã thả mà vẫn mất phí tìm trận. Đến hạn mà đang đánh thì phải chờ.

Ba: nút chạy thử phải chạy đúng MỘT chu kỳ rồi trả mọi thứ về như cũ —
nó kiểm tra cơ chế, không phải bật tính năng hộ người dùng.

Đồng hồ được tiêm vào nên test tua thẳng qua một tiếng thay vì ngồi chờ.
"""

import unittest
from unittest import mock

from core.session_cycle import SessionCycle
from core.state_machine import GameState


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeRng:
    """Luôn lấy giữa dải — để test khẳng định được con số cụ thể."""

    def uniform(self, lo, hi):
        return (lo + hi) / 2.0


def _settings(**over):
    data = {
        "session_cycle_enabled": True,
        "session_play_min_min": 60.0,
        "session_play_max_min": 75.0,
        "session_break_min_min": 5.0,
        "session_break_max_min": 10.0,
    }
    data.update(over)
    fake = mock.Mock()
    fake.get.side_effect = lambda key, default=None: data.get(key, default)
    return fake


def _cycle(clock=None, **over):
    clock = clock or FakeClock()
    return SessionCycle(settings=_settings(**over), clock=clock, rng=FakeRng()), clock


class DisabledIsSilentTest(unittest.TestCase):
    def test_start_arms_nothing_when_disabled(self):
        cyc, clock = _cycle(session_cycle_enabled=False)
        self.assertIsNone(cyc.start())
        clock.advance(10 * 3600)
        self.assertFalse(cyc.due())

    def test_never_due_before_start(self):
        cyc, clock = _cycle()
        clock.advance(10 * 3600)
        self.assertFalse(cyc.due(), "chưa start mà đã đòi nghỉ")

    def test_cancel_disarms(self):
        cyc, clock = _cycle()
        cyc.start()
        clock.advance(80 * 60)
        self.assertTrue(cyc.due())
        cyc.cancel()
        self.assertFalse(cyc.due())


class PlayWindowTest(unittest.TestCase):
    def test_the_default_band_is_one_hour_to_one_fifteen(self):
        cyc, _ = _cycle()
        lo, hi = cyc.play_band_sec()
        self.assertEqual((lo, hi), (3600.0, 4500.0))

    def test_the_default_break_band_is_five_to_ten_minutes(self):
        cyc, _ = _cycle()
        lo, hi = cyc.break_band_sec()
        self.assertEqual((lo, hi), (300.0, 600.0))

    def test_not_due_until_the_window_is_up(self):
        cyc, clock = _cycle()
        cyc.start()                      # giữa dải = 67.5 phút
        clock.advance(67 * 60)
        self.assertFalse(cyc.due())
        clock.advance(60)
        self.assertTrue(cyc.due())

    def test_the_window_is_rolled_not_fixed(self):
        rolls = set()
        for _ in range(40):
            cyc = SessionCycle(settings=_settings(), clock=FakeClock())
            rolls.add(round(cyc.start(), 3))
        self.assertGreater(len(rolls), 1, "đoạn chơi không đổi giữa các phiên")
        self.assertTrue(all(3600.0 <= r <= 4500.0 for r in rolls))

    def test_the_break_is_rolled_not_fixed(self):
        rolls = set()
        for _ in range(40):
            clock = FakeClock()
            cyc = SessionCycle(settings=_settings(), clock=clock)
            cyc.start()
            clock.advance(5 * 3600)
            rolls.add(round(cyc.take_break(), 3))
        self.assertGreater(len(rolls), 1, "thời gian nghỉ không đổi")
        self.assertTrue(all(300.0 <= r <= 600.0 for r in rolls))

    def test_a_backwards_band_is_read_the_right_way_round(self):
        cyc, _ = _cycle(session_play_min_min=75.0, session_play_max_min=60.0)
        self.assertEqual(cyc.play_band_sec(), (3600.0, 4500.0))

    def test_garbage_falls_back_to_the_defaults(self):
        cyc, _ = _cycle(session_play_min_min="x", session_play_max_min=None)
        self.assertEqual(cyc.play_band_sec(), (3600.0, 4500.0))

    def test_zero_is_clamped_so_it_cannot_loop_instantly(self):
        cyc, _ = _cycle(session_play_min_min=0.0, session_play_max_min=0.0)
        lo, hi = cyc.play_band_sec()
        self.assertGreater(lo, 0.0)
        self.assertEqual(lo, hi)

    def test_take_break_closes_the_window(self):
        cyc, clock = _cycle()
        cyc.start()
        clock.advance(5 * 3600)
        cyc.take_break()
        self.assertFalse(cyc.due(), "nghỉ xong vẫn còn đòi nghỉ nữa")

    def test_start_after_a_break_opens_a_fresh_window(self):
        cyc, clock = _cycle()
        cyc.start()
        clock.advance(5 * 3600)
        cyc.take_break()
        cyc.start()
        self.assertFalse(cyc.due())
        clock.advance(68 * 60)
        self.assertTrue(cyc.due())


class OverdueTest(unittest.TestCase):
    def test_overdue_counts_from_the_first_due_check(self):
        cyc, clock = _cycle()
        cyc.start()
        clock.advance(68 * 60)
        self.assertTrue(cyc.due())
        self.assertEqual(cyc.overdue_sec(), 0.0)
        clock.advance(90)
        self.assertEqual(cyc.overdue_sec(), 90.0)

    def test_overdue_is_zero_while_still_playing(self):
        cyc, clock = _cycle()
        cyc.start()
        clock.advance(10 * 60)
        cyc.due()
        self.assertEqual(cyc.overdue_sec(), 0.0)


class TestCycleTest(unittest.TestCase):
    def test_the_test_cycle_runs_with_the_feature_off(self):
        cyc, clock = _cycle(session_cycle_enabled=False)
        cyc.start()
        cyc.arm_test(30.0, 15.0)
        clock.advance(29)
        self.assertFalse(cyc.due())
        clock.advance(2)
        self.assertTrue(cyc.due())
        self.assertEqual(cyc.take_break(), 15.0)

    def test_the_test_cycle_does_not_turn_the_feature_on(self):
        cyc, clock = _cycle(session_cycle_enabled=False)
        cyc.arm_test(30.0, 15.0)
        clock.advance(31)
        cyc.take_break()
        cyc.start()                       # engine gọi sau mỗi lần nghỉ
        clock.advance(10 * 3600)
        self.assertFalse(cyc.due(), "chạy thử xong lại tự chạy chu kỳ thật")

    def test_the_feature_keeps_running_after_a_test_cycle(self):
        cyc, clock = _cycle()
        cyc.arm_test(30.0, 15.0)
        clock.advance(31)
        cyc.take_break()
        cyc.start()
        clock.advance(68 * 60)
        self.assertTrue(cyc.due())

    def test_the_break_after_a_test_is_the_test_length_not_the_band(self):
        cyc, clock = _cycle()             # dải thật là 5-10 phút
        cyc.arm_test(30.0, 15.0)
        clock.advance(31)
        self.assertEqual(cyc.take_break(), 15.0)

    def test_the_flag_says_a_test_is_running(self):
        cyc, _ = _cycle()
        cyc.start()
        self.assertFalse(cyc.is_test)
        cyc.arm_test(30.0, 15.0)
        self.assertTrue(cyc.is_test)
        cyc.start()
        self.assertFalse(cyc.is_test)


class WhenItIsSafeToCloseTest(unittest.TestCase):
    """``BotEngine._may_break_now`` — cái quyết định có mất trận hay không."""

    def _engine(self, overdue: float):
        from core.bot_engine import BotEngine

        engine = BotEngine.__new__(BotEngine)
        engine._session = mock.Mock()
        engine._session.overdue_sec.return_value = overdue
        return engine

    def test_never_closes_mid_battle_however_overdue(self):
        engine = self._engine(overdue=10 * 3600)
        for state in (GameState.IN_BATTLE, GameState.BB_BATTLE,
                      GameState.BB_BATTLE_STAGE2):
            self.assertFalse(engine._may_break_now(state), state.name)

    def test_closes_at_the_village(self):
        engine = self._engine(overdue=0.0)
        self.assertTrue(engine._may_break_now(GameState.HOME))
        self.assertTrue(engine._may_break_now(GameState.BUILDER_BASE_HOME))

    def test_waits_out_a_search_before_closing(self):
        engine = self._engine(overdue=30.0)
        self.assertFalse(engine._may_break_now(GameState.SEARCHING))
        self.assertFalse(engine._may_break_now(GameState.BATTLE_ENDED))

    def test_closes_anyway_once_stuck_long_enough(self):
        from core.bot_engine import BREAK_OVERDUE_GRACE

        engine = self._engine(overdue=BREAK_OVERDUE_GRACE + 1)
        self.assertTrue(engine._may_break_now(GameState.UNKNOWN))
        self.assertTrue(engine._may_break_now(GameState.LOADING))

    def test_being_stuck_still_does_not_close_mid_battle(self):
        from core.bot_engine import BREAK_OVERDUE_GRACE

        engine = self._engine(overdue=BREAK_OVERDUE_GRACE * 10)
        self.assertFalse(engine._may_break_now(GameState.IN_BATTLE))


class EngineWiringTest(unittest.TestCase):
    def test_the_tick_asks_before_handling_the_screen(self):
        # Nghỉ xong thì ảnh trên tay đã cũ mấy phút — phải return chứ
        # không được đem ảnh đó đi lập kế hoạch đánh.
        import pathlib

        source = pathlib.Path("core/bot_engine.py").read_text(encoding="utf-8")
        tick = source.split("def _tick(")[1]
        take = tick.index("_maybe_take_session_break")
        handle = tick.index("self._home_logic.handle")
        self.assertLess(take, handle, "tick xử màn hình trước khi hỏi chu kỳ")
        after = tick[take:take + 200]
        self.assertIn("return", after, "hỏi xong không return")

    def test_stopping_the_bot_disarms_the_cycle(self):
        import pathlib

        source = pathlib.Path("core/bot_engine.py").read_text(encoding="utf-8")
        stop = source.split("def stop_bot(")[1].split("def ")[0]
        self.assertIn("self._session.cancel()", stop)

    def test_the_break_sleep_can_be_cut_short(self):
        # Bấm Dừng lúc đang nghỉ phải dừng ngay, không phải đợi hết 10 phút.
        import pathlib

        source = pathlib.Path("core/bot_engine.py").read_text(encoding="utf-8")
        body = source.split("def _take_session_break(")[1].split("def ")[0]
        self.assertIn("while self._running and time.time() < end", body)


if __name__ == "__main__":
    unittest.main()
