"""Tests for the Builder Base farm rule: one troop, then surrender.

What matters: it never spends more than one unit, uses the surrender
button as proof the drop landed and moves on to another point when it did
not, remembers the point and confirm spot that worked, never taps blind,
never needs the red-zone polygon, and stops the bot at the battle limit.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

try:
    import numpy as np
    _HAVE_NP = True
except ImportError:                                    # pragma: no cover
    _HAVE_NP = False

CARD = (50, 700)
LINE = [(1250, 400), (600, 300), (500, 350)]
SURRENDER = (106, 824)
CONFIRM = (839, 661)


def _screen():
    return np.zeros((1080, 1350, 3), dtype=np.uint8)


@unittest.skipUnless(_HAVE_NP, "requires numpy")
class BBSurrenderRuleTest(unittest.TestCase):
    def setUp(self):
        from logic.rules.bb_surrender_rule import BBSurrenderRule
        self.rule = BBSurrenderRule()
        self.landing = {(1250, 400)}
        self.visible: dict = {}
        self.limit = 0

    def _ctx(self, troops=("witch_bb",), card=CARD):
        skills = SimpleNamespace(target=mock.Mock(), touch=mock.Mock())
        skills.target.find_one.side_effect = (
            lambda _s, key: card if key in troops else self.visible.get(key)
        )
        skills.target.find_first_of.side_effect = (
            lambda _s, keys: next(((k, *self.visible[k]) for k in keys if k in self.visible), None)
        )

        def on_tap(x, y, _cfg):
            if (x, y) in self.landing:
                self.visible = {"surrender_button": SURRENDER}
            elif (x, y) == SURRENDER:
                self.visible = {"end_battle_confirm": CONFIRM}
            elif (x, y) == CONFIRM:
                self.visible = {}

        skills.touch.tap.side_effect = on_tap
        engine = SimpleNamespace(_running=True, _paused=False,
                                 _home_logic=mock.Mock(), stop_bot=mock.Mock())
        return SimpleNamespace(
            screenshot=_screen(), profile={"bb_selected_troops": list(troops)},
            config={"bb_surrender": {}}, skills=skills, mode_key="bb",
            ui_cutoff=950, engine=engine,
        )

    def _run(self, ctx):
        self.visible = {}
        with mock.patch("logic.rules.bb_surrender_rule.screencap", return_value=_screen()), \
             mock.patch("logic.rules.bb_surrender_rule.time.sleep"), \
             mock.patch("logic.rules.bb_surrender_rule.Settings") as settings, \
             mock.patch("logic.rules.bb_surrender_rule.ScreenReader.get_focused_deployment_line",
                        return_value=(LINE, (0, 0))):
            settings.return_value.get.side_effect = (
                lambda k, d=None: self.limit if k == "bb_surrender_max_battles" else d
            )
            return self.rule.execute(ctx)

    @staticmethod
    def _taps(ctx):
        return [c.args[:2] for c in ctx.skills.touch.tap.call_args_list]

    def test_a_drop_that_did_not_land_moves_on_to_the_next_point(self):
        ctx = self._ctx()
        self.assertTrue(self._run(ctx))
        self.assertEqual([CARD, (600, 300), (1250, 400), SURRENDER, CONFIRM], self._taps(ctx))
        ctx.skills.touch.long_press.assert_not_called()
        self.assertEqual((1250, 400), self.rule._good_drop)
        self.assertEqual(CONFIRM, self.rule._confirm_xy)

    def test_next_battle_tries_the_point_that_worked_first(self):
        ctx = self._ctx()
        self._run(ctx)
        ctx.skills.touch.tap.reset_mock()
        self._run(ctx)
        self.assertEqual([CARD, (1250, 400), SURRENDER, CONFIRM], self._taps(ctx))

    def test_nothing_lands_means_no_surrender_and_no_blind_taps(self):
        self.landing = set()
        ctx = self._ctx()
        self.assertTrue(self._run(ctx), "False would chain into a full attack")
        self.assertNotIn(SURRENDER, self._taps(ctx))
        self.assertNotIn(CONFIRM, self._taps(ctx))
        ctx.engine._home_logic._end_battle.assert_not_called()

    def test_missing_card_taps_nothing(self):
        ctx = self._ctx(card=None)
        self.assertTrue(self._run(ctx))
        self.assertEqual([], self._taps(ctx))

    def test_stops_the_bot_when_the_battle_limit_is_reached(self):
        self.limit = 2
        ctx = self._ctx()
        self._run(ctx)
        ctx.engine.stop_bot.assert_not_called()
        self._run(ctx)
        ctx.engine.stop_bot.assert_called_once()

    def test_zero_limit_never_stops(self):
        ctx = self._ctx()
        for _ in range(5):
            self._run(ctx)
        ctx.engine.stop_bot.assert_not_called()

    def test_does_not_need_the_red_zone_polygon(self):
        self.assertFalse(self.rule.needs_polygon)


@unittest.skipUnless(_HAVE_NP, "requires numpy")
class OrchestratorSkipsPolygonTest(unittest.TestCase):
    def test_manual_bb_surrender_runs_without_polygon_detection(self):
        from logic.v2_orchestrator import V2Orchestrator
        from logic.rules.bb_surrender_rule import BBSurrenderRule
        orch = V2Orchestrator(mock.Mock())
        orch._skills.red_zone = mock.Mock()
        settings = {"v2_rule_bb": "bb_surrender", "v2_decoration_wait": 0}
        with mock.patch("logic.v2_orchestrator.Settings") as s, \
             mock.patch.object(BBSurrenderRule, "execute", return_value=True) as run:
            s.return_value.get.side_effect = lambda k, d=None: settings.get(k, d)
            ok = orch.execute(_screen(), {}, "bb", None)
        self.assertTrue(ok)
        run.assert_called_once()
        orch._skills.red_zone.detect.assert_not_called()
        self.assertIsNone(run.call_args.args[0].polygon)


if __name__ == "__main__":
    unittest.main()
