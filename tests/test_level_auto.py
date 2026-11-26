"""The daemon's automatic level trim, without a sound server."""

import unittest
from unittest import mock

from js8call_logger import engine, pulse


class LevelAutoTest(unittest.TestCase):
    def _run_watch(self, measured, volume, applied):
        eng = engine.Engine.__new__(engine.Engine)   # no Sink, no decoders
        eng.cfg = {"level_auto": True, "level_target_db": -14.0,
                   "level_max_step_db": 6.0, "level_auto_interval_s": 20,
                   "cable": "js8call-logger_cable"}
        eng.stop_flag = False
        calls = []

        ticks = []

        def fake_sleep(_seconds):
            # let the loop body run once, then stop it on the next sleep
            ticks.append(1)
            if len(ticks) > 1:
                eng.stop_flag = True

        with mock.patch.object(engine.time, "sleep", fake_sleep), \
             mock.patch.object(engine.config, "load",
                               return_value={"level_auto": True,
                                             "level_target_db": -14.0,
                                             "level_max_step_db": 6.0,
                                             "cable": "js8call-logger_cable"}), \
             mock.patch.object(pulse, "source_exists", return_value=True), \
             mock.patch.object(pulse, "capture_samples", return_value=b"\x00\x40" * 800), \
             mock.patch.object(pulse, "peak_dbfs", return_value=measured), \
             mock.patch.object(pulse, "source_volume", return_value=volume), \
             mock.patch.object(pulse, "set_source_volume",
                               side_effect=lambda n, p: calls.append((n, p)) or True):
            eng._level_watch()
        return calls

    def test_hot_signal_is_trimmed(self):
        calls = self._run_watch(-2.0, 100.0, [])
        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(calls[0][1], 50.1, places=1)

    def test_quiet_signal_is_raised(self):
        calls = self._run_watch(-30.0, 40.0, [])
        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(calls[0][1], 79.8, places=1)

    def test_in_tolerance_does_nothing(self):
        self.assertEqual(self._run_watch(-14.0, 100.0, []), [])

    def test_silence_does_nothing(self):
        self.assertEqual(self._run_watch(-120.0, 100.0, []), [])


if __name__ == "__main__":
    unittest.main()
