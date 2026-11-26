"""The fixtures themselves: present, intact, and the right shape."""

import os
import unittest
import wave

from js8call_logger import burst

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
LIVE = os.path.join(FIX, "live-2026-11-26T1622Z-30s.wav")
BUSY = os.path.join(FIX, "websdr-2026-11-25T1453Z-30s.wav")


class FixtureTest(unittest.TestCase):
    def test_both_recordings_are_30s_of_8k_mono(self):
        for path in (LIVE, BUSY):
            self.assertTrue(os.path.exists(path), path)
            with wave.open(path, "rb") as w:
                self.assertEqual(w.getnchannels(), 1)
                self.assertEqual(w.getframerate(), 8000)
                self.assertAlmostEqual(w.getnframes() / 8000.0, 30.0, places=1)

    def test_live_fixture_is_audio_not_silence(self):
        with wave.open(LIVE, "rb") as w:
            frames = w.readframes(w.getnframes())
        self.assertGreater(max(abs(int.from_bytes(frames[i:i + 2], "little",
                                                  signed=True))
                               for i in range(0, len(frames), 400)), 1000)

    def test_live_fixture_can_be_analysed(self):
        with wave.open(LIVE, "rb") as w:
            data = w.readframes(w.getnframes())
        res = burst.analyse(data, 0.0, 8000)
        self.assertEqual(res["windows"], 234)
        self.assertIn("ratio", res)
