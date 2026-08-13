"""
Smart Vision AI V2 — reusable QGroupBox embedded in HV and BB tabs.

UI surface for the CSR (Config + Skills + Rules) attack system:
  • Enable / disable per village.
  • Strategy mode (smart / building / storage) — legacy compat.
  • Rule selector (auto / smart_default / perimeter / air / ground / raid / snipe).
  • NEW: Reload Config button (hot-reload JSON files in config/).
  • NEW: Active rule indicator (shows what the orchestrator picked).
  • Decoration fade wait.
  • Target picker synced with the Asset Manager.
"""

import os
import subprocess
import sys
from pathlib import Path

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QComboBox, QDoubleSpinBox, QFormLayout, QPushButton,
    QFrame, QSizePolicy,
)

from core.logger import BotLogger
from core.settings import Settings
from vision.template_manager import list_assets_by_category

log = BotLogger.get("v2_panel")

_BUILDING_CATEGORIES = ("buildings", "builder_base", "custom")

_RULE_OPTIONS = [
    ("Auto  — bot tự chọn cách đánh hợp nhất", "auto"),
    ("Mặc định  — tìm hành lang rộng nhất rồi giữ thả",      "smart_default"),
    ("Ring Sweep  — hold 4 điểm quanh base (sát vùng đỏ)", "ring_sweep"),
    ("Quét viền  — vuốt quanh map, điểm bắt đầu ngẫu nhiên", "perimeter_sweep"),
    ("Đánh không quân  — dàn theo hành lang bay an toàn",    "air_attack"),
    ("Bộ binh mở phễu  — 2 mũi mở đường rồi thả đợt chính",  "ground_funnel"),
    ("Cướp tài nguyên  — dò từng kho rồi thả",               "resource_raid"),
    ("Bắn tỉa TH  — hành lang an toàn gần công trình đích",  "th_snipe"),
]


class SmartV2Panel(QGroupBox):
    """One V2 configuration block. ``mode_key`` ∈ {"hv","bb"} so the
    same widget feeds two independent settings groups."""

    settings_changed = pyqtSignal()

    def __init__(self, mode_key: str, parent=None):
        super().__init__("Smart Vision AI V2 (CSR)", parent)
        assert mode_key in ("hv", "bb")
        self._mode_key = mode_key
        self._s = Settings()
        self._engine = None
        self._init_ui()
        self.refresh_targets()
        self._load()

    def set_engine(self, engine) -> None:
        """Bound by MainWindow so 'Reload Config' can hot-reload the
        running orchestrator inside the engine threads."""
        self._engine = engine

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        self._chk_enable = QCheckBox("Bật V2 (thả quân né vùng đỏ)")
        self._chk_enable.setToolTip(
            "Bật thì làng này dùng bộ lập kế hoạch V2. Nếu V2 không\n"
            "chạy được thì tự rơi về luồng V36 cũ. Cài riêng cho\n"
            "từng làng.",
        )
        self._chk_enable.stateChanged.connect(self._emit)
        root.addWidget(self._chk_enable)

        form = QFormLayout()

        self._combo_mode = QComboBox()
        self._combo_mode.addItem("Thông minh  (không nhắm mục tiêu)",   "smart")
        self._combo_mode.addItem("Nhắm công trình  (chỗ an toàn gần nhất)", "building")
        self._combo_mode.addItem("Nhắm kho  (dò rồi thả gần nhất)",     "storage")
        self._combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Cách đánh:", self._combo_mode)

        self._combo_target = QComboBox()
        self._combo_target.setMinimumWidth(260)
        self._combo_target.setToolTip(
            "Đồng bộ với tab Ảnh mẫu. Chọn một công trình (hoặc bản\n"
            "theo cấp) — bot nhắm cái gần nhất trên màn hình rồi thả\n"
            "quân ở ô an toàn sát bên.",
        )
        self._combo_target.currentIndexChanged.connect(self._emit)
        form.addRow("Mục tiêu:", self._combo_target)

        self._combo_rule = QComboBox()
        for label, value in _RULE_OPTIONS:
            self._combo_rule.addItem(label, value)
        self._combo_rule.setToolTip(
            "Ép dùng một cách đánh cố định. 'Auto' để bot tự chọn theo\n"
            "quân và mục tiêu bạn đã set. Chỉ ép khi muốn bắt buộc\n"
            "đánh theo một kiểu.",
        )
        self._combo_rule.currentIndexChanged.connect(self._emit)
        form.addRow("Luật V2:", self._combo_rule)

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Chờ vật trang trí mờ đi (giây):"))
        self._spin_wait = QDoubleSpinBox()
        self._spin_wait.setRange(0.0, 15.0)
        self._spin_wait.setSingleStep(0.5)
        self._spin_wait.setDecimals(1)
        self._spin_wait.setToolTip(
            "Sau khi đọc tài nguyên thì chờ bấy nhiêu giây rồi mới chụp\n"
            "lại màn hình để tính chỗ thả. Vật trang trí của đối thủ mờ\n"
            "dần sau vài giây, chờ xong thì nhận vùng đỏ mới chuẩn.",
        )
        self._spin_wait.valueChanged.connect(self._emit)
        zoom_row.addWidget(self._spin_wait)
        zoom_row.addStretch()

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)

        cfg_row = QHBoxLayout()
        self._lbl_cfg_status = QLabel("Config: sẵn sàng (config/v2_*.json)")
        self._lbl_cfg_status.setStyleSheet("color: #c0c0c0;")
        self._lbl_cfg_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        cfg_row.addWidget(self._lbl_cfg_status)

        self._btn_reload = QPushButton("Nạp lại config")
        self._btn_reload.setToolTip(
            "Nạp lại các file config/v2_*.json vào bot đang chạy,\n"
            "không cần khởi động lại.",
        )
        self._btn_reload.clicked.connect(self._on_reload_config)
        cfg_row.addWidget(self._btn_reload)

        self._btn_open = QPushButton("Mở thư mục config")
        self._btn_open.setToolTip(
            "Mở thư mục config/ trong trình quản lý tệp để sửa\n"
            "v2_attack_rules.json / v2_troop_profiles.json / v2_spell_profiles.json.",
        )
        self._btn_open.clicked.connect(self._on_open_config_folder)
        cfg_row.addWidget(self._btn_open)

        root.addLayout(form)
        root.addLayout(zoom_row)
        root.addWidget(sep)
        root.addLayout(cfg_row)

    # ── Sync from Asset Manager ─────────────────────────────────────
    def refresh_targets(self) -> None:
        prev = self._combo_target.currentData()
        self._combo_target.blockSignals(True)
        self._combo_target.clear()
        self._combo_target.addItem("(không)", "")
        seen: set[str] = set()
        for category in _BUILDING_CATEGORIES:
            for key, label, has_image in list_assets_by_category(category):
                if key in seen:
                    continue
                seen.add(key)
                tag = "✓" if has_image else "○"
                self._combo_target.addItem(f"{tag}  {label}  [{category}]", key)
        if prev:
            idx = self._combo_target.findData(prev)
            if idx >= 0:
                self._combo_target.setCurrentIndex(idx)
        self._combo_target.blockSignals(False)

    # ── Settings I/O ────────────────────────────────────────────────
    def _key(self, base: str) -> str:
        return f"{base}_{self._mode_key}"

    def _load(self) -> None:
        for w in (self._chk_enable, self._combo_mode, self._combo_target,
                  self._combo_rule, self._spin_wait):
            w.blockSignals(True)

        self._chk_enable.setChecked(bool(self._s.get(self._key("v2_enabled"), False)))
        mode = str(self._s.get(self._key("v2_mode"), "smart"))
        idx = self._combo_mode.findData(mode)
        self._combo_mode.setCurrentIndex(max(0, idx))

        target = str(self._s.get(self._key("v2_target"), ""))
        tidx = self._combo_target.findData(target)
        self._combo_target.setCurrentIndex(max(0, tidx))

        rule = str(self._s.get(self._key("v2_rule"), "auto"))
        ridx = self._combo_rule.findData(rule)
        self._combo_rule.setCurrentIndex(max(0, ridx))

        self._spin_wait.setValue(float(self._s.get("v2_decoration_wait", 5.0)))

        for w in (self._chk_enable, self._combo_mode, self._combo_target,
                  self._combo_rule, self._spin_wait):
            w.blockSignals(False)

        self._on_mode_changed()

    def _save(self) -> None:
        self._s.set(self._key("v2_enabled"),  self._chk_enable.isChecked())
        self._s.set(self._key("v2_mode"),     self._combo_mode.currentData() or "smart")
        self._s.set(self._key("v2_target"),   self._combo_target.currentData() or "")
        self._s.set(self._key("v2_rule"),     self._combo_rule.currentData() or "auto")
        self._s.set("v2_decoration_wait",     self._spin_wait.value())
        self._s.save()

    def _on_mode_changed(self) -> None:
        mode = self._combo_mode.currentData()
        self._combo_target.setEnabled(mode in ("building", "storage"))
        self._emit()

    # ── Config controls ─────────────────────────────────────
    def _on_reload_config(self) -> None:
        """Hot-reload the V2 JSON configs in the running engine."""
        ok_engine = False
        if self._engine is not None:
            for attr in ("_home_logic", "_bb_logic"):
                logic = getattr(self._engine, attr, None)
                if logic is None:
                    continue
                v2 = getattr(logic, "_v2", None)
                if v2 is None or not hasattr(v2, "reload_config"):
                    continue
                try:
                    v2.reload_config()
                    ok_engine = True
                except Exception as exc:
                    log.warning("reload_config failed on %s: %s", attr, exc)
        msg = (
            "Config reloaded into running orchestrator."
            if ok_engine
            else "Config will be reloaded on next attack (engine not running)."
        )
        self._lbl_cfg_status.setText(f"Config: {msg}")
        log.info("V2 panel reload_config: %s", msg)

    def _on_open_config_folder(self) -> None:
        root = Path(__file__).resolve().parent.parent / "config"
        root.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(root))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(root)])
            else:
                subprocess.Popen(["xdg-open", str(root)])
        except Exception as exc:
            log.warning("open config folder failed: %s", exc)
            self._lbl_cfg_status.setText(f"Config: open failed — {root}")

    def _emit(self, *_) -> None:
        self._save()
        self.settings_changed.emit()
