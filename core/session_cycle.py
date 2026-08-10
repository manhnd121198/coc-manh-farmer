"""Chu kỳ chơi — nghỉ: chơi một lúc rồi tắt game, lát sau mở lại.

Chạy liên tục nhiều giờ không nghỉ là dấu vết rõ nhất của một tài khoản
tự động. Cái này cắt phiên chơi thành từng đoạn có độ dài ngẫu nhiên,
giữa các đoạn thì ĐÓNG HẲN game (``am force-stop``, không phải bấm Home)
rồi mở lại và chạy tiếp.

Mọi thứ ở đây chỉ là tính giờ và tung số — không đụng ADB, không đụng
Qt. Việc đóng/mở game là của ``BotEngine``. Tách ra như vậy để phần
quyết định "khi nào nghỉ, nghỉ bao lâu" test được mà không cần máy ảo.

Đồng hồ và bộ random đều tiêm được từ ngoài vào, nên test tua thời gian
được thay vì phải ngồi chờ một tiếng.
"""

from __future__ import annotations

import random as _random
import time

from core.logger import BotLogger
from core.settings import Settings

log = BotLogger.get("session_cycle")

# Khoảng an toàn cho mỗi con số, tính bằng phút. Chặn trên chủ yếu để
# một lần gõ nhầm không biến thành phiên nghỉ mấy ngày.
MIN_MINUTES = 0.1
MAX_MINUTES = 24 * 60.0


def _band(settings, lo_key: str, hi_key: str, lo_default: float, hi_default: float):
    """Đọc một cặp min/max từ Settings, đã kẹp và đã xếp đúng thứ tự."""
    try:
        lo = float(settings.get(lo_key, lo_default))
    except (TypeError, ValueError):
        lo = lo_default
    try:
        hi = float(settings.get(hi_key, hi_default))
    except (TypeError, ValueError):
        hi = hi_default
    lo = max(MIN_MINUTES, min(lo, MAX_MINUTES))
    hi = max(MIN_MINUTES, min(hi, MAX_MINUTES))
    return min(lo, hi), max(lo, hi)


class SessionCycle:
    """Nói cho engine biết khi nào tắt game và nghỉ bao lâu."""

    def __init__(self, settings=None, clock=time.time, rng=_random) -> None:
        self._s = settings if settings is not None else Settings()
        self._clock = clock
        self._rng = rng
        # Mốc phải nghỉ. None = không có chu kỳ nào đang chạy.
        self._play_until: float | None = None
        # Lúc đến hạn nghỉ nhưng chưa nghỉ được (còn đang đánh dở).
        self._due_since: float | None = None
        # Độ dài nghỉ của lần chạy thử; None = lấy theo Settings.
        self._forced_break: float | None = None
        self._is_test = False

    # ── Cấu hình ────────────────────────────────────────────────────
    def enabled(self) -> bool:
        return bool(self._s.get("session_cycle_enabled", False))

    def play_band_sec(self) -> tuple[float, float]:
        lo, hi = _band(self._s, "session_play_min_min", "session_play_max_min", 60.0, 75.0)
        return lo * 60.0, hi * 60.0

    def break_band_sec(self) -> tuple[float, float]:
        lo, hi = _band(self._s, "session_break_min_min", "session_break_max_min", 5.0, 10.0)
        return lo * 60.0, hi * 60.0

    # ── Vòng đời ────────────────────────────────────────────────────
    def start(self) -> float | None:
        """Bắt đầu một đoạn chơi mới. Trả về độ dài đoạn đó (giây).

        Tắt tính năng thì trả None và ``due()`` không bao giờ True nữa —
        chu kỳ phải im hoàn toàn khi không bật.
        """
        self._due_since = None
        self._forced_break = None
        self._is_test = False
        if not self.enabled():
            self._play_until = None
            return None
        lo, hi = self.play_band_sec()
        span = self._rng.uniform(lo, hi)
        self._play_until = self._clock() + span
        log.info("Phiên chơi này dài %.1f phút rồi sẽ tắt game.", span / 60.0)
        return span

    def arm_test(self, play_sec: float, break_sec: float) -> None:
        """Hẹn MỘT chu kỳ ngắn để kiểm tra, không cần bật tính năng.

        Chỉ chạy đúng một lần: nghỉ xong, nếu tính năng đang tắt thì mọi
        thứ trở về im lặng, còn nếu đang bật thì quay lại dải bình
        thường. Nút test là để xem đóng/mở game có chạy không, chứ không
        phải để lén bật tính năng.
        """
        self._play_until = self._clock() + max(1.0, float(play_sec))
        self._due_since = None
        self._forced_break = max(1.0, float(break_sec))
        self._is_test = True
        log.info(
            "CHẠY THỬ chu kỳ: %.0f giây nữa tắt game, %.0f giây sau mở lại.",
            play_sec, break_sec,
        )

    def cancel(self) -> None:
        self._play_until = None
        self._due_since = None
        self._forced_break = None
        self._is_test = False

    # ── Truy vấn ────────────────────────────────────────────────────
    @property
    def is_test(self) -> bool:
        return self._is_test

    def due(self) -> bool:
        """Đã đến lúc tắt game chưa."""
        if self._play_until is None:
            return False
        if self._clock() < self._play_until:
            return False
        if self._due_since is None:
            self._due_since = self._clock()
        return True

    def overdue_sec(self) -> float:
        """Đến hạn bao lâu rồi mà vẫn chưa nghỉ được (đang đánh dở)."""
        if self._due_since is None:
            return 0.0
        return max(0.0, self._clock() - self._due_since)

    def remaining_sec(self) -> float:
        if self._play_until is None:
            return 0.0
        return max(0.0, self._play_until - self._clock())

    def take_break(self) -> float:
        """Chốt độ dài lần nghỉ này (giây) và đóng đoạn chơi hiện tại.

        Gọi đúng một lần cho mỗi lần nghỉ, ngay trước khi tắt game.
        """
        if self._forced_break is not None:
            span = self._forced_break
        else:
            lo, hi = self.break_band_sec()
            span = self._rng.uniform(lo, hi)
        self._play_until = None
        self._due_since = None
        return span
