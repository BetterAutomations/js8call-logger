"""The watchdog's decisions and the automatic prune schedule, without a daemon."""

import json
import os
import tempfile
import time
import unittest

from js8call_logger import engine, retention


BASE = {"watchdog": True, "watchdog_silence_s": 300, "watchdog_cooldown_s": 600,
        "watchdog_max_restarts": 4}


class WatchdogDecisionTest(unittest.TestCase):
    def _due(self, now=1000.0, started=0.0, last_decode=None, last_fire=0.0,
             fired=0, db=-20.0, **over):
        cfg = dict(BASE)
        cfg.update(over)
        return engine.watchdog_restart_due(now, started, last_decode, last_fire,
                                           fired, cfg, db)

    def test_fires_when_audio_flows_and_nothing_decodes(self):
        self.assertTrue(self._due())

    def test_waits_out_the_startup_grace(self):
        self.assertFalse(self._due(now=200.0, started=0.0))

    def test_recent_decode_resets_the_clock(self):
        self.assertFalse(self._due(now=1000.0, last_decode=900.0))

    def test_quiet_band_is_not_a_decoder_fault(self):
        self.assertFalse(self._due(db=-90.0))
        self.assertFalse(self._due(db=None))

    def test_cooldown_blocks_a_restart_loop(self):
        self.assertFalse(self._due(now=1000.0, last_fire=900.0))

    def test_gives_up_after_max_restarts(self):
        self.assertFalse(self._due(fired=4))
        self.assertTrue(self._due(fired=3))

    def test_disabled_never_fires(self):
        self.assertFalse(self._due(watchdog=False))


class AutoPruneTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="js8prune-")
        self.paths = {"home": self.home, "logs": os.path.join(self.home, "logs"),
                      "run": os.path.join(self.home, "run"),
                      "conversations": os.path.join(self.home, "conversations")}
        os.makedirs(self.paths["logs"])
        os.makedirs(self.paths["run"])

    def _old_file(self, name="js8call-logger-2020-01-01.txt"):
        path = os.path.join(self.paths["logs"], name)
        with open(path, "w") as f:
            f.write("old\n")
        return path

    def test_off_by_default(self):
        old = self._old_file()
        self.assertIsNone(retention.maybe_auto_prune(self.paths, {}))
        self.assertTrue(os.path.exists(old))

    def test_needs_a_retention_policy(self):
        old = self._old_file()
        self.assertIsNone(retention.maybe_auto_prune(
            self.paths, {"prune_auto": True, "retention_days": 0}))
        self.assertTrue(os.path.exists(old))

    def test_prunes_and_records_the_time(self):
        old = self._old_file()
        cfg = {"prune_auto": True, "retention_days": 30, "prune_interval_h": 24}
        result = retention.maybe_auto_prune(self.paths, cfg)
        self.assertIsNotNone(result)
        self.assertEqual(result["files"], 1)
        self.assertFalse(os.path.exists(old))
        with open(os.path.join(self.paths["run"], "prune.json")) as f:
            self.assertEqual(json.load(f)["days"], 30)

    def test_does_not_run_again_inside_the_interval(self):
        self._old_file()
        cfg = {"prune_auto": True, "retention_days": 30, "prune_interval_h": 24}
        self.assertIsNotNone(retention.maybe_auto_prune(self.paths, cfg))
        self._old_file()
        self.assertIsNone(retention.maybe_auto_prune(self.paths, cfg))

    def test_runs_again_after_the_interval(self):
        self._old_file()
        cfg = {"prune_auto": True, "retention_days": 30, "prune_interval_h": 24}
        retention.maybe_auto_prune(self.paths, cfg)
        state = os.path.join(self.paths["run"], "prune.json")
        with open(state, "w") as f:
            json.dump({"last": time.time() - 25 * 3600}, f)
        self._old_file()
        self.assertIsNotNone(retention.maybe_auto_prune(self.paths, cfg))


if __name__ == "__main__":
    unittest.main()
