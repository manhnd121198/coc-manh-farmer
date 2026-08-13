"""
Bot Engine — QThread main loop with continuous vision tick,
stuck detection, macro support, and V6 ROI calibration fallback.

V6 CHANGE:
  • On start_bot(), checks OCRReader.get_missing_calibrations().
  • If ROIs are missing, emits calibration_needed signal BEFORE
    entering the main loop. MainWindow shows InteractiveAssistDialog
    for each missing ROI and saves results to the profile.
  • OCRReader now receives the profile dict at construction.
"""

import time
import traceback

from PyQt5.QtCore import QThread, pyqtSignal

from core.logger import BotLogger
from core.adb_handler import screencap, tap, check_connection, press_back, press_home
from core.adb_handler import play_recording, load_recording
from core.adb_handler import (
    is_app_installed,
    is_game_running,
    launch_app,
    force_stop_app,
    get_focused_package,
)
from core import emulator
from core.session_cycle import SessionCycle
from core.state_machine import StateMachine, GameState
from vision.screen_reader import ScreenReader
from vision.ocr_reader import OCRReader
from vision.template_manager import get_sequence_readiness
from logic.home_village import HomeVillageLogic
from logic.builder_base import BuilderBaseLogic
from core.settings import Settings

log = BotLogger.get("engine")

SEQUENCE_SCAN_TIMEOUT = 12
SEQUENCE_SCAN_SLEEP = 0.8
ACTION_CHAIN_MAX_LOOPS = 15
ACTION_CHAIN_SLEEP = 1.0
STUCK_TIMEOUT = 20

# After tapping `attack_button2` (Find a Match) the game *sometimes*
# shows an extra Confirm popup — usually in Ranked mode, occasionally
# in Normal. We poll for `confirm_button` for this many seconds before
# giving up so we never miss it, and never freeze if it doesn't appear.
POST_ATTACK_CONFIRM_WAIT = 4.0
POST_ATTACK_CONFIRM_POLL = 0.5

# Chỉ tắt game khi đang đứng ở làng. Tắt giữa trận là mất quân đã thả mà
# vẫn mất phí tìm trận, nên đến hạn mà đang đánh dở thì chờ đánh xong.
BREAK_SAFE_STATES = (GameState.HOME, GameState.BUILDER_BASE_HOME)
# Không bao giờ tắt ở các màn này, dù đã quá hạn bao lâu.
BREAK_NEVER_STATES = (
    GameState.IN_BATTLE, GameState.BB_BATTLE, GameState.BB_BATTLE_STAGE2,
)
# Quá hạn lâu hơn ngần này mà vẫn chưa về được làng thì tắt luôn từ bất
# kỳ màn nào không phải đang đánh. Kẹt ở UNKNOWN/LOADING là lý do hay
# gặp nhất, mà tắt mở lại game chính là cách gỡ kẹt đó.
BREAK_OVERDUE_GRACE = 300.0
# Chờ game vào tới làng sau khi mở lại. CoC tải lâu, và hết thời gian
# này cũng không sao — vòng lặp chính tự xử tiếp.
GAME_READY_TIMEOUT = 180.0
GAME_READY_POLL = 2.0

# Thiết bị treo/bò: screencap trả None liên tục. Đây KHÔNG phải mất kết nối
# — `adb devices` vẫn xanh vì nó hỏi ADB server trên PC chứ không hỏi máy —
# nên phải đếm riêng, nếu không vòng lặp chính cứ nện lệnh vào một con máy
# đang ngộp cho tới khi người dùng tự tay dừng.
DEVICE_STALL_STRIKES = 5          # sau ngần này lần thì báo mất kết nối + lùi
DEVICE_STALL_BACKOFF = 15.0       # nghỉ bao lâu mỗi vòng khi đã lùi
DEVICE_STALL_RESTART = 8          # ngần này lần thì khởi động lại giả lập
DEVICE_STALL_GIVE_UP = 40         # ngần này lần thì dừng hẳn (khi không bật tự khởi động lại)


class BotEngine(QThread):
    """Central automation thread."""

    state_changed      = pyqtSignal(str)
    loot_read          = pyqtSignal(int, int, int)
    battle_status      = pyqtSignal(str)
    error_occurred     = pyqtSignal(str)
    bot_stopped        = pyqtSignal()
    help_needed        = pyqtSignal(object, str)     # (screenshot, reason)
    game_not_installed = pyqtSignal(str)             # package name
    stats_changed      = pyqtSignal(int, int)        # (attacks, skips)
    session_note       = pyqtSignal(str)             # chu kỳ chơi — nghỉ

    def __init__(self, profile: dict, mode: str = "home_village") -> None:
        super().__init__()
        self._running = False
        self._paused = False
        self._profile = profile
        self._mode = mode
        self._executing_sequence = False

        self._sm = StateMachine()
        self._screen_reader = ScreenReader()
        self._ocr = OCRReader()
        self._home_logic = HomeVillageLogic(self._profile, self._sm, self._screen_reader, self._ocr)
        self._bb_logic = BuilderBaseLogic(self._profile, self._sm, self._screen_reader, self._ocr)

        self._home_logic.set_engine(self)
        self._bb_logic.set_engine(self)

        # Stuck detection
        self._state_enter_time = time.time()
        self._help_already_requested = False

        # Game-presence periodic check (timestamps of the last verification).
        self._last_game_check: float = 0.0

        # Chu kỳ chơi — nghỉ (tắt game một lúc rồi mở lại).
        self._session = SessionCycle()
        self._break_wait_logged = False

        # Số lần screencap trả None liên tiếp — dấu hiệu giả lập treo.
        self._stall_strikes: int = 0
        # Nút "Chạy thử" bên tab Cài đặt bật cờ này; tick sau mới làm.
        self._test_emulator_restart: bool = False

        # Session tally — reset on every start_bot(), not on pause/resume.
        self._attack_count = 0
        self._skip_count = 0

    # ── Session tally ───────────────────────────────────────────────────

    def record_attack(self) -> None:
        """One battle entered. Called by the village logic, not the UI."""
        self._attack_count += 1
        log.info("SESSION: %d attack(s), %d skip(s).",
                 self._attack_count, self._skip_count)
        self.stats_changed.emit(self._attack_count, self._skip_count)

    def record_skip(self) -> None:
        """One village passed over because its loot was below threshold."""
        self._skip_count += 1
        self.stats_changed.emit(self._attack_count, self._skip_count)

    def record_attack_cancelled(self) -> None:
        """A battle already counted, then walked away from without deploying.

        Moves it from the attack column to the skip column so the tally
        keeps meaning "battles actually fought" — the count is armed the
        moment the bot commits to a base, which is before the planner gets
        its say.
        """
        if self._attack_count > 0:
            self._attack_count -= 1
        self._skip_count += 1
        log.info("SESSION: attack cancelled → %d attack(s), %d skip(s).",
                 self._attack_count, self._skip_count)
        self.stats_changed.emit(self._attack_count, self._skip_count)

    def reset_stats(self) -> None:
        self._attack_count = 0
        self._skip_count = 0
        self.stats_changed.emit(0, 0)

    # ── Control ─────────────────────────────────────────────────────────

    def _ensure_game_running(self, allow_launch: bool = True) -> bool:
        """Make sure the configured game package is the foreground app.

        Returns True only if (a) the package is installed and
        (b) it is now focused (already focused, or was successfully
        launched when ``allow_launch`` is True). Returns False if the
        app is not installed — caller should refuse to start the bot.
        """
        s = Settings()
        package = str(s.get("game_package", "com.supercell.clashofclans"))

        if not is_app_installed(package):
            log.error("Game package not installed on device: %s", package)
            self.game_not_installed.emit(package)
            return False

        # Ask ONCE. is_game_running() runs the same dumpsys internally, so
        # calling both doubles the traffic — and dumpsys is one of the first
        # things to stop answering when a device starts thrashing.
        focused = get_focused_package()

        if focused == package:
            return True

        if focused is None:
            # The CHECK failed, which is not the same as "the game is gone".
            # dumpsys returns None both when the window is unparseable and
            # when the call timed out, and launching an app at a device that
            # just failed to answer is the worst possible response: it was
            # already struggling and now it gets two more intents to service.
            log.warning(
                "Cannot read the foreground app (dumpsys gave nothing) — "
                "leaving the game alone instead of guessing it died.",
            )
            return False

        log.warning(
            "Game is not in the foreground (focused: %s). %s",
            focused,
            "Auto-launching…" if allow_launch and s.get("auto_launch_game", True)
            else "Skipping auto-launch.",
        )
        if allow_launch and s.get("auto_launch_game", True):
            if launch_app(package):
                # Give Android a moment to bring the app forward.
                time.sleep(2.0)
                return is_game_running(package)
        return False

    def start_bot(self) -> bool:
        """Start the bot thread.

        Returns False (and does NOT start the QThread) when the game is
        not installed on the device. The caller is responsible for
        listening to ``game_not_installed`` and showing a UI message.
        """
        # Soft readiness notice — missing assets are SKIPPED at runtime, not blocked.
        hv_seq = self._profile.get("hv_entry_sequence", [])
        bb_seq = self._profile.get("bb_entry_sequence", [])
        ready, seq_missing = get_sequence_readiness(hv_seq, bb_seq)
        if not ready and seq_missing:
            log.warning(
                "Starting with %d unmapped sequence asset(s) (will be skipped): %s",
                len(seq_missing), ", ".join(seq_missing),
            )

        # Hard guard: refuse to start if the configured game package is
        # not installed on the connected device. Other failure modes
        # (game not focused) are auto-recovered via launch_app().
        if not self._ensure_game_running(allow_launch=True):
            if not is_app_installed(str(Settings().get("game_package",
                                                        "com.supercell.clashofclans"))):
                log.error("Refusing to start bot — game package missing.")
                return False
            log.warning("Game could not be brought to foreground; "
                        "starting anyway and will keep retrying in the loop.")

        self._last_game_check = time.time()
        self.reset_stats()
        self._session.start()
        log.info("Starting bot (mode=%s).", self._mode)
        self._running = True
        self.start()
        return True

    def stop_bot(self) -> None:
        log.info("Bot engine STOP.")
        self._running = False
        self._paused = False
        self._session.cancel()

    # ── Chu kỳ chơi — nghỉ ──────────────────────────────────────────────

    def request_test_cycle(self, play_sec: float = 30.0, break_sec: float = 15.0) -> None:
        """Hẹn một chu kỳ ngắn để xem đóng/mở game có chạy không.

        Nút test trên tab Cài đặt gọi hàm này. Chạy đúng một lần và
        không cần bật tính năng — nó kiểm tra cơ chế, không phải bật
        tính năng hộ người dùng.
        """
        self._session.arm_test(play_sec, break_sec)
        self._break_wait_logged = False
        self.session_note.emit(
            f"Chạy thử: {play_sec:.0f}s nữa tắt game, {break_sec:.0f}s sau mở lại.",
        )

    def request_test_emulator_restart(self) -> None:
        """Hẹn một lần tắt/bật giả lập để xem cơ chế gỡ treo có chạy không.

        Chỉ đặt cờ; việc nặng để tick sau làm trên thread của engine. Gọi
        thẳng ``_restart_emulator()`` từ nút bấm sẽ khoá cứng giao diện
        vài phút, vì nó chờ máy ảo lên rồi chờ game vào tới làng.

        Không cần bật ``emulator_auto_restart``: nút này kiểm tra cơ chế,
        không phải bật tính năng hộ người dùng — giống nút chạy thử chu kỳ.
        """
        self._test_emulator_restart = True
        self.session_note.emit("Chạy thử: sắp tắt giả lập rồi bật lại…")

    def _may_break_now(self, detected: GameState) -> bool:
        """Có được tắt game ở màn hình hiện tại không."""
        if detected in BREAK_NEVER_STATES:
            return False
        if detected in BREAK_SAFE_STATES:
            return True
        # Không phải làng, cũng không phải đang đánh: màn tìm trận, màn
        # kết quả, hoặc kẹt ở UNKNOWN. Chờ một lúc cho nó tự về làng đã,
        # quá lâu thì tắt luôn.
        return self._session.overdue_sec() >= BREAK_OVERDUE_GRACE

    def _maybe_take_session_break(self, detected: GameState) -> bool:
        """True nghĩa là vừa nghỉ xong — tick này bỏ qua, chụp lại từ đầu."""
        if not self._session.due():
            return False

        if not self._may_break_now(detected):
            if not self._break_wait_logged:
                self._break_wait_logged = True
                log.info(
                    "Đến giờ nghỉ nhưng đang ở %s — chờ về làng rồi mới tắt game.",
                    detected.name,
                )
            return False

        self._break_wait_logged = False
        self._take_session_break(detected)
        return True

    def _take_session_break(self, detected: GameState) -> None:
        """Tắt game, ngồi chờ, mở lại, chờ vào tới làng."""
        was_test = self._session.is_test
        seconds = self._session.take_break()
        package = str(Settings().get("game_package", "com.supercell.clashofclans"))

        log.info(
            "NGHỈ PHIÊN%s: tắt %s ở màn %s, %.1f phút nữa mở lại.",
            " (chạy thử)" if was_test else "", package, detected.name, seconds / 60.0,
        )
        self.session_note.emit(f"Đang nghỉ — mở lại game sau {seconds / 60.0:.1f} phút.")
        force_stop_app(package)
        self._sm.reset()
        self.state_changed.emit(GameState.UNKNOWN.name)

        # Chờ có cắt được: bấm Dừng lúc đang nghỉ thì phải dừng ngay chứ
        # không phải đợi hết mười phút.
        end = time.time() + seconds
        while self._running and time.time() < end:
            time.sleep(0.5)
        if not self._running:
            log.info("Bot dừng trong lúc nghỉ — không mở lại game.")
            return

        log.info("Hết giờ nghỉ — mở lại %s.", package)
        self.session_note.emit("Đang mở lại game…")
        launch_app(package)
        self._last_game_check = time.time()
        ready = self._wait_for_game_ready()

        # Đồng hồ kẹt phải reset: màn tải của CoC lâu hơn STUCK_TIMEOUT,
        # không reset thì vừa vào game đã bị hỏi "bot có bị kẹt không".
        self._state_enter_time = time.time()
        self._help_already_requested = False

        if was_test:
            log.info(
                "CHẠY THỬ chu kỳ: %s. Chu kỳ thật %s.",
                "vào lại game OK" if ready else "mở lại rồi nhưng chưa thấy làng",
                "vẫn chạy tiếp" if self._session.enabled() else "đang tắt nên dừng ở đây",
            )
            self.session_note.emit(
                "Chạy thử xong — bot đã vào lại game."
                if ready else
                "Chạy thử: đã mở lại game nhưng chưa nhận ra màn làng.",
            )
        self._session.start()

    def _wait_for_game_ready(self) -> bool:
        """Chờ đến khi nhận ra màn làng. False = hết giờ chờ.

        Hết giờ cũng không sao: vòng lặp chính vẫn chạy tiếp và tự xử
        màn hình đang có. Chờ ở đây chỉ để không đâm thẳng vào logic
        đánh trong lúc game còn đang tải.
        """
        deadline = time.time() + GAME_READY_TIMEOUT
        while self._running and time.time() < deadline:
            time.sleep(GAME_READY_POLL)
            screenshot = screencap()
            if screenshot is None:
                continue
            state = self._screen_reader.detect_state(screenshot)
            if state in BREAK_SAFE_STATES:
                log.info("Đã vào lại game, đang ở %s.", state.name)
                return True
        if self._running:
            log.warning(
                "Mở lại game nhưng %.0f giây vẫn chưa thấy màn làng — "
                "để vòng lặp chính tự xử.", GAME_READY_TIMEOUT,
            )
        return False

    def _handle_device_stall(self) -> None:
        """screencap trả None — máy có thể đang treo.

        Đường này trước đây chỉ ``return`` lặng lẽ, nên bộ đếm 10-lỗi ở
        ``run()`` không bao giờ tăng (không có exception nào được ném) và
        bot nện lệnh vào máy chết cho tới khi người dùng tự tay dừng. Đo
        trên máy thật: 8 phút, ~50 lần screencap quá hạn.
        """
        self._stall_strikes += 1

        if self._stall_strikes < DEVICE_STALL_STRIKES:
            return

        if self._stall_strikes == DEVICE_STALL_STRIKES:
            log.error(
                "Máy không trả lời %d lần liên tiếp. ADB vẫn báo 'device' vì "
                "nó chỉ hỏi server trên PC — nhiều khả năng giả lập đang treo.",
                self._stall_strikes,
            )
            self._sm.transition(GameState.DISCONNECTED)
            self.state_changed.emit(GameState.DISCONNECTED.name)

        if (
            self._stall_strikes >= DEVICE_STALL_RESTART
            and Settings().get("emulator_auto_restart", False)
            and emulator.is_available()
        ):
            self._restart_emulator()
            return

        if self._stall_strikes >= DEVICE_STALL_GIVE_UP:
            log.critical(
                "Bỏ cuộc sau %d lần máy không trả lời — dừng bot. "
                "Khởi động lại giả lập bằng tay rồi bấm Start.",
                self._stall_strikes,
            )
            self.error_occurred.emit(
                "Giả lập không trả lời. Bot đã dừng — hãy khởi động lại giả lập.",
            )
            self._running = False
            return

        # Lùi lại thay vì nện tiếp. Mỗi lần thử vẫn tốn nguyên timeout của
        # screencap (10s), nên nghỉ thêm ở đây là để máy có cửa thở.
        time.sleep(DEVICE_STALL_BACKOFF)

    def _restart_emulator(self, stop_on_failure: bool = True) -> None:
        """Tắt hẳn giả lập rồi bật lại, sau đó chờ game vào tới làng.

        ``stop_on_failure`` False là đường của nút Chạy thử: hỏng thì báo
        rồi thôi. Dừng bot lúc đó là trừng phạt người dùng vì đã đi kiểm
        tra, mà giả lập lúc ấy vẫn đang chạy tốt.
        """
        log.warning("Đang khởi động lại giả lập…")
        self.session_note.emit("Đang tắt giả lập rồi bật lại…")

        if not emulator.restart():
            log.error("Khởi động lại giả lập thất bại.")
            self.session_note.emit("❌ Khởi động lại giả lập THẤT BẠI.")
            self.error_occurred.emit(
                "Không khởi động lại được giả lập."
                + (" Bot đã dừng." if stop_on_failure else ""),
            )
            if stop_on_failure:
                self._running = False
            return

        # Máy ảo lên không có nghĩa là ADB đã nối lại. Chờ tới khi chụp
        # được một tấm hình thật thì mới coi là xong.
        deadline = time.time() + GAME_READY_TIMEOUT
        while self._running and time.time() < deadline:
            if screencap() is not None:
                break
            time.sleep(GAME_READY_POLL)

        self._stall_strikes = 0
        self._sm.reset()
        self._state_enter_time = time.time()
        self._help_already_requested = False
        self._last_game_check = 0.0

        # Giả lập vừa bật lại thì game chưa chạy — mở rồi chờ vào làng.
        self.session_note.emit("Giả lập đã lên, đang mở game…")
        self._ensure_game_running(allow_launch=True)
        ready = self._wait_for_game_ready()
        if ready:
            self.session_note.emit("✅ Đã tắt/bật lại giả lập và vào được làng.")
            log.info("Đã gỡ treo xong, chạy tiếp.")
        else:
            self.session_note.emit(
                "⚠ Giả lập đã lên nhưng chưa thấy màn làng — vòng lặp chính tự xử.",
            )

    def pause(self) -> None:
        log.info("Bot engine PAUSED.")
        self._paused = True

    def resume(self) -> None:
        log.info("Bot engine RESUMED.")
        self._paused = False
        self._help_already_requested = False
        self._state_enter_time = time.time()

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    def update_profile(self, profile: dict) -> None:
        self._profile = profile
        self._home_logic.update_profile(profile)
        self._bb_logic.update_profile(profile)

    def handle_assist_result(self, action: str, data) -> None:
        """Called by MainWindow after InteractiveAssistDialog closes."""
        if action == "manual_tap" and data is not None:
            x, y = data
            tap(x, y)
            log.info("Assist: manual tap at (%d, %d).", x, y)
        elif action == "abort_home":
            press_back()
            time.sleep(0.5)
            press_home()
            log.info("Assist: aborting to home screen.")
        elif action == "saved_asset":
            self._screen_reader.clear_cache()
            log.info("Assist: new asset saved, cache cleared.")
        self.resume()

    def notify_assets_changed(self) -> None:
        """Called by MainWindow when the asset manifest changes."""
        self._screen_reader.clear_cache()
        log.info("Assets changed → template cache cleared.")

    # ── Main Loop ───────────────────────────────────────────────────────

    def run(self) -> None:
        log.info("Bot thread started.")
        consecutive_errors = 0

        while self._running:
            if self._paused:
                time.sleep(0.5)
                continue

            try:
                self._tick()
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                log.error("Tick error (#%d): %s\n%s", consecutive_errors, exc, traceback.format_exc())
                self.error_occurred.emit(str(exc))
                if consecutive_errors >= 10:
                    self._running = False
                    break

            time.sleep(Settings().get("tick_interval", 1.0))

        log.info("Bot thread exiting.")
        self.bot_stopped.emit()

    def _tick(self) -> None:
        if self._executing_sequence:
            return

        # Chạy thử gỡ treo — đi qua ĐÚNG đường thật, không mô phỏng.
        if self._test_emulator_restart:
            self._test_emulator_restart = False
            self._restart_emulator()
            return

        # ── Periodic game-presence check ──────────────────────────────
        # Runs at most once every Settings.game_check_interval seconds
        # so we don't spam dumpsys on every tick. If the user switched
        # to another app, try to bring CoC back to the foreground.
        #
        # Skipped entirely while the device is stalling: dumpsys is heavy
        # and a device that cannot answer screencap will not answer this
        # either, so all it does is add load to something already choking.
        interval = float(Settings().get("game_check_interval", 60))
        if (
            self._stall_strikes == 0
            and interval > 0
            and (time.time() - self._last_game_check) >= interval
        ):
            self._last_game_check = time.time()
            self._ensure_game_running(allow_launch=True)

        if not check_connection():
            self._sm.transition(GameState.DISCONNECTED)
            self.state_changed.emit(GameState.DISCONNECTED.name)
            time.sleep(5)
            return

        screenshot = screencap()
        if screenshot is None:
            self._handle_device_stall()
            return
        self._stall_strikes = 0

        detected = self._screen_reader.detect_state(screenshot)

        # Chu kỳ chơi — nghỉ. Hỏi trước khi xử màn hình: nếu vừa nghỉ
        # xong thì ảnh chụp trên tay đã cũ mấy phút, tick sau chụp lại.
        if self._maybe_take_session_break(detected):
            return

        prev_state = self._sm.state
        if detected != prev_state:
            self._sm.transition(detected)
            self.state_changed.emit(detected.name)
            self._state_enter_time = time.time()
            self._help_already_requested = False
        else:
            # ── Stuck Detection ───────────────────────────────────
            elapsed = time.time() - self._state_enter_time
            if (
                elapsed > STUCK_TIMEOUT
                and not self._help_already_requested
                and detected in (GameState.UNKNOWN, GameState.LOADING)
            ):
                log.warning(
                    "STUCK in %s for %.0f s — requesting user help.",
                    detected.name, elapsed,
                )
                self._help_already_requested = True
                self.pause()
                reason_msg = f"Stuck in '{detected.name}' state for {elapsed:.0f}s. Expected HOME buttons (attack_button / shop_button) or confirmation UI."
                self.help_needed.emit(screenshot, reason_msg)
                return

        # ── Handle state ────────────────────────────────────────────────
        if detected == GameState.DISCONNECTED:
            self._handle_disconnect(screenshot)
            return

        if detected in (GameState.CONFIRMING, GameState.BB_CONFIRMING):
            self._handle_action_chain()
            return

        if self._mode == "builder_base":
            self._bb_logic.handle(screenshot, detected)
        else:
            self._home_logic.handle(screenshot, detected)

    # ═══════════════════════════════════════════════════════════════════
    #  SEQUENCE EXECUTOR
    # ═══════════════════════════════════════════════════════════════════

    def execute_attack_sequence(self, sequence: list[str]) -> bool:
        if not sequence:
            return False

        self._executing_sequence = True
        log.info("SEQUENCE: %d steps…", len(sequence))

        for step_idx, template_key in enumerate(sequence):
            if not self._running or self._paused:
                self._executing_sequence = False
                return False

            log.info("SEQ [%d/%d]: scanning '%s'…", step_idx+1, len(sequence), template_key)
            found = False
            start_time = time.time()

            while time.time() - start_time < SEQUENCE_SCAN_TIMEOUT:
                if not self._running or self._paused:
                    self._executing_sequence = False
                    return False

                ss = screencap()
                if ss is None:
                    time.sleep(SEQUENCE_SCAN_SLEEP)
                    continue

                match = self._screen_reader.find_template_by_name(ss, template_key, 0.70)
                if match is not None:
                    tap(match[0], match[1])
                    log.info("SEQ [%d/%d]: tapped '%s' at (%d,%d).",
                             step_idx+1, len(sequence), template_key, match[0], match[1])
                    found = True
                    time.sleep(1.0 + step_idx * 0.2)
                    break
                time.sleep(SEQUENCE_SCAN_SLEEP)

            if not found:
                log.warning("SEQ [%d/%d]: '%s' not found — skipping.",
                            step_idx+1, len(sequence), template_key)

        self._executing_sequence = False
        return True

    # ── Macro Playback ──────────────────────────────────────────────────

    def execute_macro(self, filepath: str) -> None:
        events = load_recording(filepath)
        if events:
            self._executing_sequence = True
            play_recording(events)
            self._executing_sequence = False
        else:
            log.warning("Macro file empty or missing: %s", filepath)

    # ── Fallback Action Chain ───────────────────────────────────────────

    def _handle_action_chain(self) -> None:
        # Respect the user's HV match-mode choice (Normal vs Ranked).
        preferred_mode = (
            "ranked_mode_btn"
            if str(self._profile.get("hv_match_mode", "normal")).lower() == "ranked"
            else "normal_mode_btn"
        )
        rejected_mode = (
            "normal_mode_btn" if preferred_mode == "ranked_mode_btn" else "ranked_mode_btn"
        )

        # Tiered priority — lower tier = tapped first.
        #  Tier -1: blocking modals that sit OVER everything else. Whatever
        #           else scores a match while one is up is showing through
        #           the dimmed village behind it, so clear this first.
        #   Tier 0: pick the Mode tab the user chose (Normal / Ranked).
        #   Tier 1: press the FINAL confirm — attack_button2 or confirm_button
        #           (whichever appears, they are alternates with different text
        #           but the same role: start matchmaking).
        #   Tier 2: fallback popups (end-battle confirm, disconnect reload).
        priority = {
            "ok_button":         -1,
            preferred_mode:       0,
            "attack_button2":     1,
            "confirm_button":     1,
            "end_battle_confirm": 2,
            "reload_button":      2,
        }

        for attempt in range(ACTION_CHAIN_MAX_LOOPS):
            if not self._running or self._paused:
                return
            screenshot = screencap()
            if screenshot is None:
                time.sleep(ACTION_CHAIN_SLEEP)
                continue
            new_state = self._screen_reader.detect_state(screenshot)
            if new_state not in (GameState.CONFIRMING, GameState.BB_CONFIRMING, GameState.UNKNOWN):
                self._sm.transition(new_state)
                self.state_changed.emit(new_state.name)
                return
            confirmations = self._screen_reader.scan_for_confirmations(screenshot)
            if confirmations:
                # Never tap the rejected match-mode tab.
                confirmations = [c for c in confirmations if c[0] != rejected_mode]
                confirmations.sort(
                    key=lambda c: (priority.get(c[0], 99), -c[3]),
                )
                if confirmations:
                    name, cx, cy, _ = confirmations[0]
                    log.debug("ACTION-CHAIN tap '%s' at (%d,%d)", name, cx, cy)
                    tap(cx, cy)
                    # ── Post-attack Confirm grace window ──────────────
                    # The "Find a Match" button (attack_button2) is
                    # sometimes followed by an extra Confirm popup
                    # (always in Ranked, occasionally in Normal). We
                    # poll briefly for it so it's never missed even
                    # when the state has already transitioned.
                    if name == "attack_button2":
                        self._await_post_attack_confirm()
            time.sleep(ACTION_CHAIN_SLEEP)

    def _await_post_attack_confirm(self) -> None:
        """Poll for an optional ``confirm_button`` for a few seconds.
        Taps it as soon as it appears; returns silently if it doesn't.
        """
        deadline = time.time() + POST_ATTACK_CONFIRM_WAIT
        while time.time() < deadline:
            if not self._running or self._paused:
                return
            time.sleep(POST_ATTACK_CONFIRM_POLL)
            ss = screencap()
            if ss is None:
                continue
            cm = self._screen_reader.find_template_by_name(ss, "confirm_button")
            if cm:
                log.info("Post-attack confirm_button detected at (%d,%d) — tapping.",
                         cm[0], cm[1])
                tap(cm[0], cm[1])
                return
        log.debug("No post-attack confirm popup within %.1fs — continuing.",
                  POST_ATTACK_CONFIRM_WAIT)

    def _handle_disconnect(self, screenshot) -> None:
        match = self._screen_reader.find_template_by_name(screenshot, "reload_button")
        if match:
            tap(match[0], match[1])
        else:
            log.warning("reload_button not found during disconnect handling.")
