"""Tests for the decode-depth tuner (pure parts; no audio, no decoder)."""

import array
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from js8call_logger import tune


class ProfileEnvTest(unittest.TestCase):
    def test_all_on_means_no_variables(self):
        env = tune.profile_env({name: True for name in tune.PASSES})
        self.assertEqual(env, {})

    def test_disabled_passes_become_disable_flags(self):
        env = tune.profile_env({"deep_search": False, "redemod": False})
        self.assertEqual(env, {"JS8_DISABLE_DEEP_SEARCH": "1",
                               "JS8_DISABLE_REDEMOD": "1"})

    def test_missing_keys_default_to_on(self):
        self.assertEqual(tune.profile_env({}), {})
        self.assertEqual(tune.profile_env(None), {})

    def test_profiles_shape(self):
        self.assertEqual(tune.PROFILES["default"], {})
        self.assertTrue(all(v is False for v in tune.PROFILES["minimal"].values()))
        self.assertEqual(set(tune.PROFILES["minimal"]), set(tune.PASSES))


class AttenuateTest(unittest.TestCase):
    def test_zero_db_is_identity(self):
        audio = array.array("h", [100, -100, 32767])
        self.assertEqual(list(tune.attenuate(audio, 0)), [100, -100, 32767])

    def test_minus_six_db_is_half(self):
        audio = array.array("h", [1000, -1000])
        out = tune.attenuate(audio, -6.02)
        self.assertEqual(list(out), [500, -500])

    def test_clipping_is_bounded(self):
        audio = array.array("h", [32000])
        out = tune.attenuate(audio, 12)          # x4, must clip
        self.assertEqual(out[0], 32767)

    def test_length_preserved(self):
        audio = array.array("h", range(100))
        self.assertEqual(len(tune.attenuate(audio, -20)), 100)


class ScoreTest(unittest.TestCase):
    def row(self, name, decodes, runs, cpu):
        return {"profile": name, "decodes": decodes, "runs": runs, "cpu_pct": cpu}

    def test_more_decodes_wins(self):
        best = tune.pick([self.row("a", 4, 6, 50.0), self.row("b", 6, 6, 50.0)])
        self.assertEqual(best["profile"], "b")

    def test_cpu_breaks_ties(self):
        best = tune.pick([self.row("slow", 6, 6, 80.0), self.row("fast", 6, 6, 20.0)])
        self.assertEqual(best["profile"], "fast")

    def test_cpu_never_outweighs_missing_decodes(self):
        # 10 % more CPU costs 0.015 of score; one decode in six is worth 0.167
        best = tune.pick([self.row("lean", 6, 6, 10.0), self.row("full", 7, 6, 60.0)])
        self.assertEqual(best["profile"], "full")

    def test_rate_and_table(self):
        rows = [self.row("x", 3, 6, 25.0)]
        tune.score(rows)
        self.assertAlmostEqual(rows[0]["rate"], 0.5)
        text = tune.table(rows)
        self.assertIn("profile", text)
        self.assertIn("x", text)


if __name__ == "__main__":
    unittest.main()


class MixNoiseTest(unittest.TestCase):
    """The fixture must be made *hard*, not just quiet - see attenuate()."""

    def setUp(self):
        self.audio = array.array("h", [3000, -3000] * 500)

    def correlation(self, mixed):
        """Normalised correlation with the original: 1 = untouched, 0 = noise.

        Used instead of a power ratio because mix_noise normalises the peak to
        avoid clipping, which scales signal and noise together; correlation is
        invariant to that gain.
        """
        dot = sum(m * b for m, b in zip(mixed, self.audio))
        num = dot
        den = (sum(m * m for m in mixed) ** 0.5) * (sum(b * b for b in self.audio) ** 0.5)
        return num / den if den else 0.0

    def test_same_length_and_type(self):
        out = tune.mix_noise(self.audio, -20)
        self.assertEqual(len(out), len(self.audio))
        self.assertEqual(out.typecode, "h")

    def test_quieter_snr_buries_the_signal_further(self):
        import statistics
        tight = self.correlation(tune.mix_noise(self.audio, -6))
        loose = self.correlation(tune.mix_noise(self.audio, -20))
        buried = self.correlation(tune.mix_noise(self.audio, -30))
        self.assertGreater(tight, loose)
        self.assertGreater(loose, buried)
        self.assertLess(buried, 0.15)

    def test_deterministic_for_a_seed(self):
        a = tune.mix_noise(self.audio, -20, seed=7)
        b = tune.mix_noise(self.audio, -20, seed=7)
        self.assertEqual(list(a)[:50], list(b)[:50])

    def test_seed_changes_the_noise(self):
        a = tune.mix_noise(self.audio, -20, seed=1)
        b = tune.mix_noise(self.audio, -20, seed=2)
        self.assertNotEqual(list(a)[:50], list(b)[:50])

    def test_no_clipping(self):
        out = tune.mix_noise(self.audio, 10)          # deliberately loud
        self.assertLessEqual(max(out), 32767)
        self.assertGreaterEqual(min(out), -32768)
