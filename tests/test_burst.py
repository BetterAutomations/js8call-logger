"""Burst-phase clock bootstrap: find the 15 s frame grid without decoding."""

import array
import math
import unittest
from unittest import mock

from js8call_logger import burst

RATE = 8000


def tone_pcm(seconds, amp=6000, freq=1000.0):
    n = int(RATE * seconds)
    a = array.array("h")
    for i in range(n):
        a.append(int(amp * math.sin(2 * math.pi * freq * i / RATE)))
    return a


def silence_pcm(seconds):
    return array.array("h", bytes(2 * int(RATE * seconds)))


def frames_audio(phase, period=15.0, length=45.0, noise=300):
    """A repeating 12.6 s tone burst starting at `phase` in capture time."""
    out = array.array("h")
    n = int(RATE * length)
    tone = tone_pcm(1.0, amp=6000)          # one second, repeated
    amp_noise = noise
    for i in range(n):
        t = i / RATE
        pos = (t - phase) % period
        if pos < burst.FRAME:
            out.append(tone[i % RATE])
        else:
            # deterministic pseudo-noise so the test is repeatable
            out.append(((i * 2654435761) % (2 * amp_noise)) - amp_noise)
    return out.tobytes()


class WrapTest(unittest.TestCase):
    def test_folds_into_half_slot(self):
        self.assertAlmostEqual(burst.wrap_phase(14.8), -0.2, places=6)
        self.assertAlmostEqual(burst.wrap_phase(15.1), 0.1, places=6)
        self.assertAlmostEqual(burst.wrap_phase(-0.1), -0.1, places=6)
        self.assertAlmostEqual(burst.wrap_phase(3.0), 3.0, places=6)


class ScoreTest(unittest.TestCase):
    def test_frame_phase_scores_higher_than_the_gap(self):
        series = [(float(t), 1.0 if (t % 15) < burst.FRAME else 0.0)
                  for t in range(0, 40)]
        inside = burst.score_phase(series, 0.0)
        outside = burst.score_phase(series, 13.0)
        self.assertGreater(inside, outside)

    def test_no_windows_returns_negative_infinity(self):
        self.assertEqual(burst.score_phase([], 0.0), float("-inf"))


class MeasureTest(unittest.TestCase):
    def _measure(self, phase_in_capture, t_end, length=45.0):
        data = frames_audio(phase_in_capture, length=length)
        with mock.patch.object(burst.pulse, "capture_samples", return_value=data):
            with mock.patch.object(burst.time, "time", return_value=t_end):
                return burst.measure(seconds=length)

    def test_recovers_a_frame_grid_that_starts_at_five(self):
        # capture covers [t_end-45, t_end]; frames start at +5 in capture time,
        # so in wall time they start at t_end - 40.  t_end = 1000 -> 960,
        # 960 % 15 = 0 -> phase 0.
        got = self._measure(phase_in_capture=5.0, t_end=1000.0)
        self.assertIsNotNone(got["phase"])
        self.assertAlmostEqual(got["phase"], 0.0, delta=0.35)

    def test_strong_signal_reports_a_structure_ratio(self):
        got = self._measure(phase_in_capture=5.0, t_end=1000.0)
        self.assertGreater(got["ratio"], 2.0)

    def test_noise_has_no_structure(self):
        import array
        n = int(RATE * 30)
        noise = array.array("h", (((i * 2654435761) % 2000) - 1000 for i in range(n)))
        with mock.patch.object(burst.pulse, "capture_samples",
                               return_value=noise.tobytes()):
            with mock.patch.object(burst.time, "time", return_value=1000.0):
                got = burst.measure(seconds=30)
        self.assertLess(got["ratio"], 1.3)

    def test_recovers_a_clock_two_seconds_out(self):
        # frames at capture +5, capture starts at 1000-45 = 955 -> wall 960 = 0.
        # Shift the wall clock by 2 s: t_end = 1002 -> wall phase +2.
        got = self._measure(phase_in_capture=5.0, t_end=1002.0)
        self.assertAlmostEqual(got["phase"], 2.0, delta=0.35)
        self.assertAlmostEqual(got["correction"], -2.0, delta=0.35)


if __name__ == "__main__":
    unittest.main()
