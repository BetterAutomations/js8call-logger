"""Real off-air audio: a busy band must not look like slot structure."""

import os
import unittest
import wave

from js8call_logger import burst

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures", "websdr-2026-11-25T1453Z-30s.wav")


class BusyBandTest(unittest.TestCase):
    """The recording is a live 40 m pileup: many stations, no clean gap."""

    def setUp(self):
        with wave.open(FIXTURE, "rb") as w:
            self.rate = w.getframerate()
            self.data = w.readframes(w.getnframes())

    def test_ratio_is_too_low_to_trust(self):
        res = burst.analyse(self.data, t_end=0.0, rate=self.rate)
        self.assertLess(res["ratio"], 1.5,
                        f"ratio {res['ratio']} - a busy band must not score "
                        f"like an isolated signal")

    def test_still_reports_something(self):
        res = burst.analyse(self.data, t_end=0.0, rate=self.rate)
        self.assertIsNotNone(res["phase"])
        self.assertGreater(res["windows"], 100)
