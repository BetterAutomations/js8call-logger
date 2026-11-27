"""Input-level trimming: the arithmetic, and the refusals that matter."""

import unittest

from js8call_logger import pulse


class TrimTest(unittest.TestCase):
    def test_too_hot_lowers_toward_target(self):
        plan = pulse.trim_volume(-2.0, 100.0, target_db=-14.0)
        self.assertEqual(plan["action"], "lower")
        # 12 dB over target, capped at a 6 dB step: 100 * 10^(-6/20) = 50.1%
        self.assertAlmostEqual(plan["percent"], 50.1, places=1)
        self.assertEqual(plan["delta_db"], -12.0)

    def test_too_quiet_raises_toward_target(self):
        plan = pulse.trim_volume(-30.0, 40.0, target_db=-14.0)
        self.assertEqual(plan["action"], "raise")
        # 16 dB under target, capped at 6 dB: 40 * 10^(6/20) = 79.8%
        self.assertAlmostEqual(plan["percent"], 79.8, places=1)

    def test_within_tolerance_does_nothing(self):
        plan = pulse.trim_volume(-14.0, 100.0, target_db=-14.0)
        self.assertEqual(plan["action"], "none")
        self.assertEqual(plan["reason"], "within tolerance")
        self.assertEqual(plan["percent"], 100.0)

    def test_deadband_absorbs_small_errors(self):
        plan = pulse.trim_volume(-15.5, 80.0, target_db=-14.0)
        self.assertEqual(plan["action"], "none")

    def test_digital_silence_is_never_amplified(self):
        plan = pulse.trim_volume(-120.0, 50.0, target_db=-14.0)
        self.assertEqual(plan["action"], "none")
        self.assertEqual(plan["percent"], 50.0)
        self.assertIn("silence", plan["reason"])

    def test_never_exceeds_unity(self):
        plan = pulse.trim_volume(-60.0, 100.0, target_db=-14.0)
        self.assertEqual(plan["action"], "none")
        self.assertIn("already at 100%", plan["reason"])

    def test_full_volume_hint_comes_from_the_caller(self):
        # An rtl_fm feed has no source application to turn up; the knob is the
        # tuner gain, so the caller that knows the source names it.
        plan = pulse.trim_volume(-60.0, 100.0, target_db=-14.0,
                                 at_full_hint="raise rtl_gain")
        self.assertEqual(plan["action"], "none")
        self.assertIn("already at 100% - raise rtl_gain", plan["reason"])
        self.assertNotIn("source application", plan["reason"])

    def test_never_goes_below_zero(self):
        plan = pulse.trim_volume(0.0, 1.0, target_db=-40.0)
        self.assertEqual(plan["action"], "none")
        self.assertIn("already at 0%", plan["reason"])

    def test_unknown_inputs_are_refused(self):
        for measured, vol in ((None, 100.0), (-14.0, None)):
            plan = pulse.trim_volume(measured, vol, target_db=-14.0)
            self.assertEqual(plan["action"], "none")
            self.assertIsNone(plan["percent"])

    def test_step_is_capped(self):
        plan = pulse.trim_volume(-2.0, 100.0, target_db=-14.0, max_step_db=3.0)
        # 3 dB down from 100% is 70.8%, not 50%
        self.assertAlmostEqual(plan["percent"], 70.8, places=1)

    def test_repeated_calls_converge(self):
        # A 12 dB overshoot needs two capped steps, not one huge one. Model the
        # volume as scaling the measured level, which is what PipeWire does.
        import math
        db0, vol, db = -2.0, 100.0, -2.0
        steps = 0
        for _ in range(6):
            plan = pulse.trim_volume(db, vol, target_db=-14.0)
            if plan["action"] == "none":
                break
            vol, steps = plan["percent"], steps + 1
            db = db0 + 20 * math.log10(vol / 100.0)
        self.assertLessEqual(steps, 3)
        self.assertLess(abs(db - (-14.0)), 2.0)


class VerdictTest(unittest.TestCase):
    """A quiet feed means two different things, and only one is fixable."""

    def test_quiet_cable_is_a_volume_problem(self):
        self.assertEqual(pulse.level_verdict(-30.0), "too quiet")

    def test_quiet_sdr_is_an_empty_band(self):
        # rtl_fm has no volume: a low peak is nothing in the passband.
        self.assertEqual(pulse.level_verdict(-30.0, sdr=True),
                         "no signal in the passband")

    def test_loud_is_clipping_on_both(self):
        for sdr in (False, True):
            self.assertEqual(pulse.level_verdict(-3.0, sdr=sdr), "clipping")

    def test_in_between_is_ok(self):
        for sdr in (False, True):
            self.assertEqual(pulse.level_verdict(-15.0, sdr=sdr), "ok")

    def test_thresholds_are_the_ones_documented(self):
        self.assertEqual(pulse.level_verdict(-20.0), "ok")
        self.assertEqual(pulse.level_verdict(-20.1, sdr=True),
                         "no signal in the passband")
        self.assertEqual(pulse.level_verdict(-10.0), "ok")
        self.assertEqual(pulse.level_verdict(-9.9), "clipping")


class VolumeParseTest(unittest.TestCase):
    def test_parses_front_left(self):
        text = ("Volume: front-left: 65536 / 100% / 0.00 dB,   "
                "front-right: 65536 / 100% / 0.00 dB\n        balance 0.00\n")
        import re
        m = re.search(r"front-left: \d+ / (\d+)%", text)
        self.assertEqual(m.group(1), "100")

    def test_falls_back_to_any_percentage(self):
        text = "Volume: mono: 32768 /  50% / -6.00 dB\n"
        import re
        m = re.search(r"front-left: \d+ / (\d+)%", text)
        self.assertIsNone(m)
        m = re.search(r"/ +(\d+)%", text)
        self.assertEqual(m.group(1), "50")


if __name__ == "__main__":
    unittest.main()
