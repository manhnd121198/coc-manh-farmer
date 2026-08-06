"""
Home Village Tab — V36: Drag & Drop Restored + Smart Retreat UI.

V36 CHANGES:
  • Restored the beautiful QListWidget for Drag & Drop ordering (As requested!).
  • Fully supports dynamic asset refreshing from TemplateManager.
  • Timer & Dynamic Loot Retreat settings integrated.
"""

import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QSpinBox, QPushButton, QCheckBox,
    QComboBox, QScrollArea, QFileDialog, QListWidget, 
    QListWidgetItem, QAbstractItemView, QMessageBox, QInputDialog
)
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt, pyqtSignal

from core.logger import BotLogger
from core.adb_handler import start_recording, stop_recording, save_recording
from vision.template_manager import list_assets_by_category
from ui.smart_v2_panel import SmartV2Panel

log = BotLogger.get("hv_tab")


class _OrderableList(QListWidget):
    """A custom list widget that allows reordering and checking items."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("orderable_list")
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setMinimumHeight(160)

    def populate(self, assets: list[tuple[str, str, bool]], saved_selection: list[str]):
        self.clear()
        added_keys = set()
        
        # Add saved items in their saved order first (Checked)
        for key in saved_selection:
            for k, label, has_img in assets:
                if k == key:
                    self._add_item(k, label, has_img, True)
                    added_keys.add(k)
                    break
                    
        # Add the rest unchecked
        for k, label, has_img in assets:
            if k not in added_keys:
                self._add_item(k, label, has_img, False)

    def _add_item(self, key, label, has_img, checked):
        status = "✅" if has_img else "❌"
        item = QListWidgetItem(f"{status} {label}")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        item.setData(Qt.UserRole, key)
        self.addItem(item)

    def get_selected_ordered(self) -> list[str]:
        selected = []
        for i in range(self.count()):
            item = self.item(i)
            if item.checkState() == Qt.Checked:
                selected.append(item.data(Qt.UserRole))
        return selected


class HomeVillageTab(QWidget):
    start_requested = pyqtSignal()
    stop_requested  = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._recording = False
        self._init_ui()

    def _init_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(10)

        hdr = QLabel("Làng chính — Cấu hình cày tài nguyên")
        hdr.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(hdr)

        # ── Loot Thresholds ─────────────────────────────────────────────
        loot_group = QGroupBox("Ngưỡng tài nguyên để đánh")
        loot_grid = QGridLayout()

        loot_grid.addWidget(QLabel("Vàng tối thiểu:"), 0, 0)
        self._min_gold = QSpinBox()
        self._min_gold.setRange(0, 5_000_000)
        self._min_gold.setSingleStep(50000)
        self._min_gold.setValue(200000)
        loot_grid.addWidget(self._min_gold, 0, 1)

        loot_grid.addWidget(QLabel("Elixir tối thiểu:"), 0, 2)
        self._min_elixir = QSpinBox()
        self._min_elixir.setRange(0, 5_000_000)
        self._min_elixir.setSingleStep(50000)
        self._min_elixir.setValue(200000)
        loot_grid.addWidget(self._min_elixir, 0, 3)

        loot_grid.addWidget(QLabel("Elixir đen tối thiểu:"), 1, 0)
        self._min_dark = QSpinBox()
        self._min_dark.setRange(0, 500_000)
        self._min_dark.setSingleStep(500)
        self._min_dark.setValue(1000)
        loot_grid.addWidget(self._min_dark, 1, 1)

        loot_group.setLayout(loot_grid)
        layout.addWidget(loot_group)

        # ── Auto-Retreat Systems ───────────────────────────────────
        retreat_group = QGroupBox("Tự động rút quân")
        retreat_layout = QGridLayout()

        self._retreat_enabled = QCheckBox("Rút khi tài nguyên còn lại quá ít")
        self._retreat_enabled.setChecked(False)
        retreat_layout.addWidget(self._retreat_enabled, 0, 0, 1, 2)

        self._retreat_heroes = QCheckBox("Rút khi Warden và 1 hero nữa chết")
        self._retreat_heroes.setChecked(False)
        self._retreat_heroes.setStyleSheet("color: #e9b44c; font-weight: bold;")
        retreat_layout.addWidget(self._retreat_heroes, 0, 2, 1, 2)

        retreat_layout.addWidget(QLabel("Rút khi vàng còn ≤"), 1, 0)
        self._retreat_gold = QSpinBox()
        self._retreat_gold.setRange(0, 5_000_000)
        self._retreat_gold.setSingleStep(10000)
        self._retreat_gold.setValue(50000)
        retreat_layout.addWidget(self._retreat_gold, 1, 1)

        retreat_layout.addWidget(QLabel("Rút khi elixir còn ≤"), 1, 2)
        self._retreat_elixir = QSpinBox()
        self._retreat_elixir.setRange(0, 5_000_000)
        self._retreat_elixir.setSingleStep(10000)
        self._retreat_elixir.setValue(50000)
        retreat_layout.addWidget(self._retreat_elixir, 1, 3)

        retreat_layout.addWidget(QLabel("Rút khi elixir đen còn ≤"), 2, 0)
        self._retreat_dark = QSpinBox()
        self._retreat_dark.setRange(0, 500_000)
        self._retreat_dark.setSingleStep(100)
        self._retreat_dark.setValue(500)
        retreat_layout.addWidget(self._retreat_dark, 2, 1)

        retreat_layout.addWidget(QLabel("Rút khi thời gian còn ≤ (giây):"), 3, 0)
        self._retreat_time = QSpinBox()
        self._retreat_time.setRange(0, 180)
        self._retreat_time.setValue(0)
        self._retreat_time.setToolTip("Để 0 là tắt. Ví dụ 30: còn 30 giây thì đầu hàng.")
        retreat_layout.addWidget(self._retreat_time, 3, 1)

        # ── NEW: Post-Deployment Timer (silent end-of-battle countdown) ──
        self._deploy_timer_enabled = QCheckBox("Tự kết thúc trận sau khi thả hết quân và hero")
        self._deploy_timer_enabled.setToolTip(
            "Thả xong quân là bot bắt đầu đếm ngược ngầm, hết giờ thì\n"
            "đầu hàng luôn. Hợp với kiểu cày nhanh, không chờ hết trận.",
        )
        retreat_layout.addWidget(self._deploy_timer_enabled, 4, 0, 1, 4)

        retreat_layout.addWidget(QLabel("Đếm ngược sau khi thả (giây):"), 5, 0)
        self._deploy_timer_seconds = QSpinBox()
        self._deploy_timer_seconds.setRange(5, 300)
        self._deploy_timer_seconds.setSingleStep(5)
        self._deploy_timer_seconds.setValue(90)
        retreat_layout.addWidget(self._deploy_timer_seconds, 5, 1)

        retreat_group.setLayout(retreat_layout)
        layout.addWidget(retreat_group)

        # ── Attack Mode ───────────────────────────────────────
        mode_group = QGroupBox("Kiểu đánh")
        mode_layout = QHBoxLayout()

        mode_layout.addWidget(QLabel("Loại trận:"))
        self._match_mode = QComboBox()
        self._match_mode.addItems(["⚔  Thường", "🏆  Xếp hạng"])
        self._match_mode.setToolTip(
            "Sau khi bấm Attack, bot sẽ bấm nút chế độ nào:\n"
            "  • Thường     → normal_mode_btn\n"
            "  • Xếp hạng   → ranked_mode_btn",
        )
        mode_layout.addWidget(self._match_mode)

        mode_layout.addSpacing(16)

        mode_layout.addWidget(QLabel("Chế độ:"))
        self._attack_mode = QComboBox()
        self._attack_mode.addItems(["Smart Vision AI", "Phát lại thao tác đã ghi"])
        self._attack_mode.currentIndexChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self._attack_mode)

        self._macro_label = QLabel("Thao tác:")
        self._macro_label.hide()
        mode_layout.addWidget(self._macro_label)

        self._macro_path_label = QLabel("(chưa chọn)")
        self._macro_path_label.setStyleSheet("color: #9e9e9e;")
        self._macro_path_label.hide()
        mode_layout.addWidget(self._macro_path_label)

        self._macro_pick_btn = QPushButton("📂  Chọn tệp…")
        self._macro_pick_btn.hide()
        self._macro_pick_btn.clicked.connect(self._pick_macro)
        mode_layout.addWidget(self._macro_pick_btn)

        self._record_btn = QPushButton("🔴  Ghi thao tác")
        self._record_btn.setMinimumHeight(32)
        self._record_btn.clicked.connect(self._toggle_recording)
        mode_layout.addWidget(self._record_btn)

        mode_layout.addStretch()
        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)
        self._macro_file = ""

        # ── Lists (Drag & Drop) ───────────────────────────────────────
        lists_layout = QHBoxLayout()

        troop_box = QGroupBox("Quân (kéo để đổi thứ tự)")
        tv = QVBoxLayout()
        self._troop_list = _OrderableList()
        tv.addWidget(self._troop_list)
        troop_box.setLayout(tv)
        lists_layout.addWidget(troop_box)

        hero_box = QGroupBox("Hero (kéo để đổi thứ tự)")
        hv = QVBoxLayout()
        self._hero_list = _OrderableList()
        hv.addWidget(self._hero_list)
        hero_box.setLayout(hv)
        lists_layout.addWidget(hero_box)

        spell_box = QGroupBox("Phép (kéo để đổi thứ tự)")
        sv = QVBoxLayout()
        self._spell_list = _OrderableList()
        sv.addWidget(self._spell_list)
        spell_box.setLayout(sv)
        lists_layout.addWidget(spell_box)

        layout.addLayout(lists_layout)
        self._rebuild_lists()

        # ── Smart Vision V2 ─────────────────────────────────────────────
        self._v2 = SmartV2Panel("hv")
        layout.addWidget(self._v2)

        # ── Action Buttons ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("▶  Bắt đầu cày")
        self._start_btn.setObjectName("start_button")
        self._start_btn.setMinimumHeight(40)
        self._start_btn.clicked.connect(self.start_requested.emit)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("■  Dừng")
        self._stop_btn.setObjectName("stop_button")
        self._stop_btn.setMinimumHeight(40)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        btn_row.addWidget(self._stop_btn)
        layout.addLayout(btn_row)

        layout.addStretch()
        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _rebuild_lists(self) -> None:
        t_sel = self._troop_list.get_selected_ordered()
        h_sel = self._hero_list.get_selected_ordered()
        s_sel = self._spell_list.get_selected_ordered()

        self._troop_list.populate(list_assets_by_category("troops"), t_sel)
        self._hero_list.populate([a for a in list_assets_by_category("heroes") if a[0] != "battle_machine"], h_sel)
        self._spell_list.populate(list_assets_by_category("spells"), s_sel)

    def _on_mode_changed(self, index: int) -> None:
        is_macro = index == 1
        self._macro_label.setVisible(is_macro)
        self._macro_path_label.setVisible(is_macro)
        self._macro_pick_btn.setVisible(is_macro)

    def _pick_macro(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Macro Recording", "recordings", "JSON (*.json);;All Files (*)")
        if path:
            self._macro_file = path
            self._macro_path_label.setText(os.path.basename(path))

    def _toggle_recording(self) -> None:
        if not self._recording:
            self._recording = True
            self._record_btn.setText("⏹  Dừng ghi")
            self._record_btn.setObjectName("stop_button")
            self._record_btn.setStyle(self._record_btn.style())
            start_recording()
        else:
            self._recording = False
            self._record_btn.setText("🔴  Ghi thao tác")
            self._record_btn.setObjectName("")
            self._record_btn.setStyle(self._record_btn.style())
            events = stop_recording()
            if not events: return
            name, ok = QInputDialog.getText(self, "Save Macro", "Macro name:", text="hv_attack_macro")
            if ok and name.strip():
                os.makedirs("recordings", exist_ok=True)
                filepath = os.path.join("recordings", f"{name.strip()}.json")
                save_recording(events, filepath)
                self._macro_file = filepath
                self._macro_path_label.setText(os.path.basename(filepath))
                self._attack_mode.setCurrentIndex(1)

    def get_settings(self) -> dict:
        mode = "smart_target" if self._attack_mode.currentIndex() == 0 else "macro_playback"
        match_mode = "ranked" if self._match_mode.currentIndex() == 1 else "normal"
        return {
            "min_gold": self._min_gold.value(),
            "min_elixir": self._min_elixir.value(),
            "min_dark_elixir": self._min_dark.value(),
            "auto_retreat_enabled": self._retreat_enabled.isChecked(),
            "retreat_heroes_dead": self._retreat_heroes.isChecked(),
            "retreat_gold": self._retreat_gold.value(),
            "retreat_elixir": self._retreat_elixir.value(),
            "retreat_dark_elixir": self._retreat_dark.value(),
            "retreat_time": self._retreat_time.value(),
            "deploy_timer_enabled": self._deploy_timer_enabled.isChecked(),
            "deploy_timer_seconds": self._deploy_timer_seconds.value(),
            "hv_match_mode": match_mode,
            "attack_strategy": mode,
            "macro_file": self._macro_file,
            "selected_troops": self._troop_list.get_selected_ordered(),
            "selected_heroes": self._hero_list.get_selected_ordered(),
            "selected_spells": self._spell_list.get_selected_ordered(),
        }

    def load_settings(self, profile: dict) -> None:
        self._min_gold.setValue(profile.get("min_gold", 200000))
        self._min_elixir.setValue(profile.get("min_elixir", 200000))
        self._min_dark.setValue(profile.get("min_dark_elixir", 1000))

        self._retreat_enabled.setChecked(profile.get("auto_retreat_enabled", False))
        self._retreat_heroes.setChecked(profile.get("retreat_heroes_dead", False))
        self._retreat_gold.setValue(profile.get("retreat_gold", 50000))
        self._retreat_elixir.setValue(profile.get("retreat_elixir", 50000))
        self._retreat_dark.setValue(profile.get("retreat_dark_elixir", 500))
        self._retreat_time.setValue(profile.get("retreat_time", 0))
        self._deploy_timer_enabled.setChecked(profile.get("deploy_timer_enabled", False))
        self._deploy_timer_seconds.setValue(profile.get("deploy_timer_seconds", 90))
        self._match_mode.setCurrentIndex(
            1 if str(profile.get("hv_match_mode", "normal")).lower() == "ranked" else 0
        )

        strategy = profile.get("attack_strategy", "smart_target")
        self._attack_mode.setCurrentIndex(1 if strategy == "macro_playback" else 0)
        self._macro_file = profile.get("macro_file", "")
        if self._macro_file:
            self._macro_path_label.setText(os.path.basename(self._macro_file))

        self._troop_list.populate(list_assets_by_category("troops"), profile.get("selected_troops", []))
        self._hero_list.populate([a for a in list_assets_by_category("heroes") if a[0] != "battle_machine"], profile.get("selected_heroes", []))
        self._spell_list.populate(list_assets_by_category("spells"), profile.get("selected_spells", []))

    def set_running_state(self, running: bool) -> None:
        self._running = running
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)

    def refresh_assets(self) -> None:
        """Re-populate the lists from the manifest while preserving selection/order."""
        self._rebuild_lists()
        if hasattr(self, "_v2"):
            self._v2.refresh_targets()