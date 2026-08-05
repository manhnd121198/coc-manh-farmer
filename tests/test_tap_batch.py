"""Taps are chained inside one ADB call instead of one call each.

Measured on the dev box: an ``adb shell`` round-trip is ~38 ms and the
on-device ``input`` binary ~120 ms, so the win comes from removing the
round-trip — and only if the chunking actually happens.

``core.adb_handler`` imports cv2, so the function is exec'd from source.
"""

import ast
import unittest
from pathlib import Path


ADB_PATH = Path(__file__).resolve().parents[1] / "core" / "adb_handler.py"


def _tap_batch(recorder):
    tree = ast.parse(ADB_PATH.read_text(encoding="utf-8"))
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "tap_batch"
    )
    namespace = {"_run": recorder}
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, str(ADB_PATH), "exec"), namespace)
    return namespace["tap_batch"]


class _Recorder:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.timeouts: list[int] = []

    def __call__(self, args, timeout=None):
        self.calls.append(args)
        self.timeouts.append(timeout)


class TapBatchTest(unittest.TestCase):
    def setUp(self):
        self.rec = _Recorder()
        self.tap_batch = _tap_batch(self.rec)

    def test_one_call_carries_a_whole_chunk(self):
        self.tap_batch([(1, 2), (3, 4), (5, 6)], chunk_size=6)

        self.assertEqual(1, len(self.rec.calls))
        self.assertEqual(
            "input tap 1 2; input tap 3 4; input tap 5 6",
            self.rec.calls[0][1],
        )

    def test_points_beyond_the_chunk_spill_into_more_calls(self):
        self.tap_batch([(i, i) for i in range(7)], chunk_size=3)

        self.assertEqual(3, len(self.rec.calls))
        self.assertEqual(1, self.rec.calls[-1][1].count("input tap"))

    def test_gap_becomes_an_on_device_sleep(self):
        self.tap_batch([(1, 1), (2, 2)], gap_ms=30, chunk_size=6)

        cmd = self.rec.calls[0][1]
        self.assertEqual(2, cmd.count("sleep 0.030"))

    def test_no_sleep_is_emitted_when_the_gap_is_zero(self):
        self.tap_batch([(1, 1), (2, 2)], gap_ms=0)

        self.assertNotIn("sleep", self.rec.calls[0][1])

    def test_coordinates_are_ints_in_the_command(self):
        self.tap_batch([(10.7, 20.2)])

        self.assertEqual("input tap 10 20", self.rec.calls[0][1])

    def test_empty_input_sends_nothing(self):
        self.tap_batch([])

        self.assertEqual([], self.rec.calls)

    def test_timeout_always_covers_the_sleeps_it_asked_for(self):
        # A long gap must not make the ADB call time out on itself.
        self.tap_batch([(1, 1)] * 6, gap_ms=3000, chunk_size=6)

        self.assertGreaterEqual(self.rec.timeouts[0], 6 * 3)

    def test_short_batches_keep_a_generous_floor(self):
        self.tap_batch([(1, 1)], chunk_size=6)

        self.assertEqual(15, self.rec.timeouts[0])

    def test_a_silly_chunk_size_still_sends_every_tap(self):
        self.tap_batch([(i, i) for i in range(4)], chunk_size=0)

        self.assertEqual(4, len(self.rec.calls))


if __name__ == "__main__":
    unittest.main()
