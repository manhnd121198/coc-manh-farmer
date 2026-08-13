"""Opening the Settings tab must not rewrite the settings it is loading.

`_load_values` blocks each control's signals before assigning to it. A
control left off that list stays live, so assigning to it fires
`_save_values` MID-LOAD — and that save reads the controls loaded later
in the same pass, which still hold what Qt gave them at construction. For
a spin box that is its minimum.

The result was silent and repeatable: every time the Settings tab was
built, the three vision thresholds reset to exactly their minimums and
the multi-finger switch turned itself back off.
"""

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    _HAVE_QT = True
except Exception:                                    # pragma: no cover
    _HAVE_QT = False


@unittest.skipUnless(_HAVE_QT, "requires PyQt5")
class SettingsTabLoadTest(unittest.TestCase):
    SAVED = {
        "multi_touch_enabled": True,
        "hv_fast_entry": True,
        "vision_ui_threshold": 0.80,
        "vision_troop_threshold": 0.35,
        "vision_building_threshold": 0.38,
        "session_cycle_enabled": True,
        "session_play_min_min": 42.0,
        "session_break_max_min": 12.0,
    }

    def setUp(self):
        from core.settings import Settings, _SETTINGS_FILE
        self._path = _SETTINGS_FILE
        self._backup = None
        if os.path.isfile(self._path):
            with open(self._path, encoding="utf-8") as fh:
                self._backup = fh.read()
        self.addCleanup(self._restore)

        data = json.loads(self._backup) if self._backup else {}
        data.update(self.SAVED)
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

        Settings._instance = None                    # re-read from disk
        self.addCleanup(setattr, Settings, "_instance", None)
        self.settings = Settings()

    def _restore(self):
        if self._backup is None:
            if os.path.isfile(self._path):
                os.remove(self._path)
        else:
            with open(self._path, "w", encoding="utf-8") as fh:
                fh.write(self._backup)

    def test_building_the_tab_preserves_every_saved_value(self):
        from ui.settings_tab import SettingsTab
        SettingsTab()
        for key, expected in self.SAVED.items():
            self.assertEqual(
                expected, self.settings.get(key),
                f"opening the Settings tab rewrote {key}",
            )

    def test_every_loaded_control_has_its_signals_blocked(self):
        """The guard itself: any control assigned during the load must be
        in the blocked list, or it re-introduces the mid-load save."""
        from ui.settings_tab import SettingsTab
        tab = SettingsTab()
        blocked = {id(w) for w in tab._all_widgets()}
        loaded = [
            tab._combo_preset, tab._spin_tap_min, tab._spin_tap_max,
            tab._spin_swipe, tab._spin_tick, tab._chk_skip_loot,
            tab._chk_skip_timer, tab._chk_fast_entry, tab._chk_multi_touch,
            tab._spin_troop_thr, tab._spin_ui_thr, tab._spin_building_thr,
            tab._spin_ocr_interval, tab._spin_hero_delay, tab._spin_jitter,
            tab._edit_game_pkg, tab._spin_game_interval, tab._chk_auto_launch,
            tab._chk_session_cycle, tab._spin_play_min, tab._spin_play_max,
            tab._spin_break_min, tab._spin_break_max,
            tab._spin_max_lines, tab._spin_font, tab._chk_debug,
        ]
        missing = [w for w in loaded if id(w) not in blocked]
        self.assertEqual([], missing, "controls loaded but not signal-blocked")


if __name__ == "__main__":
    unittest.main()
