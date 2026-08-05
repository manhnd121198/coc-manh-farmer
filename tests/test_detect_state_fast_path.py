"""Speed-ups on the HOME → CONFIRMING → OPPONENT_FOUND path.

Every one of these is a shortcut that is silent when it breaks: the bot
still works, just slower. So the wiring is pinned here.

Source is parsed with ``ast`` — importing screen_reader would pull in cv2.
"""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "core" / "bot_engine.py"
SR_PATH = ROOT / "vision" / "screen_reader.py"


def _func(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}")


class ModeGatingTest(unittest.TestCase):
    """Builder Base templates cost ~17% of every detect_state call and
    can never match while farming Home Village."""

    def test_detect_state_takes_a_mode_hint(self):
        node = _func(SR_PATH, "detect_state")
        args = [a.arg for a in node.args.args]

        self.assertIn("mode", args)
        # Optional — old call sites (and BB) must keep checking everything.
        self.assertEqual(1, len(node.args.defaults))

    def test_bb_templates_are_skipped_in_home_village(self):
        body = ast.unparse(_func(SR_PATH, "detect_state"))

        self.assertIn("check_bb = mode != 'home_village'", body)
        for tmpl in ("bb_find_match", "bb_attack_confirm", "bb_return_home",
                     "bb_battle_result", "bb_battle_hud", "bb_prep_text"):
            self.assertIn(tmpl, body)
        # Everything BB-specific sits behind the gate: no bb_ lookup may
        # appear before the first `if check_bb:`.
        head = body.split("if check_bb:")[0]
        self.assertNotIn("bb_", head)

    def test_engine_passes_its_mode_on_every_call(self):
        source = ENGINE_PATH.read_text(encoding="utf-8")

        self.assertEqual(2, source.count("detect_state(screenshot, self._mode)"))
        self.assertEqual(0, source.count("detect_state(screenshot)"))


class ConfirmationCacheTest(unittest.TestCase):
    """detect_state already scans confirmations; the action chain then
    asks for them again on the same frame."""

    def test_scan_reuses_the_result_for_the_same_frame(self):
        body = ast.unparse(_func(SR_PATH, "scan_for_confirmations"))

        self.assertIn("_frame_signature(screenshot)", body)
        self.assertIn("_conf_cache_sig", body)
        self.assertIn("_conf_cache_val", body)

    def test_cache_is_dropped_when_assets_change(self):
        body = ast.unparse(_func(SR_PATH, "clear_cache"))

        self.assertIn("_conf_cache_sig = None", body)
        self.assertIn("_ui_scale_memo.clear()", body)


class ScaleMemoTest(unittest.TestCase):
    """A UI template matches at one scale on a given device; trying the
    remembered scale first turns four full-frame matches into one."""

    def test_match_ui_accepts_and_stores_a_memo_key(self):
        node = _func(SR_PATH, "_match_ui")
        body = ast.unparse(node)

        self.assertIn("memo_key", [a.arg for a in node.args.args])
        self.assertIn("_ui_scale_memo.get((memo_key, h))", body)
        self.assertIn("_ui_scale_memo[memo_key, h] = best_scale", body)

    def test_a_memo_miss_still_sweeps_every_scale(self):
        body = ast.unparse(_func(SR_PATH, "_match_ui"))

        # The sweep must not be inside an `else` of the fast path — a
        # stale memo would then make the template undetectable.
        self.assertIn("for scale in ui_scales:", body)
        self.assertIn("_ui_scale_memo.pop((memo_key, h), None)", body)

    def test_named_lookups_feed_the_memo(self):
        body = ast.unparse(_func(SR_PATH, "find_template_by_name"))

        self.assertIn("memo_key=template_name", body)


class RankedOnlyConfirmWaitTest(unittest.TestCase):
    """The 4 s post-attack confirm poll never paid off in Normal mode."""

    def test_the_grace_window_is_ranked_only(self):
        body = ast.unparse(_func(ENGINE_PATH, "_handle_action_chain"))

        self.assertIn("name == 'attack_button2' and self._is_ranked()", body)

    def test_ranked_check_reads_the_profile(self):
        body = ast.unparse(_func(ENGINE_PATH, "_is_ranked"))

        self.assertIn("hv_match_mode", body)
        self.assertIn("ranked", body)


if __name__ == "__main__":
    unittest.main()
