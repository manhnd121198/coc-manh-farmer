"""Tests for the optional random village skip.

The point of the option is that the bot sometimes passes on a village it
could take. Two things must hold: it stays completely inert when off, and
when it fires it skips a *run* of villages rather than re-rolling for each
one — a coin flip per village produces a different, and much more even,
pattern than the occasional pause it is meant to imitate.
"""

import unittest
from unittest import mock

from logic.home_village import HomeVillageLogic


def _logic(profile: dict) -> HomeVillageLogic:
    """A HomeVillageLogic with every collaborator stubbed out."""
    with mock.patch("logic.home_village.SmartV2Logic"):
        return HomeVillageLogic(profile, mock.Mock(), mock.Mock(), mock.Mock())


def _rolls(chance: int, length: int):
    """Pin both dice. The chance roll is the only one over 1..100."""
    def randint(low, high):
        return chance if (low, high) == (1, 100) else length
    return mock.patch("logic.home_village.random.randint", side_effect=randint)


def _run_length(logic) -> int:
    """Villages skipped by the run that starts on this call."""
    assert logic._random_skip_due(), "the roll was supposed to fire"
    return 1 + logic._random_skips_left


class RandomSkipOffTest(unittest.TestCase):
    def test_never_skips_when_disabled(self):
        logic = _logic({})
        self.assertFalse(any(logic._random_skip_due() for _ in range(200)))

    def test_never_skips_at_zero_chance(self):
        logic = _logic({"hv_random_skip_enabled": True, "hv_random_skip_chance": 0})
        self.assertFalse(any(logic._random_skip_due() for _ in range(200)))

    def test_turning_it_off_mid_run_drops_the_queue(self):
        """The switch has to take effect on the next village, not after the
        run it was in the middle of."""
        profile = {"hv_random_skip_enabled": True, "hv_random_skip_chance": 100,
                   "hv_random_skip_min": 5, "hv_random_skip_max": 5}
        logic = _logic(profile)
        self.assertTrue(logic._random_skip_due())
        self.assertEqual(4, logic._random_skips_left)

        profile["hv_random_skip_enabled"] = False
        self.assertFalse(logic._random_skip_due())
        self.assertEqual(0, logic._random_skips_left)


class RandomSkipRunTest(unittest.TestCase):
    BASE = {"hv_random_skip_enabled": True, "hv_random_skip_chance": 100}

    def test_a_run_is_exactly_as_long_as_the_roll(self):
        for length in (1, 2, 3):
            logic = _logic({**self.BASE, "hv_random_skip_min": 1, "hv_random_skip_max": 3})
            with _rolls(chance=1, length=length):
                self.assertEqual(length, _run_length(logic))

    def test_the_run_counts_down_and_then_stops(self):
        logic = _logic({**self.BASE, "hv_random_skip_min": 3, "hv_random_skip_max": 3})
        with _rolls(chance=1, length=3):
            self.assertTrue(logic._random_skip_due())      # village 1 of 3
        # No more rolls: the queue alone must carry the rest of the run.
        with mock.patch("logic.home_village.random.randint",
                        side_effect=AssertionError("re-rolled mid-run")):
            self.assertTrue(logic._random_skip_due())      # village 2
            self.assertTrue(logic._random_skip_due())      # village 3
        self.assertEqual(0, logic._random_skips_left)

    def test_default_range_skips_one_or_two(self):
        lengths = {_run_length(_logic(self.BASE)) for _ in range(60)}
        self.assertEqual({1, 2}, lengths)

    def test_a_backwards_range_still_makes_sense(self):
        """min > max is a config mistake, not a crash."""
        logic = _logic({**self.BASE, "hv_random_skip_min": 5, "hv_random_skip_max": 1})
        self.assertEqual(5, _run_length(logic))


class ForcedSkipInteractionTest(unittest.TestCase):
    """A base V2 walked away from must not be followed by a dice skip.

    The two skips are unrelated — one is deliberate rhythm, the other is the
    planner failing — and stacking them turns "skip a base" into "skip
    three", each paying its own search fee.
    """

    BASE = {"hv_random_skip_enabled": True, "hv_random_skip_chance": 100,
            "hv_random_skip_min": 1, "hv_random_skip_max": 1}

    def test_the_base_after_a_forced_skip_is_taken(self):
        logic = _logic(self.BASE)
        logic._forced_skip_last = True
        self.assertFalse(logic._random_skip_due(), "dice must not stack on a forced skip")

    def test_the_suppression_lasts_exactly_one_base(self):
        logic = _logic(self.BASE)
        logic._forced_skip_last = True
        self.assertFalse(logic._random_skip_due())
        self.assertTrue(logic._random_skip_due(), "back to normal on the next base")

    def test_abandoning_a_base_arms_the_suppression_and_drops_the_queue(self):
        from core.state_machine import GameState, StateMachine
        with mock.patch("logic.home_village.SmartV2Logic"):
            logic = HomeVillageLogic(self.BASE, StateMachine(), mock.Mock(), mock.Mock())
        logic._sm.transition(GameState.OPPONENT_FOUND)
        logic._engine = mock.Mock()
        logic._random_skips_left = 3          # a run was still owed
        logic._sr.find_template_by_name.return_value = (700, 900)

        with mock.patch("logic.home_village.tap"):
            logic._abandon_base(mock.Mock())

        self.assertTrue(logic._forced_skip_last)
        self.assertEqual(0, logic._random_skips_left, "the owed run is dropped")
        self.assertFalse(logic._random_skip_due(), "next base is taken")

    def test_a_chain_of_forced_skips_is_never_lengthened_by_the_dice(self):
        """V2 failing on base after base is allowed to skip each one. The
        dice must add nothing to that chain."""
        logic = _logic(self.BASE)
        for _ in range(5):
            logic._forced_skip_last = True            # V2 gave up again
            self.assertFalse(logic._random_skip_due())


class RandomSkipChanceTest(unittest.TestCase):
    def _fires_at(self, chance: int, roll: int) -> bool:
        logic = _logic({"hv_random_skip_enabled": True,
                        "hv_random_skip_chance": chance,
                        "hv_random_skip_min": 1, "hv_random_skip_max": 1})
        with _rolls(chance=roll, length=1):
            return logic._random_skip_due()

    def test_chance_is_honoured(self):
        self.assertTrue(self._fires_at(chance=30, roll=30))    # on the line
        self.assertFalse(self._fires_at(chance=30, roll=31))   # just past it

    def test_full_chance_always_fires(self):
        self.assertTrue(self._fires_at(chance=100, roll=100))


class RandomSkipWiringTest(unittest.TestCase):
    """The skip has to actually press Next and be counted, without paying
    for a loot read on a village it already decided to pass on."""

    def test_skipped_village_taps_next_and_never_reads_loot(self):
        logic = _logic({"hv_random_skip_enabled": True, "hv_random_skip_chance": 100})
        engine = mock.Mock()
        logic._engine = engine
        logic._sr.find_template_by_name.return_value = (700, 900)

        with mock.patch("logic.home_village.tap") as tap:
            logic._handle_opponent_found(mock.Mock())

        tap.assert_called_once_with(700, 900)
        engine.record_skip.assert_called_once()
        engine.record_attack.assert_not_called()
        logic._ocr.read_loot.assert_not_called()

    def test_random_skip_still_works_without_the_loot_check(self):
        """Loot filtering off + random skip on is a config in its own right:
        every village is either skipped or attacked, no OCR either way."""
        engine = mock.Mock()
        with mock.patch("logic.home_village.Settings") as settings:
            settings.return_value.get.side_effect = (
                lambda key, default=None: True if key == "skip_loot_ocr" else default
            )

            skipping = _logic({"hv_random_skip_enabled": True, "hv_random_skip_chance": 100})
            skipping._engine = engine
            skipping._sr.find_template_by_name.return_value = (700, 900)
            with mock.patch("logic.home_village.tap"):
                skipping._handle_opponent_found(mock.Mock())

            attacking = _logic({"hv_random_skip_enabled": True, "hv_random_skip_chance": 0})
            attacking._engine = engine
            with mock.patch.object(attacking, "_execute_full_attack") as attack:
                attacking._handle_opponent_found(mock.Mock())

        attack.assert_called_once()
        engine.record_skip.assert_called_once()
        engine.record_attack.assert_called_once()
        skipping._ocr.read_loot.assert_not_called()
        attacking._ocr.read_loot.assert_not_called()

    def test_a_village_that_is_not_skipped_still_gets_attacked(self):
        logic = _logic({"hv_random_skip_enabled": True, "hv_random_skip_chance": 0,
                        "min_gold": 100, "min_elixir": 100})
        engine = mock.Mock()
        logic._engine = engine
        logic._ocr.read_loot.return_value = {"gold": 999_999, "elixir": 0, "dark_elixir": 0}

        with mock.patch.object(logic, "_execute_full_attack") as attack:
            logic._handle_opponent_found(mock.Mock())

        attack.assert_called_once()
        engine.record_attack.assert_called_once()
        engine.record_skip.assert_not_called()


if __name__ == "__main__":
    unittest.main()
