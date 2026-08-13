"""
HeroPlannerSkill — decides where heroes drop and when their abilities
fire.

Drop policy:
    • Heroes drop AFTER the first wave (tanks) has engaged.
    • Drop point is just behind the main army cluster (toward the deploy
      corridor center, not the base center) so they tank for the wave.
    • Each hero drops with ±15 px jitter to avoid stack-of-one.

Kỹ năng hero:
    • Nút kích kỹ năng CHÍNH LÀ ô thẻ đã chọn hero lúc thả. Chờ hết
      khoảng đã cấu hình thì DOUBLE-TAP đúng ô đó.
    • Khoảng chờ là dải ``[min, max]``, roll lại mỗi trận, nên không
      trận nào kích đúng cùng một nhịp. Ghi một số trần vẫn chạy, hiểu
      là chờ cố định.
    • Có thể TẮT hẳn việc bấm. Khi đó game tự phát kỹ năng lúc hero sắp
      hết máu — muộn hơn bấm tay, nhưng là nhịp của chính con hero chứ
      không phải một con số cố định, và không phí kỹ năng lên con hero
      còn đầy máu.
"""

from __future__ import annotations

import random
from typing import List, Tuple

from core.logger import BotLogger

log = BotLogger.get("v2.hero_planner")

HeroEntry = Tuple[str, Tuple[int, int], Tuple[int, int]]


class HeroPlannerSkill:
    name = "hero_planner"

    def plan_drops(
        self,
        cluster_xy: tuple[int, int],
        hero_card_locations: List[Tuple[str, int, int]],
    ) -> List[HeroEntry]:
        cx, cy = cluster_xy
        out: List[HeroEntry] = []
        for name, hx, hy in hero_card_locations:
            drop_x = cx + random.randint(-18, 18)
            drop_y = cy + random.randint(-18, 18)
            out.append((name, (hx, hy), (drop_x, drop_y)))
        return out

    # MỘT nút duy nhất, không tách delay với công tắc: chính ô delay nói
    # luôn có bấm kỹ năng hay không. Ghi một dải thì bot double-tap vào
    # lúc nào đó trong dải; ghi "auto" (hoặc null) thì bot không đụng vào
    # thẻ, để game tự phát kỹ năng khi hero sắp hết máu. Tách riêng cờ
    # bật/tắt thì hai thứ mâu thuẫn được — tắt mà vẫn ghi 4 giây, đọc vào
    # tưởng còn làm gì đó — mà cũng chẳng diễn tả thêm được gì.
    ABILITY_AUTO_WORDS = frozenset({
        "auto", "off", "no", "none", "never", "self", "khong", "không",
    })
    DEFAULT_ABILITY_BAND = (3.0, 5.0)

    @staticmethod
    def _ability_block(config: dict | None) -> dict:
        block = (config or {}).get("hero_ability")
        return block if isinstance(block, dict) else {}

    @classmethod
    def ability_enabled(cls, config: dict | None) -> bool:
        """False = không bao giờ double-tap; game tự phát khi hero yếu máu."""
        raw = cls._ability_block(config).get(
            "trigger_after_engagement_sec", cls.DEFAULT_ABILITY_BAND,
        )
        if raw is None:
            return False
        if isinstance(raw, str):
            return raw.strip().casefold() not in cls.ABILITY_AUTO_WORDS
        return True

    @classmethod
    def ability_delay_seconds(
        cls, config: dict | None, fallback: float | None = None,
    ) -> float:
        """Số giây chờ sau khi quân đã xuống, roll lại mỗi trận.

        Để "auto" thì vẫn trả về một khoảng chờ — chờ ở đây là để quân
        kịp giao chiến trước khi thả spell, nên không được bỏ; chỉ là
        lúc đó config không còn con số nào nên rơi về dải mặc định.
        ``fallback`` là giá trị dự phòng của bên gọi (ô nhập trong tab
        Cài đặt, dùng cho các đường cũ) và chỉ có tác dụng khi config
        không có dải riêng.
        """
        raw = cls._ability_block(config).get("trigger_after_engagement_sec")
        if raw is None or isinstance(raw, str):
            raw = fallback if fallback is not None else cls.DEFAULT_ABILITY_BAND
        if isinstance(raw, (list, tuple)):
            try:
                lo, hi = float(raw[0]), float(raw[-1])
            except (TypeError, ValueError, IndexError):
                lo, hi = cls.DEFAULT_ABILITY_BAND
        else:
            try:
                lo = hi = float(raw)
            except (TypeError, ValueError):
                lo, hi = cls.DEFAULT_ABILITY_BAND
        lo, hi = max(0.0, min(lo, hi)), max(0.0, max(lo, hi))
        return random.uniform(lo, hi)

    @classmethod
    def ability_double_tap_gap_ms(cls, config: dict | None) -> int:
        try:
            return int(cls._ability_block(config).get("double_tap_gap_ms", 120))
        except (TypeError, ValueError):
            return 120
