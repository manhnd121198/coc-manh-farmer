"""Tests for "skip the base instead of falling back to the legacy planner".

The option only matters when V2 gives up, and the two things that can go
wrong there are opposites: falling back when the user asked not to, and
skipping forever when the reason V2 gives up is a setting rather than the
base in front of it.
"""

import unittest
from unittest import mock

from logic.smart_v2_logic import SmartV2Logic


def _v2(skip_on_fallback: bool, orchestrator_succeeds: bool, limit: int = 3):
    with mock.patch("logic.smart_v2_logic.SmartVisionV2"), \
         mock.patch("logic.smart_v2_logic.V2Orchestrator") as orch:
        logic = SmartV2Logic({}, mock.Mock(), mock.Mock(), mode_key="hv")
    logic._orchestrator.execute.return_value = orchestrator_succeeds
    logic._orchestrator.attack_rules.return_value = {
        "fallback": {"max_consecutive_skips": limit},
    }
    settings = mock.patch("logic.smart_v2_logic.Settings")
    return logic, settings, skip_on_fallback


def _run(logic, settings, skip_on_fallback, times: int = 1):
    """Returns the list of execute() results over `times` bases."""
    out = []
    with settings as s, mock.patch.object(logic, "_legacy_run") as legacy:
        s.return_value.get.side_effect = (
            lambda key, default=None:
            skip_on_fallback if key == "v2_skip_on_fallback" else (default or "smart")
        )
        for _ in range(times):
            out.append(logic.execute(mock.Mock()))
    return out, legacy


class FallbackBehaviourTest(unittest.TestCase):
    def test_a_working_rule_never_touches_the_fallback(self):
        logic, settings, skip = _v2(skip_on_fallback=True, orchestrator_succeeds=True)
        results, legacy = _run(logic, settings, skip)
        self.assertEqual([True], results)
        legacy.assert_not_called()

    def test_off_by_default_means_the_legacy_planner_attacks(self):
        logic, settings, skip = _v2(skip_on_fallback=False, orchestrator_succeeds=False)
        results, legacy = _run(logic, settings, skip)
        self.assertEqual([True], results, "caller must not walk away")
        legacy.assert_called_once()

    def test_on_means_walk_away_without_deploying(self):
        logic, settings, skip = _v2(skip_on_fallback=True, orchestrator_succeeds=False)
        results, legacy = _run(logic, settings, skip)
        self.assertEqual([False], results, "caller must walk away")
        legacy.assert_not_called()

    def test_a_crash_counts_as_giving_up(self):
        logic, settings, skip = _v2(skip_on_fallback=True, orchestrator_succeeds=False)
        logic._orchestrator.execute.side_effect = RuntimeError("boom")
        results, legacy = _run(logic, settings, skip)
        self.assertEqual([False], results)
        legacy.assert_not_called()


class SkipCapTest(unittest.TestCase):
    """A polygon threshold that no longer fits the device fails on every
    base alike. Skipping each one forever pays a search fee per base and
    never attacks, so the run has to break out on its own."""

    def test_the_run_of_skips_is_capped(self):
        logic, settings, skip = _v2(skip_on_fallback=True, orchestrator_succeeds=False, limit=3)
        results, legacy = _run(logic, settings, skip, times=5)

        # Three walked away from, then the fourth is attacked anyway, and
        # the counter starts over.
        self.assertEqual([False, False, False, True, False], results)
        legacy.assert_called_once()

    def test_a_successful_attack_clears_the_run(self):
        logic, settings, skip = _v2(skip_on_fallback=True, orchestrator_succeeds=False, limit=2)
        _run(logic, settings, skip, times=2)
        self.assertEqual(2, logic._skipped_in_a_row)

        logic._orchestrator.execute.return_value = True
        _run(logic, settings, skip)
        self.assertEqual(0, logic._skipped_in_a_row)


class AbandonBaseTest(unittest.TestCase):
    """Where the bot is when V2 gives up decides how it leaves — and the
    two exits cost very different amounts."""

    def _logic(self, state):
        from logic.home_village import HomeVillageLogic
        from core.state_machine import StateMachine
        with mock.patch("logic.home_village.SmartV2Logic"):
            logic = HomeVillageLogic({}, StateMachine(), mock.Mock(), mock.Mock())
        logic._sm.transition(state)
        logic._engine = mock.Mock()
        logic._attack_active = True
        return logic

    def test_on_the_scouting_screen_it_takes_next(self):
        from core.state_machine import GameState
        logic = self._logic(GameState.OPPONENT_FOUND)
        logic._sr.find_template_by_name.return_value = (700, 900)

        with mock.patch("logic.home_village.tap") as tap, \
             mock.patch.object(logic, "_end_battle") as surrender:
            logic._abandon_base(mock.Mock())

        tap.assert_called_once_with(700, 900)
        surrender.assert_not_called()
        self.assertFalse(logic._attack_active)

    def test_inside_a_battle_it_surrenders(self):
        from core.state_machine import GameState
        logic = self._logic(GameState.IN_BATTLE)

        with mock.patch.object(logic, "_end_battle") as surrender, \
             mock.patch.object(logic, "_tap_next") as nxt:
            logic._abandon_base(mock.Mock())

        surrender.assert_called_once()
        nxt.assert_not_called()
        self.assertFalse(logic._attack_active)

    def test_the_abandoned_base_moves_from_attacks_to_skips(self):
        from core.state_machine import GameState
        logic = self._logic(GameState.OPPONENT_FOUND)
        logic._sr.find_template_by_name.return_value = None

        with mock.patch("logic.home_village.tap"):
            logic._abandon_base(mock.Mock())

        logic._engine.record_attack_cancelled.assert_called_once()


class TallyTest(unittest.TestCase):
    def test_cancelling_moves_the_count_across(self):
        from core.bot_engine import BotEngine
        engine = BotEngine.__new__(BotEngine)
        engine._attack_count, engine._skip_count = 3, 1
        engine.stats_changed = mock.Mock()

        BotEngine.record_attack_cancelled(engine)

        self.assertEqual((2, 2), (engine._attack_count, engine._skip_count))

    def test_it_cannot_go_negative(self):
        from core.bot_engine import BotEngine
        engine = BotEngine.__new__(BotEngine)
        engine._attack_count, engine._skip_count = 0, 0
        engine.stats_changed = mock.Mock()

        BotEngine.record_attack_cancelled(engine)

        self.assertEqual((0, 1), (engine._attack_count, engine._skip_count))


if __name__ == "__main__":
    unittest.main()
