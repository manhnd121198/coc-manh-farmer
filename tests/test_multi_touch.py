"""Tests for the root-only multi-finger gesture.

Two things must hold. It has to stay OFF and out of the way unless both
the switch and root are present — a half-working gesture would leave
fingers stuck down and turn every later action into one phantom drag. And
the event stream has to be a valid MT protocol B sequence, because the
driver silently ignores a malformed one: no error, no touch, no troops.
"""

import unittest
from unittest import mock

from core import multi_touch

CFG = {
    "multi_touch": {
        "event_device": "/dev/input/event9",
        "raw_max": 4095,
        "swap_xy": False,
        "invert_x": False,
        "invert_y": False,
    },
}
SCREEN = (1350, 1080)


def _cfg(**over):
    block = dict(CFG["multi_touch"])
    block.update(over)
    return {"multi_touch": block}


def _reset_probes():
    multi_touch._root_prefix = None
    multi_touch._probed_root = False
    multi_touch._detected = None


class MultiTouchAvailabilityTest(unittest.TestCase):
    def setUp(self):
        _reset_probes()
        self.addCleanup(_reset_probes)

    def _available(self, switch: bool, root: bool) -> bool:
        with mock.patch.object(multi_touch, "enabled", lambda: switch), \
             mock.patch.object(multi_touch, "have_root", lambda *a, **k: root):
            return multi_touch.available(CFG)

    def test_off_unless_switched_on(self):
        self.assertFalse(self._available(False, True))

    def test_off_without_root(self):
        """SELinux blocks the touchscreen node for the shell user, so an
        un-rooted device must fall back rather than fail silently."""
        self.assertFalse(self._available(True, False))

    def test_on_with_both(self):
        self.assertTrue(self._available(True, True))

    def test_root_probe_needs_uid_zero(self):
        for out, expected in ((b"0\n", True), (b"2000\n", False), (b"", False)):
            _reset_probes()
            done = mock.Mock(stdout=out, stderr=b"", returncode=0)
            with mock.patch.object(multi_touch, "_run", return_value=done):
                self.assertEqual(expected, multi_touch.have_root(refresh=True))

    def test_an_already_root_shell_skips_su(self):
        """Emulators hand out a root shell; wrapping in su there is at
        best pointless and often missing entirely."""
        _reset_probes()
        done = mock.Mock(stdout=b"0\n", stderr=b"", returncode=0)
        with mock.patch.object(multi_touch, "_run", return_value=done) as run:
            self.assertTrue(multi_touch.have_root(refresh=True))
        self.assertEqual(["shell", "id -u"], run.call_args_list[0].args[0])
        self.assertEqual([], multi_touch._root_prefix)

    def test_falls_back_to_su_when_the_shell_is_not_root(self):
        _reset_probes()
        outs = [mock.Mock(stdout=b"2000\n", stderr=b"", returncode=0),
                mock.Mock(stdout=b"0\n", stderr=b"", returncode=0)]
        with mock.patch.object(multi_touch, "_run", side_effect=outs):
            self.assertTrue(multi_touch.have_root(refresh=True))
        self.assertEqual(["su", "-c"], multi_touch._root_prefix)

    def test_root_probe_survives_a_dead_adb(self):
        _reset_probes()
        with mock.patch.object(multi_touch, "_run", side_effect=OSError("no device")):
            self.assertFalse(multi_touch.have_root(refresh=True))


class CoordinateMappingTest(unittest.TestCase):
    def test_identity_mapping_spans_the_whole_grid(self):
        cfg = multi_touch._cfg(_cfg())
        self.assertEqual((0, 0), multi_touch.to_raw(0, 0, cfg, SCREEN))
        self.assertEqual((4095, 4095), multi_touch.to_raw(1349, 1079, cfg, SCREEN))

    def test_swap_exchanges_the_axes(self):
        cfg = multi_touch._cfg(_cfg(swap_xy=True))
        self.assertEqual((4095, 0), multi_touch.to_raw(0, 1079, cfg, SCREEN))

    def test_inversion_flips_each_axis(self):
        cfg = multi_touch._cfg(_cfg(invert_x=True, invert_y=True))
        self.assertEqual((4095, 4095), multi_touch.to_raw(0, 0, cfg, SCREEN))

    def test_out_of_range_points_are_clamped_not_wrapped(self):
        """A raw value past the driver's max is rejected by the driver, so
        an off-screen point must land at the edge instead of vanishing."""
        cfg = multi_touch._cfg(_cfg())
        self.assertEqual((0, 0), multi_touch.to_raw(-500, -500, cfg, SCREEN))
        self.assertEqual((4095, 4095), multi_touch.to_raw(9999, 9999, cfg, SCREEN))


class TouchDeviceDetectionTest(unittest.TestCase):
    """The event node number is not portable — event9 on the reference
    phone, something else on an emulator. Writing to the wrong node is
    accepted without error and simply does nothing."""

    GETEVENT = b"""add device 1: /dev/input/event0
  name:     "gpio-keys"
    KEY (0001): 0074
add device 3: /dev/input/event7
  name:     "touchscreen"
    ABS (0003):
      ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0
      ABS_MT_POSITION_X     : value 0, min 0, max 32767, fuzz 0, flat 0
      ABS_MT_POSITION_Y     : value 0, min 0, max 32767, fuzz 0, flat 0
"""

    def setUp(self):
        _reset_probes()
        self.addCleanup(_reset_probes)

    def test_picks_the_node_that_reports_touch_coordinates(self):
        cfg = multi_touch._cfg({"multi_touch": {"event_device": "auto"}})
        done = mock.Mock(stdout=self.GETEVENT, stderr=b"", returncode=0)
        with mock.patch.object(multi_touch, "_run", return_value=done):
            self.assertEqual(("/dev/input/event7", 32767),
                             multi_touch.touch_device(cfg, refresh=True))

    def test_a_pinned_device_is_used_as_given(self):
        cfg = multi_touch._cfg({"multi_touch": {
            "event_device": "/dev/input/event9", "raw_max": 4095}})
        with mock.patch.object(multi_touch, "_run") as run:
            self.assertEqual(("/dev/input/event9", 4095),
                             multi_touch.touch_device(cfg))
        run.assert_not_called()

    def test_no_touch_node_is_reported_not_guessed(self):
        cfg = multi_touch._cfg({"multi_touch": {"event_device": "auto"}})
        done = mock.Mock(stdout=b"add device 1: /dev/input/event0\n",
                         stderr=b"", returncode=0)
        with mock.patch.object(multi_touch, "_run", return_value=done):
            self.assertIsNone(multi_touch.touch_device(cfg, refresh=True))


class EventSequenceTest(unittest.TestCase):
    def setUp(self):
        _reset_probes()
        multi_touch._probed_root = True
        multi_touch._root_prefix = []
        self.addCleanup(_reset_probes)
        self.done = mock.Mock(stdout=b"", stderr=b"", returncode=0)

    def _script(self, points, duration=1000):
        with mock.patch.object(multi_touch, "enabled", lambda: True), \
             mock.patch.object(multi_touch, "_run", return_value=self.done) as run, \
             mock.patch.object(multi_touch.time, "sleep"):
            ok = multi_touch.hold_all(points, duration, CFG, SCREEN)
        self.assertTrue(ok)
        self.assertEqual(1, run.call_count, "the gesture must be one su call")
        return run.call_args.args[0][-1]

    def test_every_finger_gets_its_own_slot_and_tracking_id(self):
        script = self._script([(100, 100), (200, 200), (300, 300), (400, 400)])
        for slot in range(4):
            self.assertIn(f"3 47 {slot};", script + ";")
            self.assertIn(f"3 57 {100 + slot}", script)

    def test_all_fingers_are_pressed_before_the_sync(self):
        """One SYN_REPORT after all four is what makes them simultaneous;
        syncing per finger would be four taps in a row instead."""
        script = self._script([(100, 100), (200, 200), (300, 300), (400, 400)])
        press, _sep, rest = script.partition("sleep")
        self.assertEqual(1, press.count("0 0 0"), "one sync for the whole press")
        self.assertEqual(4, press.count("3 57 1"), "all four ids before it")
        self.assertIn("1 330 1", press, "BTN_TOUCH must go down")
        self.assertIn("3 57 -1", rest, "every finger must be lifted")

    def test_hold_time_is_inside_the_same_shell_call(self):
        """If the sleep travelled separately, a slow round-trip would leave
        fingers down and the game would read one endless drag."""
        script = self._script([(100, 100)], duration=2500)
        self.assertIn("sleep 2.50", script)

    def test_a_failed_gesture_still_lifts_the_fingers(self):
        calls = []

        def flaky(args, timeout=15):
            calls.append(args[-1])
            if len(calls) == 1:
                raise RuntimeError("su denied")
            return mock.Mock(stdout=b"", stderr=b"", returncode=0)

        with mock.patch.object(multi_touch, "enabled", lambda: True), \
             mock.patch.object(multi_touch, "_run", side_effect=flaky):
            self.assertFalse(multi_touch.hold_all([(10, 10), (20, 20)], 500, CFG, SCREEN))
        self.assertEqual(2, len(calls), "a cleanup lift must follow the failure")
        self.assertIn("3 57 -1", calls[1])

    def test_more_fingers_than_slots_are_trimmed(self):
        script = self._script([(i * 10, i * 10) for i in range(1, 15)])
        self.assertIn(f"3 47 {multi_touch.MAX_SLOTS - 1}", script)
        self.assertNotIn(f"3 47 {multi_touch.MAX_SLOTS}", script)

    def test_nothing_runs_for_an_empty_point_list(self):
        with mock.patch.object(multi_touch, "_run") as run:
            self.assertFalse(multi_touch.hold_all([], 1000, CFG, SCREEN))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
