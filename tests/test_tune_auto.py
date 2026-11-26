"""Decode-sweep automation: when it may run, and when it must keep its hands off."""

import os
import tempfile
import time
import unittest

from js8call_logger import config, tune


def rows(*specs):
    """specs of (profile, decodes, runs, cpu_pct) -> sweep-shaped rows."""
    out = []
    for profile, decodes, runs, cpu in specs:
        out.append({"profile": profile, "decodes": decodes, "runs": runs,
                    "cpu_pct": cpu, "rate": decodes / runs})
    return out


class ProfileTest(unittest.TestCase):
    def test_names_round_trip(self):
        cfg = dict(config.DEFAULTS)
        self.assertEqual(tune.current_profile(cfg), "default")
        tune.apply_profile(cfg, "lean")
        self.assertEqual(tune.current_profile(cfg), "lean")
        tune.apply_profile(cfg, "minimal")
        self.assertEqual(tune.current_profile(cfg), "minimal")

    def test_something_else_is_custom(self):
        cfg = dict(config.DEFAULTS,
                   decode_env={"deep_search": False, "redemod": False})
        self.assertEqual(tune.current_profile(cfg), "custom")


class DueTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home))

    def test_off_by_default(self):
        self.assertFalse(tune.due(dict(config.DEFAULTS), self.home)[0])

    def test_never_run_is_due(self):
        cfg = dict(config.DEFAULTS, decode_auto=True)
        self.assertTrue(tune.due(cfg, self.home)[0])

    def test_recent_is_not_due(self):
        cfg = dict(config.DEFAULTS, decode_auto=True, decode_auto_days=14)
        tune.write_state(self.home, last_sweep=time.time() - 86400)
        self.assertFalse(tune.due(cfg, self.home)[0])

    def test_old_is_due(self):
        cfg = dict(config.DEFAULTS, decode_auto=True, decode_auto_days=14)
        tune.write_state(self.home, last_sweep=time.time() - 20 * 86400)
        due, why = tune.due(cfg, self.home)
        self.assertTrue(due)
        self.assertIn("20.0 d ago", why)


class ChooseTest(unittest.TestCase):
    def test_clear_win_wins(self):
        best, why = tune.choose(rows(("default", 2, 3, 5), ("lean", 3, 3, 5)),
                                "default")
        self.assertEqual(best["profile"], "lean")
        self.assertIn("beats default", why)

    def test_marginal_win_is_ignored(self):
        best, why = tune.choose(rows(("default", 10, 10, 5), ("lean", 10, 10, 4.9)),
                                "default")
        self.assertEqual(best["profile"], "default")
        self.assertIn("not clearly better", why)

    def test_loss_keeps_the_current_one(self):
        best, _ = tune.choose(rows(("default", 3, 3, 5), ("minimal", 1, 3, 5)),
                              "default")
        self.assertEqual(best["profile"], "default")

    def test_unmeasured_current_falls_back_to_best(self):
        best, why = tune.choose(rows(("lean", 3, 3, 5)), "default")
        self.assertEqual(best["profile"], "lean")
        self.assertIn("not measured", why)

    def test_no_results(self):
        best, why = tune.choose([], "default")
        self.assertIsNone(best)
        self.assertEqual(why, "no results")
