"""The CPU profile, and that scaling never compounds into the saved config."""

import json
import os
import tempfile
import unittest
from unittest import mock

from js8call_logger import config, cpu


class ClassifyTest(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(cpu.classify(0.028), "fast")
        self.assertEqual(cpu.classify(0.055), "fast")
        self.assertEqual(cpu.classify(0.056), "medium")
        self.assertEqual(cpu.classify(0.100), "medium")
        self.assertEqual(cpu.classify(0.101), "slow")
        self.assertEqual(cpu.classify(0.150), "slow")     # SU9300 territory

    def test_benchmark_is_positive(self):
        self.assertGreater(cpu.benchmark(reps=1), 0.0)


class DetectTest(unittest.TestCase):
    def test_cache_is_used(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "run"))
            with open(os.path.join(home, "run", cpu.CACHE), "w") as f:
                json.dump({"version": cpu.BENCH_VERSION, "seconds": 0.12,
                           "profile": "slow"}, f)
            with mock.patch.object(cpu, "benchmark") as bench:
                seconds, profile = cpu.detect(home)
            bench.assert_not_called()
            self.assertEqual(profile, "slow")
            self.assertEqual(seconds, 0.12)

    def test_old_cache_version_is_remeasured(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "run"))
            with open(os.path.join(home, "run", cpu.CACHE), "w") as f:
                json.dump({"version": 0, "seconds": 9.9, "profile": "slow"}, f)
            seconds, profile = cpu.detect(home)
            self.assertNotEqual(seconds, 9.9)
            self.assertIn(profile, cpu.SCALES)


class ScaledTest(unittest.TestCase):
    def test_fast_keeps_the_value(self):
        cfg = {"_cpu_scale": 1.0, "watchdog_silence_s": 300}
        self.assertEqual(config.scaled(cfg, "watchdog_silence_s", 300), 300.0)

    def test_slow_stretches_it(self):
        cfg = {"_cpu_scale": 4.0, "watchdog_silence_s": 300}
        self.assertEqual(config.scaled(cfg, "watchdog_silence_s", 300), 1200.0)

    def test_default_is_used_when_unset(self):
        cfg = {"_cpu_scale": 2.0}
        self.assertEqual(config.scaled(cfg, "route_interval_s", 3), 6.0)

    def test_no_scale_means_one(self):
        self.assertEqual(config.scaled({}, "status_interval_s", 5), 5.0)


class SaveTest(unittest.TestCase):
    def test_runtime_keys_are_not_written(self):
        with tempfile.TemporaryDirectory() as home:
            p = config.ensure_dirs(config.paths(home))
            cfg = dict(config.DEFAULTS)
            cfg["_cpu_score"] = 0.12
            cfg["_cpu_profile"] = "slow"
            cfg["_cpu_scale"] = 4.0
            cfg["watchdog_silence_s"] = 300
            config.save(cfg, p)
            with open(p["config"]) as f:
                disk = json.load(f)
            self.assertNotIn("_cpu_scale", disk)
            self.assertNotIn("_cpu_profile", disk)
            self.assertEqual(disk["watchdog_silence_s"], 300)   # never scaled on disk


if __name__ == "__main__":
    unittest.main()
