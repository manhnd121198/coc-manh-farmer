import json
import os
import sys
import threading

_SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "profiles", "settings.json",
)

# ── Performance Presets (5 Profiles) ───────────────────────────────────
PRESETS: dict[str, dict] = {
    "ultra": {
        "label": "⚡ Tối đa (card rời, màn 4K/2K)",
        "tick_interval": 0.5,
        "tap_delay_min": 0.01,
        "tap_delay_max": 0.03,
        "swipe_duration": 1800,
        "template_scales": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3],
        "ocr_workers": 4,
        "ocr_min_interval": 1.0,
        "vision_troop_threshold": 0.38,
        "vision_ui_threshold": 0.80,
        "vision_building_threshold": 0.42,
        "vision_bb_card_threshold": 0.42,
        "description": "Nhanh và chính xác nhất. Cho máy mạnh, màn 2K/4K, có card rời.",
    },
    "high": {
        "label": "🔥 Cao (card tốt, máy mạnh)",
        "tick_interval": 0.8,
        "tap_delay_min": 0.02,
        "tap_delay_max": 0.05,
        "swipe_duration": 2200,
        "template_scales": [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2],
        "ocr_workers": 2,
        "ocr_min_interval": 1.5,
        "vision_troop_threshold": 0.35,
        "vision_ui_threshold": 0.80,
        "vision_building_threshold": 0.38,
        "vision_bb_card_threshold": 0.38,
        "description": "Nhanh và ổn định. Cho máy mạnh hoặc tầm trung khá.",
    },
    "medium": {
        "label": "💻 Trung bình (CPU, cân bằng)",
        "tick_interval": 1.0,
        "tap_delay_min": 0.03,
        "tap_delay_max": 0.08,
        "swipe_duration": 2500,
        "template_scales": [0.7, 0.8, 0.9, 1.0, 1.1],
        "ocr_workers": 2,
        "ocr_min_interval": 2.0,
        "vision_troop_threshold": 0.32,
        "vision_ui_threshold": 0.80,
        "vision_building_threshold": 0.35,
        "vision_bb_card_threshold": 0.35,
        "description": "Cân bằng. Hợp với đa số máy chạy CPU, không card rời.",
    },
    "low": {
        "label": "🐢 Thấp (CPU yếu, máy cấu hình thấp)",
        "tick_interval": 1.5,
        "tap_delay_min": 0.05,
        "tap_delay_max": 0.12,
        "swipe_duration": 3000,
        "template_scales": [0.8, 0.9, 1.0, 1.1],
        "ocr_workers": 1,
        "ocr_min_interval": 3.0,
        "vision_troop_threshold": 0.28,
        "vision_ui_threshold": 0.80,
        "vision_building_threshold": 0.30,
        "vision_bb_card_threshold": 0.30,
        "description": "Nhẹ và an toàn. Cho laptop, máy yếu, giả lập cấu hình thấp.",
    },
    "smart_default": {
        "label": "🤖 Tự thích ứng theo máy",
        "tick_interval": 0.9,
        "tap_delay_min": 0.025,
        "tap_delay_max": 0.065,
        "swipe_duration": 2400,
        "template_scales": [0.7, 0.85, 1.0, 1.15],
        "ocr_workers": 2,
        "ocr_min_interval": 1.8,
        "vision_troop_threshold": 0.30,
        "vision_ui_threshold": 0.80,
        "vision_building_threshold": 0.34,
        "vision_bb_card_threshold": 0.34,
        "description": "Tự thích ứng theo kích thước màn hình và cấu hình máy (dùng chung với nút Khôi phục mặc định).",
    },
}

# ── Default Settings ────────────────────────────────────────────────────
_DEFAULTS: dict = {
    # Performance
    "preset": "smart_default",
    "tick_interval": 0.9,
    "tap_delay_min": 0.025,
    "tap_delay_max": 0.065,
    "swipe_duration": 2400,
    "template_scales": [0.7, 0.85, 1.0, 1.15],
    "ocr_workers": 2,

    # Blind-tap the Attack -> Find a Match -> Attack! chain instead of
    # detecting each button. Off by default: it skips all verification.
    "hv_fast_entry": False,

    # Press several spots at once instead of one after another. Needs
    # root, so it is off by default and falls back on its own.
    "multi_touch_enabled": False,

    # After a V2 rule finishes, read the troop bar again and empty any card
    # that still has troops on it. Off by default: it judges cards by
    # colour, and those thresholds need one look at the log per device.
    "sweep_up_enabled": False,

    # Vision toggles
    "skip_loot_ocr": False,
    "skip_timer_ocr": False,
    "vision_troop_threshold": 0.30,
    "vision_ui_threshold": 0.80,
    "vision_building_threshold": 0.34,
    "vision_bb_card_threshold": 0.34,
    "vision_building_threshold": 0.40,
    "vision_bb_card_threshold": 0.42,

    # OCR
    "ocr_min_interval": 1.8,

    # Deployment
    "hero_ability_delay": 3.0,
    "taps_per_swipe": 25,
    "deploy_jitter": 15,

    # Console
    "console_max_lines": 5000,
    "console_font_size": 12,
    "console_show_debug": True,

    # Smart Vision V2  (Red-Zone-Aware planner — opt-in per village)
    "v2_enabled_hv":      False,
    "v2_enabled_bb":      False,
    "v2_mode_hv":         "smart",        # smart | building | storage
    "v2_mode_bb":         "smart",
    "v2_target_hv":       "",
    "v2_target_bb":       "",
    "v2_decoration_wait": 5.0,
    "v2_rule_hv":         "auto",
    "v2_rule_bb":         "auto",

    # When V2 cannot read the base, walk away and look for another one
    # instead of attacking with the legacy planner. Capped in
    # config/v2_attack_rules.json -> fallback.max_consecutive_skips.
    "v2_skip_on_fallback": False,

    # Game Presence
    "game_package": "com.supercell.clashofclans",
    "game_check_interval": 60,
    "auto_launch_game": True,
}


class Settings:
    """Thread-safe global settings singleton."""

    _instance: "Settings | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = dict(_DEFAULTS)
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        if os.path.isfile(_SETTINGS_FILE):
            try:
                with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                for k, v in saved.items():
                    if k in _DEFAULTS:
                        self._data[k] = v
            except Exception:
                pass

    def save(self) -> None:
        os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default=None):
        return self._data.get(key, default if default is not None else _DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def detect_smart_profile(self) -> dict:
        """Dynamically detect host CPU/RAM capabilities and active screen metrics."""
        cpu_count = os.cpu_count() or 4
        smart_config = dict(PRESETS["smart_default"])

        try:
            from core.adb_handler import get_active_resolution, is_tablet_device
            w, h = get_active_resolution()
            is_tab = is_tablet_device()

            if is_tab:
                # Tablet aspect ratios (4:3 or 16:10) need wider template search ranges
                smart_config["template_scales"] = [0.65, 0.80, 0.95, 1.05, 1.20]
                smart_config["vision_troop_threshold"] = 0.32
            elif h < 900:
                # Low resolution screen
                smart_config["template_scales"] = [0.60, 0.75, 0.90, 1.0]
                smart_config["vision_troop_threshold"] = 0.33
            else:
                smart_config["template_scales"] = [0.70, 0.85, 1.0, 1.15]
                smart_config["vision_troop_threshold"] = 0.35
        except Exception:
            pass

        if cpu_count <= 2:
            smart_config["tick_interval"] = 1.4
            smart_config["ocr_min_interval"] = 2.5
            smart_config["ocr_workers"] = 1
        elif cpu_count <= 4:
            smart_config["tick_interval"] = 1.0
            smart_config["ocr_min_interval"] = 1.8
            smart_config["ocr_workers"] = 2
        else:
            smart_config["tick_interval"] = 0.7
            smart_config["ocr_min_interval"] = 1.2
            smart_config["ocr_workers"] = 2

        return smart_config

    def apply_preset(self, preset_name: str) -> None:
        target_cfg = self.detect_smart_profile() if preset_name == "smart_default" else PRESETS.get(preset_name)
        if target_cfg is None:
            return
        self._data["preset"] = preset_name
        for k in ("tick_interval", "tap_delay_min", "tap_delay_max",
                  "swipe_duration", "template_scales", "ocr_workers",
                  "ocr_min_interval", "vision_troop_threshold",
                  "vision_ui_threshold", "vision_building_threshold",
                  "vision_bb_card_threshold"):
            if k in target_cfg:
                self._data[k] = target_cfg[k]
        self.save()

    def to_dict(self) -> dict:
        return dict(self._data)

    def reset(self) -> None:
        """Reset to defaults and apply Smart Adaptive Profile."""
        self._data = dict(_DEFAULTS)
        self.apply_preset("smart_default")
        self.save()

