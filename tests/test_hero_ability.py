"""Test cho thời điểm kích kỹ năng hero.

Khoá hai thứ. Một: khoảng chờ là một dải, roll lại mỗi trận — chờ cố định
nghĩa là trận nào cũng kích đúng một nhịp, đúng cái dấu vết mà con bot này
phải tránh. Hai: cũng chính nút đó tắt hẳn việc bấm, và khi đã tắt thì
phải tắt trên CẢ BA đường có bấm kỹ năng (luật V2, fallback tap-only của
V2, và bộ V36 cũ). Một cấu hình chỉ ăn ở một số trận còn tệ hơn là không
có cấu hình.
"""

import json
import pathlib
import unittest
from unittest import mock

from logic.skills.hero_planner import HeroPlannerSkill

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_RULES = _ROOT / "config" / "v2_attack_rules.json"


def _cfg(value):
    return {"hero_ability": {"trigger_after_engagement_sec": value}}


class AbilityDelayBandTest(unittest.TestCase):
    def test_a_band_stays_inside_its_bounds(self):
        for _ in range(200):
            got = HeroPlannerSkill.ability_delay_seconds(_cfg([3.0, 5.0]))
            self.assertGreaterEqual(got, 3.0)
            self.assertLessEqual(got, 5.0)

    def test_a_band_is_re_rolled_every_call(self):
        rolls = {HeroPlannerSkill.ability_delay_seconds(_cfg([3.0, 5.0]))
                 for _ in range(50)}
        self.assertGreater(len(rolls), 1, "the delay never changed")

    def test_a_bare_number_is_still_a_fixed_delay(self):
        # Config viết từ trước khi có dải phải chạy y nguyên.
        rolls = {HeroPlannerSkill.ability_delay_seconds(_cfg(2.0))
                 for _ in range(20)}
        self.assertEqual(rolls, {2.0})

    def test_a_backwards_band_is_read_the_right_way_round(self):
        for _ in range(50):
            got = HeroPlannerSkill.ability_delay_seconds(_cfg([5.0, 3.0]))
            self.assertGreaterEqual(got, 3.0)
            self.assertLessEqual(got, 5.0)

    def test_the_default_band_is_three_to_five_seconds(self):
        self.assertEqual(HeroPlannerSkill.DEFAULT_ABILITY_BAND, (3.0, 5.0))
        for _ in range(100):
            got = HeroPlannerSkill.ability_delay_seconds({})
            self.assertGreaterEqual(got, 3.0)
            self.assertLessEqual(got, 5.0)

    def test_garbage_falls_back_to_the_default_band(self):
        for junk in ({}, [], ["x", "y"], {"hero_ability": []}):
            config = junk if isinstance(junk, dict) else _cfg(junk)
            got = HeroPlannerSkill.ability_delay_seconds(config)
            self.assertGreaterEqual(got, 3.0)
            self.assertLessEqual(got, 5.0)

    def test_a_caller_fallback_only_applies_without_a_band(self):
        self.assertEqual(
            HeroPlannerSkill.ability_delay_seconds({}, fallback=9.0), 9.0,
        )
        got = HeroPlannerSkill.ability_delay_seconds(_cfg([3.0, 5.0]), fallback=9.0)
        self.assertLessEqual(got, 5.0)

    def test_auto_still_returns_a_wait(self):
        # Chờ ở đây không chỉ để bấm kỹ năng: spell thả sau đó cần quân
        # đã giao chiến, nên không được tụt về 0.
        got = HeroPlannerSkill.ability_delay_seconds(_cfg("auto"))
        self.assertGreaterEqual(got, 3.0)


class AbilityEnabledTest(unittest.TestCase):
    def test_a_band_means_the_bot_taps(self):
        self.assertTrue(HeroPlannerSkill.ability_enabled(_cfg([3.0, 5.0])))

    def test_a_number_means_the_bot_taps(self):
        self.assertTrue(HeroPlannerSkill.ability_enabled(_cfg(2.0)))

    def test_a_missing_block_means_the_bot_taps(self):
        self.assertTrue(HeroPlannerSkill.ability_enabled({}))
        self.assertTrue(HeroPlannerSkill.ability_enabled(None))

    def test_auto_and_its_synonyms_hand_it_to_the_game(self):
        for word in ("auto", "AUTO", " off ", "none", "never", "khong", "không"):
            self.assertFalse(
                HeroPlannerSkill.ability_enabled(_cfg(word)),
                f"{word!r} should stop the bot tapping",
            )

    def test_null_hands_it_to_the_game(self):
        self.assertFalse(HeroPlannerSkill.ability_enabled(_cfg(None)))

    def test_there_is_no_second_on_off_switch(self):
        # Cố tình chỉ một nút: thêm một cờ boolean thì nó mâu thuẫn được
        # với delay ("tắt, mà kích sau 4s"), và cũng chẳng diễn tả thêm
        # được gì so với "auto".
        source = (_ROOT / "logic" / "skills" / "hero_planner.py").read_text(
            encoding="utf-8",
        )
        self.assertNotIn("use_ability", source)


class ShippedConfigTest(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads(_RULES.read_text(encoding="utf-8"))

    def test_the_shipped_config_asks_for_three_to_five_seconds(self):
        band = self.rules["hero_ability"]["trigger_after_engagement_sec"]
        self.assertEqual([float(band[0]), float(band[-1])], [3.0, 5.0])

    def test_the_shipped_config_taps_the_ability(self):
        self.assertTrue(HeroPlannerSkill.ability_enabled(self.rules))

    def test_the_double_tap_gap_survives(self):
        self.assertEqual(
            HeroPlannerSkill.ability_double_tap_gap_ms(self.rules),
            self.rules["hero_ability"]["double_tap_gap_ms"],
        )


class EveryPathHonoursAutoTest(unittest.TestCase):
    """Cả ba chỗ double-tap thẻ hero đều phải hỏi trước."""

    def test_the_v2_rules_skip_the_double_tap(self):
        from logic.rules.air_attack_rule import AirAttackRule

        ctx = mock.Mock()
        ctx.config = _cfg("auto")
        ctx.skills.hero = HeroPlannerSkill()
        rule = AirAttackRule.__new__(AirAttackRule)
        rule._fire_hero_abilities(ctx, [("king", (10, 20), (30, 40))])
        ctx.skills.touch.double_tap.assert_not_called()

    def test_the_v2_rules_do_double_tap_when_a_band_is_set(self):
        from logic.rules.air_attack_rule import AirAttackRule

        ctx = mock.Mock()
        ctx.config = _cfg([3.0, 5.0])
        ctx.skills.hero = HeroPlannerSkill()
        rule = AirAttackRule.__new__(AirAttackRule)
        rule._interrupted = lambda _ctx: False
        rule._fire_hero_abilities(ctx, [("king", (10, 20), (30, 40))])
        ctx.skills.touch.double_tap.assert_called_once()

    def test_the_two_tap_only_paths_ask_before_tapping(self):
        # Hai chỗ này double-tap thẳng trong hàm chứ không qua skill, nên
        # kiểm bằng cách đọc source.
        for name in ("logic/smart_v2_logic.py", "logic/home_village.py"):
            source = (_ROOT / name).read_text(encoding="utf-8")
            self.assertIn(
                "hero_ability_enabled()", source,
                f"{name} fires abilities without asking whether it should",
            )
            self.assertIn(
                "hero_ability_delay()", source,
                f"{name} does not take its wait from the shared band",
            )

    def test_the_settings_spin_box_is_read_in_exactly_one_place(self):
        # Chỉ được đọc làm dự phòng trong SmartV2Logic.hero_ability_delay()
        # và không chỗ nào khác — thêm một chỗ đọc nghĩa là có đường bỏ qua
        # dải và quay về chờ cố định.
        for name in ("logic/smart_v2_logic.py", "logic/home_village.py"):
            source = (_ROOT / name).read_text(encoding="utf-8")
            expected = 1 if name.endswith("smart_v2_logic.py") else 0
            self.assertEqual(
                source.count('"hero_ability_delay"'), expected, name,
            )


if __name__ == "__main__":
    unittest.main()
