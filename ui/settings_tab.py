"""
Settings Tab — Performance profiles, vision toggles, ADB tuning.

Reads/writes from ``core.settings.Settings`` singleton.
All changes are auto-saved and immediately effective on the running engine.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QComboBox, QCheckBox, QDoubleSpinBox, QSpinBox,
    QSlider, QPushButton, QScrollArea, QFrame, QLineEdit,
)
from PyQt5.QtCore import Qt, pyqtSignal

from core.settings import Settings, PRESETS
from logic import fast_entry

_PRESET_ORDER = ["ultra", "high", "medium", "low", "smart_default"]


class SettingsTab(QWidget):
    """Full settings panel with live-sync to the engine."""

    settings_changed = pyqtSignal()   # emitted on any change

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = Settings()
        self._init_ui()
        self._load_values()

    # ═══════════════════════════════════════════════════════════════════
    #  UI
    # ═══════════════════════════════════════════════════════════════════

    def _init_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        root = QVBoxLayout(container)
        root.setSpacing(16)

        # ── Header ──────────────────────────────────────────────────────
        hdr = QLabel("⚙  Cài đặt")
        hdr.setObjectName("header_label")
        root.addWidget(hdr)

        # ── Performance Profile ─────────────────────────────────────────
        perf_grp = QGroupBox("Cấu hình hiệu năng")
        perf_lay = QVBoxLayout(perf_grp)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Mức:"))
        self._combo_preset = QComboBox()
        for key in _PRESET_ORDER:
            self._combo_preset.addItem(PRESETS[key]["label"], key)
        self._combo_preset.currentIndexChanged.connect(self._on_preset_changed)
        row1.addWidget(self._combo_preset, 1)
        perf_lay.addLayout(row1)

        self._preset_desc = QLabel("")
        self._preset_desc.setObjectName("status_label")
        self._preset_desc.setWordWrap(True)
        perf_lay.addWidget(self._preset_desc)

        root.addWidget(perf_grp)

        # ── ADB Tap Speed ───────────────────────────────────────────────
        adb_grp = QGroupBox("Tốc độ bấm ADB")
        adb_lay = QVBoxLayout(adb_grp)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Nghỉ tối thiểu (giây):"))
        self._spin_tap_min = QDoubleSpinBox()
        self._spin_tap_min.setRange(0.005, 0.500)
        self._spin_tap_min.setSingleStep(0.005)
        self._spin_tap_min.setDecimals(3)
        self._spin_tap_min.valueChanged.connect(self._on_value_changed)
        r2.addWidget(self._spin_tap_min)

        r2.addWidget(QLabel("Nghỉ tối đa (giây):"))
        self._spin_tap_max = QDoubleSpinBox()
        self._spin_tap_max.setRange(0.010, 1.000)
        self._spin_tap_max.setSingleStep(0.005)
        self._spin_tap_max.setDecimals(3)
        self._spin_tap_max.valueChanged.connect(self._on_value_changed)
        r2.addWidget(self._spin_tap_max)
        adb_lay.addLayout(r2)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Thời gian vuốt (ms):"))
        self._spin_swipe = QSpinBox()
        self._spin_swipe.setRange(500, 5000)
        self._spin_swipe.setSingleStep(100)
        self._spin_swipe.valueChanged.connect(self._on_value_changed)
        r3.addWidget(self._spin_swipe)

        r3.addWidget(QLabel("Chu kỳ vòng lặp (giây):"))
        self._spin_tick = QDoubleSpinBox()
        self._spin_tick.setRange(0.2, 5.0)
        self._spin_tick.setSingleStep(0.1)
        self._spin_tick.setDecimals(1)
        self._spin_tick.valueChanged.connect(self._on_value_changed)
        r3.addWidget(self._spin_tick)
        adb_lay.addLayout(r3)

        root.addWidget(adb_grp)

        # ── Vision Toggles ──────────────────────────────────────────────
        vis_grp = QGroupBox("Nhận diện hình ảnh")
        vis_lay = QVBoxLayout(vis_grp)

        self._chk_skip_loot = QCheckBox("Bỏ qua đọc tài nguyên (nhanh hơn)")
        self._chk_skip_loot.setToolTip(
            "Bật thì bot không đọc Vàng/Elixir/Elixir đen nữa,\n"
            "gặp base nào cũng đánh, không xét tài nguyên.",
        )
        self._chk_skip_loot.stateChanged.connect(self._on_value_changed)
        vis_lay.addWidget(self._chk_skip_loot)

        self._chk_skip_timer = QCheckBox("Bỏ qua đọc đồng hồ trận")
        self._chk_skip_timer.setToolTip(
            "Bật thì bot không đọc đồng hồ đếm ngược trong trận.\n"
            "Tính năng rút quân theo thời gian sẽ không hoạt động.",
        )
        self._chk_skip_timer.stateChanged.connect(self._on_value_changed)
        vis_lay.addWidget(self._chk_skip_timer)

        self._chk_fast_entry = QCheckBox(
            f"Vào trận nhanh — bấm mù Attack / Find a Match / Attack! "
            f"(chỉ cho {fast_entry.calibrated_label()})",
        )
        self._chk_fast_entry.setToolTip(
            "Bấm thẳng vào toạ độ ba nút vào trận thay vì chụp màn hình\n"
            "nhận diện từng nút, tiết kiệm khoảng 13 giây mỗi trận\n"
            "(mỗi nút tốn ~1.0s chụp + ~1.8s nhận diện).\n\n"
            "Lúc chạy không kiểm tra gì cả, nên nếu có quảng cáo hay\n"
            "thông báo lạ chen vào thì nó nuốt mất một cú bấm và hỏng\n"
            "lượt đó — vòng lặp sau bot đọc lại màn hình và tự gỡ.\n\n"
            f"Toạ độ đo trên màn {fast_entry.calibrated_label()};\n"
            "độ phân giải khác thì tính năng này tự tắt.",
        )
        self._chk_fast_entry.stateChanged.connect(self._on_value_changed)
        vis_lay.addWidget(self._chk_fast_entry)

        self._chk_multi_touch = QCheckBox(
            "Thả nhiều ngón — bấm tất cả các điểm cùng lúc (cần root)",
        )
        self._chk_multi_touch.setToolTip(
            "Ring Sweep bình thường giữ từng cạnh base một, nên thẻ quân\n"
            "dồn hết vào mấy cạnh đầu và nếu ít quân thì cạnh cuối không\n"
            "còn con nào. Bật cái này thì cả bốn điểm được bấm cùng lúc,\n"
            "quân chia đều cho các cạnh.\n\n"
            "Nó ghi thẳng sự kiện chạm vào thiết bị cảm ứng, mà SELinux\n"
            "chỉ cho phép khi có root — ADB thường không đặt được hai ngón\n"
            "xuống cùng lúc. Không có root thì tự tắt, quay về kiểu giữ\n"
            "lần lượt từng cạnh.\n\n"
            "Thiết bị và chiều toạ độ nằm trong\n"
            "config/v2_attack_rules.json, mục \"multi_touch\".",
        )
        self._chk_multi_touch.stateChanged.connect(self._on_value_changed)
        vis_lay.addWidget(self._chk_multi_touch)

        # Thresholds
        thr_row = QHBoxLayout()
        thr_row.addWidget(QLabel("Độ khớp quân:"))
        self._spin_troop_thr = QDoubleSpinBox()
        self._spin_troop_thr.setRange(0.10, 0.95)
        self._spin_troop_thr.setSingleStep(0.05)
        self._spin_troop_thr.setDecimals(2)
        self._spin_troop_thr.setToolTip("Độ khớp khi nhận thẻ quân/phép (thấp = dễ dãi hơn)")
        self._spin_troop_thr.valueChanged.connect(self._on_value_changed)
        thr_row.addWidget(self._spin_troop_thr)

        thr_row.addWidget(QLabel("Độ khớp nút:"))
        self._spin_ui_thr = QDoubleSpinBox()
        # Floor at 0.70. Lowering this does NOT make detection more willing —
        # it makes every template match everything. Measured over the seven
        # game screens: 0.70-0.99 identifies all seven, 0.55 gets three (the
        # loot label scores 0.57 on the home screen and hijacks it to
        # IN_BATTLE), and 0.40 reports DISCONNECTED on every screen because
        # the connection-error template matches anywhere.
        self._spin_ui_thr.setRange(0.70, 0.99)
        self._spin_ui_thr.setSingleStep(0.05)
        self._spin_ui_thr.setDecimals(2)
        self._spin_ui_thr.setToolTip(
            "Độ khớp khi nhận nút trên giao diện game.\n\n"
            "Nút thật đạt khoảng 0.91-1.00; màn hình không liên quan cao\n"
            "nhất cũng chỉ tầm 0.70. Hạ xuống dưới 0.70 là ảnh mẫu khớp\n"
            "nhầm màn hình — ở 0.55 màn làng chính bị đọc thành ĐANG ĐÁNH,\n"
            "ở 0.40 mọi màn đều thành MẤT KẾT NỐI.\n\n"
            "Nếu bot bỏ sót nút thì vào tab Ảnh mẫu chụp lại nút đó,\n"
            "đừng hạ giá trị này.",
        )
        self._spin_ui_thr.valueChanged.connect(self._on_value_changed)
        thr_row.addWidget(self._spin_ui_thr)

        thr_row.addWidget(QLabel("Độ khớp công trình:"))
        self._spin_building_thr = QDoubleSpinBox()
        self._spin_building_thr.setRange(0.20, 0.95)
        self._spin_building_thr.setSingleStep(0.05)
        self._spin_building_thr.setDecimals(2)
        self._spin_building_thr.setToolTip("Độ khớp khi nhận công trình / thẻ làng thợ")
        self._spin_building_thr.valueChanged.connect(self._on_value_changed)
        thr_row.addWidget(self._spin_building_thr)
        vis_lay.addLayout(thr_row)

        ocr_row = QHBoxLayout()
        ocr_row.addWidget(QLabel("Giãn cách đọc chữ (giây):"))
        self._spin_ocr_interval = QDoubleSpinBox()
        self._spin_ocr_interval.setRange(0.5, 10.0)
        self._spin_ocr_interval.setSingleStep(0.5)
        self._spin_ocr_interval.setDecimals(1)
        self._spin_ocr_interval.setToolTip(
            "Khoảng cách tối thiểu giữa hai lần chạy OCR.\n"
            "Để cao thì nhẹ máy hơn, nhưng số tài nguyên và đồng hồ\n"
            "sẽ cũ đi một chút.",
        )
        self._spin_ocr_interval.valueChanged.connect(self._on_value_changed)
        ocr_row.addWidget(self._spin_ocr_interval)
        ocr_row.addStretch()
        vis_lay.addLayout(ocr_row)

        root.addWidget(vis_grp)

        # ── Deployment Tuning ───────────────────────────────────────────
        dep_grp = QGroupBox("Tinh chỉnh thả quân")
        dep_lay = QVBoxLayout(dep_grp)

        r4 = QHBoxLayout()
        r4.addWidget(QLabel("Chờ trước khi kích kỹ năng hero (giây):"))
        self._spin_hero_delay = QDoubleSpinBox()
        self._spin_hero_delay.setRange(1.0, 30.0)
        self._spin_hero_delay.setSingleStep(0.5)
        self._spin_hero_delay.setDecimals(1)
        self._spin_hero_delay.valueChanged.connect(self._on_value_changed)
        r4.addWidget(self._spin_hero_delay)

        r4.addWidget(QLabel("Lệch ngẫu nhiên khi thả (px):"))
        self._spin_jitter = QSpinBox()
        self._spin_jitter.setRange(0, 50)
        self._spin_jitter.setSingleStep(5)
        self._spin_jitter.valueChanged.connect(self._on_value_changed)
        r4.addWidget(self._spin_jitter)
        dep_lay.addLayout(r4)

        root.addWidget(dep_grp)

        # ── Game Presence ───────────────────────────────────────────────
        game_grp = QGroupBox("Theo dõi game (kiểm tra game có đang mở)")
        game_lay = QVBoxLayout(game_grp)

        gp_row = QHBoxLayout()
        gp_row.addWidget(QLabel("Tên gói game:"))
        self._edit_game_pkg = QLineEdit()
        self._edit_game_pkg.setPlaceholderText("com.supercell.clashofclans")
        self._edit_game_pkg.setToolTip(
            "Tên gói Android của game.\n"
            "Bot dò bằng:  adb shell dumpsys window | findstr mCurrentFocus\n"
            "Mặc định: com.supercell.clashofclans",
        )
        self._edit_game_pkg.editingFinished.connect(self._on_value_changed)
        gp_row.addWidget(self._edit_game_pkg, 1)
        game_lay.addLayout(gp_row)

        gi_row = QHBoxLayout()
        gi_row.addWidget(QLabel("Kiểm tra mỗi (giây):"))
        self._spin_game_interval = QSpinBox()
        self._spin_game_interval.setRange(0, 600)
        self._spin_game_interval.setSingleStep(5)
        self._spin_game_interval.setToolTip(
            "Bao lâu bot kiểm tra một lần xem game có đang ở trên cùng\n"
            "không. Để 0 là tắt kiểm tra định kỳ.\n"
            "Nên để 30–60 giây.",
        )
        self._spin_game_interval.valueChanged.connect(self._on_value_changed)
        gi_row.addWidget(self._spin_game_interval)

        self._chk_auto_launch = QCheckBox("Tự mở lại game khi game bị đẩy xuống dưới")
        self._chk_auto_launch.setToolTip(
            "Nếu game không ở trên cùng, bot sẽ chạy:\n"
            "  adb shell monkey -p <tên gói> -c LAUNCHER 1\n"
            "để mở lại game.",
        )
        self._chk_auto_launch.stateChanged.connect(self._on_value_changed)
        gi_row.addWidget(self._chk_auto_launch)
        gi_row.addStretch()
        game_lay.addLayout(gi_row)

        root.addWidget(game_grp)

        # ── Console Settings ────────────────────────────────────────────
        con_grp = QGroupBox("Bảng log")
        con_lay = QVBoxLayout(con_grp)

        r5 = QHBoxLayout()
        r5.addWidget(QLabel("Số dòng tối đa:"))
        self._spin_max_lines = QSpinBox()
        self._spin_max_lines.setRange(500, 50000)
        self._spin_max_lines.setSingleStep(500)
        self._spin_max_lines.valueChanged.connect(self._on_value_changed)
        r5.addWidget(self._spin_max_lines)

        r5.addWidget(QLabel("Cỡ chữ:"))
        self._spin_font = QSpinBox()
        self._spin_font.setRange(8, 24)
        self._spin_font.setSingleStep(1)
        self._spin_font.valueChanged.connect(self._on_value_changed)
        r5.addWidget(self._spin_font)
        con_lay.addLayout(r5)

        self._chk_debug = QCheckBox("Hiện log chi tiết (DEBUG)")
        self._chk_debug.stateChanged.connect(self._on_value_changed)
        con_lay.addWidget(self._chk_debug)

        root.addWidget(con_grp)

        # ── Actions ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._btn_reset = QPushButton("🔄  Khôi phục mặc định")
        self._btn_reset.clicked.connect(self._on_reset)
        btn_row.addWidget(self._btn_reset)
        btn_row.addStretch()

        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("status_label")
        btn_row.addWidget(self._status_lbl)
        root.addLayout(btn_row)

        root.addStretch()

        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ═══════════════════════════════════════════════════════════════════
    #  LOAD / SAVE
    # ═══════════════════════════════════════════════════════════════════

    def _load_values(self) -> None:
        s = self._settings
        # Block signals during load
        for w in self._all_widgets():
            w.blockSignals(True)

        # Preset
        preset = s.get("preset", "medium")
        idx = _PRESET_ORDER.index(preset) if preset in _PRESET_ORDER else 2
        self._combo_preset.setCurrentIndex(idx)
        self._update_preset_desc(preset)

        # ADB
        self._spin_tap_min.setValue(s.get("tap_delay_min"))
        self._spin_tap_max.setValue(s.get("tap_delay_max"))
        self._spin_swipe.setValue(s.get("swipe_duration"))
        self._spin_tick.setValue(s.get("tick_interval"))

        # Vision
        self._chk_skip_loot.setChecked(s.get("skip_loot_ocr"))
        self._chk_skip_timer.setChecked(s.get("skip_timer_ocr"))
        self._chk_fast_entry.setChecked(bool(s.get("hv_fast_entry", False)))
        self._chk_multi_touch.setChecked(bool(s.get("multi_touch_enabled", False)))
        self._spin_troop_thr.setValue(s.get("vision_troop_threshold"))
        self._spin_ui_thr.setValue(s.get("vision_ui_threshold"))
        self._spin_building_thr.setValue(s.get("vision_building_threshold"))
        self._spin_ocr_interval.setValue(s.get("ocr_min_interval"))

        # Deployment
        self._spin_hero_delay.setValue(s.get("hero_ability_delay"))
        self._spin_jitter.setValue(s.get("deploy_jitter"))

        # Game presence
        self._edit_game_pkg.setText(str(s.get("game_package", "com.supercell.clashofclans")))
        self._spin_game_interval.setValue(int(s.get("game_check_interval", 60)))
        self._chk_auto_launch.setChecked(bool(s.get("auto_launch_game", True)))

        # Console
        self._spin_max_lines.setValue(s.get("console_max_lines"))
        self._spin_font.setValue(s.get("console_font_size"))
        self._chk_debug.setChecked(s.get("console_show_debug"))

        for w in self._all_widgets():
            w.blockSignals(False)

    def _save_values(self) -> None:
        s = self._settings
        s.set("tap_delay_min", self._spin_tap_min.value())
        s.set("tap_delay_max", self._spin_tap_max.value())
        s.set("swipe_duration", self._spin_swipe.value())
        s.set("tick_interval", self._spin_tick.value())
        s.set("skip_loot_ocr", self._chk_skip_loot.isChecked())
        s.set("skip_timer_ocr", self._chk_skip_timer.isChecked())
        s.set("hv_fast_entry", self._chk_fast_entry.isChecked())
        s.set("multi_touch_enabled", self._chk_multi_touch.isChecked())
        s.set("vision_troop_threshold", self._spin_troop_thr.value())
        s.set("vision_ui_threshold", self._spin_ui_thr.value())
        s.set("vision_building_threshold", self._spin_building_thr.value())
        s.set("ocr_min_interval", self._spin_ocr_interval.value())
        s.set("hero_ability_delay", self._spin_hero_delay.value())
        s.set("deploy_jitter", self._spin_jitter.value())
        s.set("game_package", self._edit_game_pkg.text().strip() or "com.supercell.clashofclans")
        s.set("game_check_interval", self._spin_game_interval.value())
        s.set("auto_launch_game", self._chk_auto_launch.isChecked())
        s.set("console_max_lines", self._spin_max_lines.value())
        s.set("console_font_size", self._spin_font.value())
        s.set("console_show_debug", self._chk_debug.isChecked())
        s.save()
        self.settings_changed.emit()
        self._status_lbl.setText("✓ Đã lưu")

    # ═══════════════════════════════════════════════════════════════════
    #  SLOTS
    # ═══════════════════════════════════════════════════════════════════

    def _on_preset_changed(self, idx: int) -> None:
        key = self._combo_preset.currentData()
        if key:
            self._settings.apply_preset(key)
            self._load_values()
            self._update_preset_desc(key)
            self.settings_changed.emit()
            self._status_lbl.setText(f"✓ Preset: {key.upper()}")

    def _on_value_changed(self, *_) -> None:
        self._save_values()

    def _on_reset(self) -> None:
        self._settings.reset()
        self._load_values()
        self.settings_changed.emit()
        self._status_lbl.setText("✓ Đã khôi phục mặc định")

    def _update_preset_desc(self, key: str) -> None:
        p = PRESETS.get(key, {})
        self._preset_desc.setText(p.get("description", ""))

    def _all_widgets(self):
        return [
            self._combo_preset,
            self._spin_tap_min, self._spin_tap_max,
            self._spin_swipe, self._spin_tick,
            self._chk_skip_loot, self._chk_skip_timer,
            self._spin_troop_thr, self._spin_ui_thr,
            self._spin_building_thr, self._spin_ocr_interval,
            self._spin_hero_delay, self._spin_jitter,
            self._edit_game_pkg, self._spin_game_interval, self._chk_auto_launch,
            self._spin_max_lines, self._spin_font, self._chk_debug,
        ]
