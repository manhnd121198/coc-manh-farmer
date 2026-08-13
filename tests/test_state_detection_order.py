"""The order the state markers are asked in is load-bearing.

Both villages draw the same red surrender button, the same "battle ends
in" timer and the same damage panel, in the same screen positions. On a
Vietnamese client they are pixel-identical: the Builder Base surrender
button scores 0.97 against the Home Village template. Nothing in
``detect_state`` can tell them apart, so the only defence is asking a
Builder-Base-only marker first.

Measured on captures from a live 1350x1080 emulator:
  BB surrender vs HV template  ->  0.97   (matches, wrongly)
  BB "Bên phòng thủ" header    ->  BB only, no Home Village equivalent
"""

import json
import pathlib
import unittest


SOURCE = pathlib.Path("vision/screen_reader.py").read_text(encoding="utf-8")
MANIFEST = json.loads(
    pathlib.Path("assets/templates/manifest.json").read_text(encoding="utf-8"),
)


def _line_of(needle: str) -> int:
    for number, line in enumerate(SOURCE.splitlines()):
        if needle in line and "return GameState" in line:
            return number
    raise AssertionError(f"{needle!r} is not asked in detect_state at all")


class BuilderBaseIsCheckedFirstTest(unittest.TestCase):
    def test_the_bb_only_marker_beats_every_shared_battle_marker(self):
        bb = _line_of("bb_side_label")
        for shared in ("surrender_button", "lot_asseset",
                       "end_battle_button", "timer_top_start"):
            self.assertLess(
                bb, _line_of(shared),
                f"{shared} is shared with the Builder Base and would win",
            )

    def test_the_bb_marker_is_registered(self):
        from vision.template_manager import DEFAULT_ASSETS
        self.assertIn("bb_side_label", DEFAULT_ASSETS)
        self.assertIn("bb_side_label", MANIFEST)
        self.assertTrue(
            pathlib.Path(
                MANIFEST["bb_side_label"]["file"].replace("\\", "/"),
            ).is_file(),
            "the template file is missing — the check would silently never fire",
        )


class TemplatesRecordTheirCaptureSizeTest(unittest.TestCase):
    """A template without screen_w cannot be rescaled onto another panel,
    so it only works on the resolution it happened to be cropped at."""

    TEXT_BEARING = (
        "attack_button", "attack_button2", "next_button", "end_battle_button",
        "surrender_button", "end_battle_confirm", "return_home",
        "ranked_mode_btn", "normal_mode_btn", "searching_indicator",
        "timer_top_start", "lot_asseset",
        "bb_find_match", "bb_attack_confirm", "bb_return_home",
        "bb_prep_text", "bb_active_text", "bb_battle_hud", "bb_side_label",
    )

    def test_every_recaptured_template_knows_its_screen_width(self):
        for name in self.TEXT_BEARING:
            entry = MANIFEST.get(name)
            self.assertIsNotNone(entry, name)
            self.assertTrue(int(entry.get("screen_w") or 0) > 0, name)


if __name__ == "__main__":
    unittest.main()
