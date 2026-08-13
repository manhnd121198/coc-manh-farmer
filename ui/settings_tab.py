"""
Settings Tab — Performance profiles, vision toggles, ADB tuning.

Reads/writes from ``core.settings.Settings`` singleton.
All changes are auto-saved and immediately effective on the running engine.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QComboBox, QCheckBox, QDoubleSpinBox, QSpinBox,
    QSlider, QPushButton, QScrollArea, QFrame, QLineEdit,
    QApplication, QMessageBox,
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
        self._engine = None
        self._init_ui()
        self._load_values()

    def set_engine(self, engine) -> None:
        """MainWindow gắn engine đang chạy vào để nút Chạy thử dùng được."""
        self._engine = engine
        if engine is not None and hasattr(engine, "session_note"):
            engine.session_note.connect(self._lbl_cycle_status.setText)
            # Cùng một signal chạy cả hai nhãn: engine chỉ có một đường
            # báo tiến độ, và người dùng đang nhìn nhãn nào thì thấy nhãn đó.
            engine.session_note.connect(self._lbl_emu_status.setText)
        else:
            self._lbl_cycle_status.setText("")
            self._lbl_emu_status.setText("")

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

        # Ticking the box is not the same as the feature working: it needs
        # root and a writable touchscreen node, and when either is missing
        # the gesture falls back silently — the attack still runs, just one
        # finger at a time, and the two look identical from outside. The
        # check is a button rather than automatic because it talks to the
        # device, and at start-up there may not be one connected yet.
        mt_row = QHBoxLayout()
        mt_row.addWidget(self._chk_multi_touch)
        self._btn_multi_touch = QPushButton("Kiểm tra")
        self._btn_multi_touch.setToolTip(
            "Hỏi thiết bị xem có chạy được quyền root và tìm thấy màn hình\n"
            "cảm ứng không. Cần thiết bị đang kết nối.",
        )
        self._btn_multi_touch.clicked.connect(self._probe_multi_touch)
        mt_row.addWidget(self._btn_multi_touch)
        mt_row.addStretch()
        vis_lay.addLayout(mt_row)

        self._lbl_multi_touch = QLabel("")
        self._lbl_multi_touch.setWordWrap(True)
        self._lbl_multi_touch.setStyleSheet("color: #9e9e9e; padding-left: 22px;")
        vis_lay.addWidget(self._lbl_multi_touch)

        self._chk_sweep_up = QCheckBox(
            "Thả nốt quân thừa — đánh xong đọc lại thanh quân, thẻ nào còn thì thả tiếp",
        )
        self._chk_sweep_up.setToolTip(
            "Chiến thuật thả theo kế hoạch chứ không theo kết quả: Ring\n"
            "Sweep giữ mỗi cạnh một khoảng thời gian cố định, giữ ngắn hơn\n"
            "số quân là còn thừa trên thẻ; thẻ nào không nhận diện được\n"
            "thì bị bỏ qua luôn. Bật cái này thì đánh xong bot chụp lại\n"
            "màn hình và thả nốt những thẻ vẫn còn quân.\n\n"
            "Cách nhận biết thẻ hết quân: CoC làm thẻ xám và tối đi, giống\n"
            "hệt thẻ hero khi chết — bot đo độ bão hoà màu và độ sáng chứ\n"
            "không đọc con số trên thẻ.\n\n"
            "Ngưỡng màu khác nhau theo máy, nên mỗi lần kiểm tra bot đều\n"
            "in ra số đo được. Xem log một trận rồi chỉnh mục \"sweep_up\"\n"
            "trong config/v2_attack_rules.json cho khớp máy bạn.",
        )
        self._chk_sweep_up.stateChanged.connect(self._on_value_changed)
        vis_lay.addWidget(self._chk_sweep_up)

        self._chk_skip_on_fallback = QCheckBox(
            "V2 đọc không ra base thì bỏ, tìm trận khác (thay vì đánh kiểu cũ)",
        )
        self._chk_skip_on_fallback.setToolTip(
            "Khi không dựng được vùng đỏ của base, bình thường bot vẫn đánh\n"
            "bằng bộ thả cũ: dồn cả đội quân vào một cụm, không theo chiến\n"
            "thuật nào. Bật cái này thì thay vì đánh như vậy, bot bỏ base\n"
            "đó và đi tìm trận khác.\n\n"
            "Giá phải trả khác nhau tuỳ lúc: đang ở màn do thám thì chỉ mất\n"
            "phí tìm trận; đã vào trận rồi thì phải đầu hàng, mất luôn lượt\n"
            "đánh và cúp.\n\n"
            "Bỏ liên tiếp quá 3 base thì bot tự đánh bằng bộ thả cũ —\n"
            "hỏng liên tục nghĩa là sai cấu hình chứ không phải xui, và bỏ\n"
            "mãi thì chỉ tốn tiền tìm trận. Sửa số này ở mục \"fallback\"\n"
            "trong config/v2_attack_rules.json.",
        )
        self._chk_skip_on_fallback.stateChanged.connect(self._on_value_changed)
        vis_lay.addWidget(self._chk_skip_on_fallback)

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
        self._spin_hero_delay.setToolTip(
            "Giá trị dự phòng. Thời gian chờ thật lấy từ\n"
            "config/v2_attack_rules.json → hero_ability →\n"
            "trigger_after_engagement_sec, mặc định random 3-5 giây mỗi\n"
            "trận. Ô này chỉ dùng khi mục đó bị xoá khỏi config.\n"
            "Đặt mục đó thành \"auto\" thì bot không bấm kỹ năng nữa —\n"
            "để game tự phát khi hero sắp hết máu.",
        )
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

        emu_row = QHBoxLayout()
        self._chk_emu_restart = QCheckBox("Giả lập treo thì tự tắt hẳn rồi bật lại")
        self._chk_emu_restart.setToolTip(
            "Khi giả lập treo, ADB vẫn báo 'device' vì nó chỉ hỏi server\n"
            "trên máy tính chứ không hỏi máy ảo — bot không tự biết được.\n"
            "Bật cái này thì sau vài lần chụp màn hình thất bại liên tiếp,\n"
            "bot gọi ldconsole.exe tắt hẳn máy ảo rồi bật lại.\n\n"
            "LƯU Ý: máy ảo bị đóng thật, mọi thứ đang mở trong đó mất hết.",
        )
        self._chk_emu_restart.stateChanged.connect(self._on_value_changed)
        emu_row.addWidget(self._chk_emu_restart)

        emu_row.addWidget(QLabel("Tên máy ảo:"))
        self._edit_emu_name = QLineEdit()
        self._edit_emu_name.setPlaceholderText("để trống = dùng index 0")
        self._edit_emu_name.setToolTip(
            "Tên máy ảo trong LDPlayer. Nên điền: thêm hoặc xoá một máy ảo\n"
            "là index xô hết, và lúc đó bot sẽ khởi động lại nhầm máy.",
        )
        self._edit_emu_name.textChanged.connect(self._on_value_changed)
        emu_row.addWidget(self._edit_emu_name)

        self._lbl_emu_found = QLabel()
        emu_row.addWidget(self._lbl_emu_found)
        emu_row.addStretch()
        game_lay.addLayout(emu_row)

        emu_test_row = QHBoxLayout()
        self._btn_test_emu = QPushButton("▶  Chạy thử: tắt giả lập rồi bật lại ngay")
        self._btn_test_emu.setToolTip(
            "Chạy đúng cái cơ chế gỡ treo, ngay bây giờ, không phải chờ\n"
            "tới lúc giả lập treo thật. Đi qua đúng đường thật:\n"
            "  ldconsole quit → chờ tắt → launch → chờ Android lên\n"
            "  → chờ ADB nối lại → mở game → chờ vào tới làng.\n\n"
            "Bot phải đang chạy thì mới bấm được. Không cần bật ô trên,\n"
            "và chạy thử xong cũng không tự bật.\n\n"
            "Mất khoảng 1–3 phút, và giả lập bị ĐÓNG THẬT.",
        )
        self._btn_test_emu.clicked.connect(self._on_test_emulator_restart)
        emu_test_row.addWidget(self._btn_test_emu)

        self._lbl_emu_status = QLabel("")
        self._lbl_emu_status.setStyleSheet("color: #c0c0c0;")
        emu_test_row.addWidget(self._lbl_emu_status, 1)
        game_lay.addLayout(emu_test_row)

        root.addWidget(game_grp)

        # ── Chu kỳ chơi — nghỉ ──────────────────────────────────────────
        cyc_grp = QGroupBox("Chu kỳ chơi — nghỉ (tắt game rồi mở lại)")
        cyc_lay = QVBoxLayout(cyc_grp)

        self._chk_session_cycle = QCheckBox(
            "Chơi một lúc rồi tắt hẳn game, nghỉ xong mở lại chạy tiếp",
        )
        self._chk_session_cycle.setToolTip(
            "Chạy liền mấy tiếng không nghỉ là dấu vết dễ thấy nhất.\n"
            "Bật cái này thì bot chơi một đoạn dài ngẫu nhiên, tắt hẳn\n"
            "game (am force-stop, không phải bấm Home), nghỉ một đoạn\n"
            "ngẫu nhiên rồi mở lại và chạy tiếp.\n"
            "Không bao giờ tắt giữa trận — đến giờ mà đang đánh thì chờ\n"
            "đánh xong về làng đã.",
        )
        self._chk_session_cycle.stateChanged.connect(self._on_value_changed)
        cyc_lay.addWidget(self._chk_session_cycle)

        play_row = QHBoxLayout()
        play_row.addWidget(QLabel("Chơi (phút):"))
        self._spin_play_min = QDoubleSpinBox()
        self._spin_play_max = QDoubleSpinBox()
        play_row.addWidget(self._spin_play_min)
        play_row.addWidget(QLabel("→"))
        play_row.addWidget(self._spin_play_max)

        play_row.addSpacing(20)
        play_row.addWidget(QLabel("Nghỉ (phút):"))
        self._spin_break_min = QDoubleSpinBox()
        self._spin_break_max = QDoubleSpinBox()
        play_row.addWidget(self._spin_break_min)
        play_row.addWidget(QLabel("→"))
        play_row.addWidget(self._spin_break_max)
        play_row.addStretch()

        for spin, tip in (
            (self._spin_play_min, "Đoạn chơi ngắn nhất."),
            (self._spin_play_max, "Đoạn chơi dài nhất."),
            (self._spin_break_min, "Nghỉ ngắn nhất."),
            (self._spin_break_max, "Nghỉ dài nhất."),
        ):
            spin.setRange(0.1, 1440.0)
            spin.setSingleStep(1.0)
            spin.setDecimals(1)
            spin.setToolTip(f"{tip}\nMỗi lần đều bốc ngẫu nhiên trong khoảng này.")
            spin.valueChanged.connect(self._on_value_changed)
        cyc_lay.addLayout(play_row)

        test_row = QHBoxLayout()
        self._btn_test_cycle = QPushButton("▶  Chạy thử (30 giây → tắt → 15 giây → mở lại)")
        self._btn_test_cycle.setToolTip(
            "Chạy đúng một chu kỳ ngắn để xem cơ chế có hoạt động không:\n"
            "30 giây nữa bot tắt game, 15 giây sau mở lại và tự chạy tiếp.\n"
            "Bot phải đang chạy thì mới bấm được — đây là kiểm tra thật,\n"
            "không phải mô phỏng. Không cần bật ô ở trên, và chạy thử\n"
            "xong cũng không tự bật.",
        )
        self._btn_test_cycle.clicked.connect(self._on_test_cycle)
        test_row.addWidget(self._btn_test_cycle)

        self._lbl_cycle_status = QLabel("")
        self._lbl_cycle_status.setStyleSheet("color: #c0c0c0;")
        test_row.addWidget(self._lbl_cycle_status, 1)
        cyc_lay.addLayout(test_row)

        root.addWidget(cyc_grp)

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
        self._chk_sweep_up.setChecked(bool(s.get("sweep_up_enabled", False)))
        self._chk_skip_on_fallback.setChecked(bool(s.get("v2_skip_on_fallback", False)))
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
        self._chk_emu_restart.setChecked(bool(s.get("emulator_auto_restart", False)))
        self._edit_emu_name.setText(str(s.get("emulator_name", "") or ""))
        self._refresh_emulator_label()

        # Chu kỳ chơi — nghỉ
        self._chk_session_cycle.setChecked(bool(s.get("session_cycle_enabled", False)))
        self._spin_play_min.setValue(float(s.get("session_play_min_min", 60.0)))
        self._spin_play_max.setValue(float(s.get("session_play_max_min", 75.0)))
        self._spin_break_min.setValue(float(s.get("session_break_min_min", 5.0)))
        self._spin_break_max.setValue(float(s.get("session_break_max_min", 10.0)))

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
        s.set("sweep_up_enabled", self._chk_sweep_up.isChecked())
        s.set("v2_skip_on_fallback", self._chk_skip_on_fallback.isChecked())
        s.set("vision_troop_threshold", self._spin_troop_thr.value())
        s.set("vision_ui_threshold", self._spin_ui_thr.value())
        s.set("vision_building_threshold", self._spin_building_thr.value())
        s.set("ocr_min_interval", self._spin_ocr_interval.value())
        s.set("hero_ability_delay", self._spin_hero_delay.value())
        s.set("deploy_jitter", self._spin_jitter.value())
        s.set("game_package", self._edit_game_pkg.text().strip() or "com.supercell.clashofclans")
        s.set("game_check_interval", self._spin_game_interval.value())
        s.set("auto_launch_game", self._chk_auto_launch.isChecked())
        s.set("emulator_auto_restart", self._chk_emu_restart.isChecked())
        s.set("emulator_name", self._edit_emu_name.text().strip())
        s.set("session_cycle_enabled", self._chk_session_cycle.isChecked())
        s.set("session_play_min_min", self._spin_play_min.value())
        s.set("session_play_max_min", self._spin_play_max.value())
        s.set("session_break_min_min", self._spin_break_min.value())
        s.set("session_break_max_min", self._spin_break_max.value())
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

    def _on_test_cycle(self, *_) -> None:
        """Hẹn một chu kỳ 30 giây / 15 giây trên engine đang chạy.

        Cố tình KHÔNG tự mở bot hộ: chạy thử là để xem bot đang chạy có
        tắt rồi mở lại game và đánh tiếp được không, nên phải chạy trên
        đúng cái engine thật.
        """
        engine = self._engine
        if engine is None or not engine.isRunning():
            self._lbl_cycle_status.setText(
                "Hãy bấm Bắt đầu cho bot chạy trước rồi mới chạy thử được.",
            )
            return
        engine.request_test_cycle(30.0, 15.0)
        self._lbl_cycle_status.setText("Đã hẹn — 30 giây nữa game sẽ tắt.")

    def _on_test_emulator_restart(self) -> None:
        """Tắt/bật lại giả lập ngay, để xem cơ chế gỡ treo có chạy không.

        Chạy trên engine đang chạy chứ không tự gọi ``emulator.restart()``
        ở đây: hàm đó chờ máy ảo lên rồi chờ game vào làng, gọi thẳng từ
        thread giao diện là treo cứng cửa sổ vài phút. Và chạy trên engine
        thật mới kiểm tra được phần bot có tự đứng dậy đánh tiếp không —
        đó mới là thứ đáng nghi.
        """
        engine = self._engine
        if engine is None or not engine.isRunning():
            self._lbl_emu_status.setText(
                "Hãy bấm Bắt đầu cho bot chạy trước rồi mới chạy thử được.",
            )
            return

        from core import emulator
        if not emulator.is_available():
            self._lbl_emu_status.setText(
                "❌ Không tìm thấy ldconsole.exe — chưa chạy thử được.",
            )
            return

        insts = emulator.list_instances()
        name = str(self._settings.get("emulator_name", "") or "").strip()
        target = name or f"index {int(self._settings.get('emulator_index', 0))}"
        answer = QMessageBox.question(
            self,
            "Chạy thử tắt/bật giả lập",
            f"Sẽ ĐÓNG THẬT giả lập '{target}' rồi bật lại ngay.\n\n"
            f"Máy ảo đang thấy: {', '.join(i['name'] for i in insts) or '(không có)'}\n\n"
            "Mọi thứ đang mở trong giả lập sẽ mất. Mất khoảng 1–3 phút.\n"
            "Tiếp tục?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self._lbl_emu_status.setText("Đã huỷ.")
            return

        engine.request_test_emulator_restart()
        self._lbl_emu_status.setText("Đã hẹn — đang tắt giả lập…")

    def _probe_multi_touch(self, *_) -> None:
        """Say straight away whether the switch can actually do anything.

        Checked on the box itself rather than left to a battle-time log
        line, because the fallback is silent: without root the attack
        still runs, just one finger at a time, and the two look identical
        from outside.
        """
        if not self._chk_multi_touch.isChecked():
            self._lbl_multi_touch.setText("")
            return

        self._lbl_multi_touch.setText("Đang kiểm tra root…")
        QApplication.processEvents()      # paint before the ADB round-trip

        from core import multi_touch

        if not multi_touch.have_root(refresh=True):
            self._lbl_multi_touch.setText(
                "✗ Không cách nào chạy được quyền root — bot giữ lần lượt "
                "từng cạnh như cũ.\n"
                f"Đã thử: {multi_touch.last_root_attempts()}\n"
                "LDPlayer: Cài đặt → Mục khác → bật Quyền Root → bấm Lưu → "
                "TẮT HẲN rồi mở lại giả lập (bật xong không khởi động lại "
                "thì adb vẫn chạy quyền cũ).",
            )
            self._lbl_multi_touch.setStyleSheet(
                "color: #e0a030; padding-left: 22px;")
            return

        cfg = multi_touch._cfg(self._multi_touch_config())
        found = multi_touch.touch_device(cfg, refresh=True)
        if found is None:
            self._lbl_multi_touch.setText(
                "✗ Có root nhưng không tìm thấy thiết bị cảm ứng nào. Chỉ "
                "định tay event_device trong config/v2_attack_rules.json.",
            )
            self._lbl_multi_touch.setStyleSheet(
                "color: #e0a030; padding-left: 22px;")
            return

        node, raw_max = found
        self._lbl_multi_touch.setText(
            f"✓ Có root (qua '{multi_touch.root_mode()}'), dùng {node} "
            f"(toạ độ 0..{raw_max}). "
            "CHƯA hiệu chỉnh chiều toạ độ — xem docs/multi-finger-deploy.md "
            "trước khi đánh thật, sai chiều là bấm nhầm chỗ.",
        )
        self._lbl_multi_touch.setStyleSheet(
            "color: #5cb85c; padding-left: 22px;")

    @staticmethod
    def _multi_touch_config() -> dict:
        """The multi_touch block, read straight from disk.

        Settings does not carry it — it lives with the other V2 tunables
        so it can be hot-reloaded — and this probe must not depend on a
        running orchestrator.
        """
        import json
        from pathlib import Path

        path = (Path(__file__).resolve().parent.parent
                / "config" / "v2_attack_rules.json")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _on_reset(self) -> None:
        self._settings.reset()
        self._load_values()
        self.settings_changed.emit()
        self._status_lbl.setText("✓ Đã khôi phục mặc định")

    def _update_preset_desc(self, key: str) -> None:
        p = PRESETS.get(key, {})
        self._preset_desc.setText(p.get("description", ""))

    def _refresh_emulator_label(self) -> None:
        """Nói thẳng có tìm thấy ldconsole.exe hay không.

        Không có nó thì công tắc trên kia là công tắc giả — bật cũng
        không làm gì, và người dùng chỉ biết điều đó khi giả lập treo
        lần sau. Thà nói ngay lúc bật.
        """
        try:
            from core import emulator
            path = emulator.console_path()
        except Exception:
            path = None

        if path:
            self._lbl_emu_found.setText("✅ ldconsole")
            self._lbl_emu_found.setToolTip(path)
        else:
            self._lbl_emu_found.setText("❌ không thấy ldconsole")
            self._lbl_emu_found.setToolTip(
                "Không tìm thấy ldconsole.exe ở các chỗ cài thường gặp.\n"
                "Điền đường dẫn đầy đủ vào 'emulator_console_path' trong\n"
                "profiles/settings.json thì tính năng mới chạy được.",
            )

    def _all_widgets(self):
        """Every control _load_values touches.

        A control missing from this list keeps its signals live while
        values are being loaded, so setting it fires _save_values in the
        middle of the load. That save reads the controls loaded LATER in
        the same pass, which still hold the values Qt gave them at
        construction — for a spin box, its minimum. The freshly loaded
        settings are then overwritten by those minimums and the load
        continues reading what it just clobbered.

        That is how the vision thresholds reset themselves to exactly
        0.10 / 0.40 / 0.20 — the three spin-box minimums — every time the
        Settings tab was opened, and how the multi-finger switch turned
        itself back off after being ticked.
        """
        return [
            self._combo_preset,
            self._spin_tap_min, self._spin_tap_max,
            self._spin_swipe, self._spin_tick,
            self._chk_skip_loot, self._chk_skip_timer,
            self._chk_fast_entry, self._chk_multi_touch,
            self._chk_sweep_up, self._chk_skip_on_fallback,
            self._spin_troop_thr, self._spin_ui_thr,
            self._spin_building_thr, self._spin_ocr_interval,
            self._spin_hero_delay, self._spin_jitter,
            self._edit_game_pkg, self._spin_game_interval, self._chk_auto_launch,
            self._chk_emu_restart, self._edit_emu_name,
            self._chk_session_cycle,
            self._spin_play_min, self._spin_play_max,
            self._spin_break_min, self._spin_break_max,
            self._spin_max_lines, self._spin_font, self._chk_debug,
        ]
