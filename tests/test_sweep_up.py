"""Tests for the post-attack sweep-up.

Two halves worth pinning. The detector: a card is only judged empty when
BOTH colour channels have dropped, because a selected card is brighter and
a shadowed one is darker and either single test would flip on those. The
pass itself: it stays off unless asked for, it stops, and it never presses
a card the bar no longer shows.
"""

import unittest
from unittest import mock

try:
    import cv2  # noqa: F401
    import numpy as np
    _HAVE_CV2 = True
except ImportError:                                    # pragma: no cover
    _HAVE_CV2 = False


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class CardStateTest(unittest.TestCase):
    """Colour, not OCR — the same signal the hero-death sensor uses."""

    CONFIG = {"sweep_up": {"empty_saturation": 60, "empty_brightness": 140,
                           "card_sample_px": 10}}

    def _card(self, hue: int, saturation: int, value: int):
        """A 60x60 patch of one HSV colour, as BGR."""
        patch = np.full((60, 60, 3), (hue, saturation, value), dtype=np.uint8)
        return cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)

    def _skill(self):
        from vision.skills.card_state import CardStateSkill
        return CardStateSkill()

    def test_a_colourful_card_still_has_troops(self):
        card = self._card(hue=110, saturation=200, value=220)
        self.assertTrue(self._skill().has_troops_left(card, (30, 30), self.CONFIG))

    def test_a_grey_dim_card_is_empty(self):
        card = self._card(hue=0, saturation=10, value=70)
        self.assertFalse(self._skill().has_troops_left(card, (30, 30), self.CONFIG))

    def test_grey_but_bright_is_not_empty(self):
        """A selected card is drained of colour but lit up. Brightness alone
        keeps it out of the empty bucket."""
        card = self._card(hue=0, saturation=10, value=230)
        self.assertTrue(self._skill().has_troops_left(card, (30, 30), self.CONFIG))

    def test_colourful_but_dark_is_not_empty(self):
        """A card in shadow. Saturation alone keeps it out."""
        card = self._card(hue=110, saturation=200, value=60)
        self.assertTrue(self._skill().has_troops_left(card, (30, 30), self.CONFIG))

    def test_a_crop_off_the_edge_is_assumed_full(self):
        """Guessing 'empty' would silently drop an army; guessing 'full'
        costs one press the game ignores."""
        card = self._card(hue=0, saturation=10, value=10)
        self.assertTrue(self._skill().has_troops_left(card, (-500, -500), self.CONFIG))

    def test_thresholds_come_from_config(self):
        card = self._card(hue=0, saturation=80, value=160)
        skill = self._skill()
        self.assertTrue(skill.has_troops_left(card, (30, 30), self.CONFIG))
        loose = {"sweep_up": {"empty_saturation": 200, "empty_brightness": 200,
                              "card_sample_px": 10}}
        self.assertFalse(skill.has_troops_left(card, (30, 30), loose))


@unittest.skipUnless(_HAVE_CV2, "requires opencv + numpy")
class SweepUpPassTest(unittest.TestCase):
    DROPS = [(100, 100), (200, 200)]

    def _ctx(self, troops):
        from types import SimpleNamespace
        skills = SimpleNamespace(
            target=mock.Mock(), card=mock.Mock(), touch=mock.Mock(), ring=mock.Mock(),
        )
        return SimpleNamespace(
            config={"sweep_up": {"max_rounds": 2, "hold_ms": 4000}},
            profile={"selected_troops": troops},
            skills=skills, mode_key="hv", engine=None,
            polygon=object(), base_centroid=(500, 400), ui_cutoff=900,
            screenshot=np.zeros((1080, 1350, 3), dtype=np.uint8),
        )

    def _sweep(self, ctx, enabled=True, screenshot=None):
        from logic import v2_orchestrator as orch
        with mock.patch.object(orch, "Settings") as settings, \
             mock.patch.object(orch, "screencap",
                               return_value=np.zeros((1080, 1350, 3), dtype=np.uint8)
                               if screenshot is None else screenshot), \
             mock.patch.object(orch.V2Orchestrator, "_sweep_up_points",
                               staticmethod(lambda _ctx: self.DROPS)):
            settings.return_value.get.side_effect = (
                lambda key, default=None: enabled if key == "sweep_up_enabled" else default
            )
            # __new__ instead of a Mock: _sweep_up calls sibling static
            # methods through self, and a Mock would answer those itself —
            # _is_interrupted would come back truthy and the pass would
            # return before doing anything.
            orch.V2Orchestrator.__new__(orch.V2Orchestrator)._sweep_up(ctx)

    def test_does_nothing_when_the_switch_is_off(self):
        ctx = self._ctx(["baba"])
        self._sweep(ctx, enabled=False)
        ctx.skills.target.find_one.assert_not_called()
        ctx.skills.touch.long_press.assert_not_called()

    def test_empties_a_card_that_still_has_troops(self):
        ctx = self._ctx(["baba"])
        ctx.skills.target.find_one.return_value = (300, 1000)
        # Round 1 says there is something left, round 2 says it is gone.
        ctx.skills.card.has_troops_left.side_effect = [True, False]

        self._sweep(ctx)

        ctx.skills.touch.tap.assert_called_once()
        self.assertEqual((300, 1000), ctx.skills.touch.tap.call_args.args[:2])
        ctx.skills.touch.long_press.assert_called_once()
        self.assertIn(ctx.skills.touch.long_press.call_args.args[:2], self.DROPS)

    def test_a_card_the_bar_no_longer_shows_is_left_alone(self):
        ctx = self._ctx(["baba"])
        ctx.skills.target.find_one.return_value = None

        self._sweep(ctx)

        ctx.skills.card.has_troops_left.assert_not_called()
        ctx.skills.touch.long_press.assert_not_called()

    def test_a_card_that_always_reads_full_cannot_loop_forever(self):
        """A misread threshold must cost a couple of presses, not the whole
        battle timer."""
        ctx = self._ctx(["baba"])
        ctx.skills.target.find_one.return_value = (300, 1000)
        ctx.skills.card.has_troops_left.return_value = True

        self._sweep(ctx)

        self.assertEqual(2, ctx.skills.touch.long_press.call_count)   # max_rounds

    def test_every_selected_troop_is_checked(self):
        ctx = self._ctx(["baba", "valkyrie", "dragon"])
        ctx.skills.target.find_one.return_value = (300, 1000)
        ctx.skills.card.has_troops_left.side_effect = [True, False, True] + [False] * 3

        self._sweep(ctx)

        self.assertEqual(2, ctx.skills.touch.long_press.call_count)


class SweepUpTimerTest(unittest.TestCase):
    def test_the_deploy_countdown_restarts_after_a_sweep(self):
        """The rule stamps the countdown when it finishes. Without a restamp
        the sweep-up's seconds come out of the user's battle time."""
        from types import SimpleNamespace
        from logic.v2_orchestrator import V2Orchestrator

        hv = SimpleNamespace(_post_deploy_time=1.0)
        ctx = SimpleNamespace(engine=SimpleNamespace(_home_logic=hv))

        with mock.patch("logic.v2_orchestrator.time.time", return_value=1234.0):
            V2Orchestrator._restamp_post_deploy(ctx)

        self.assertEqual(1234.0, hv._post_deploy_time)

    def test_no_engine_is_not_a_crash(self):
        from types import SimpleNamespace
        from logic.v2_orchestrator import V2Orchestrator
        V2Orchestrator._restamp_post_deploy(SimpleNamespace(engine=None))


if __name__ == "__main__":
    unittest.main()
