"""Tests for per-callsign and per-group conversation files."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from js8call_logger import config, records


class TargetsTest(unittest.TestCase):
    def targets(self, **kw):
        base = {"src": "", "dst": ""}
        base.update(kw)
        return records.conversation_targets(base, "N0CALL")

    def test_group_and_sender(self):
        self.assertEqual(self.targets(src="K1ABC", dst="@SITREP"),
                         ["K1ABC.txt", os.path.join("groups", "SITREP.txt")])

    def test_direct_to_me(self):
        self.assertEqual(self.targets(src="K1ABC", dst="N0CALL"), ["K1ABC.txt"])

    def test_cq_from_me(self):
        self.assertEqual(self.targets(src="N0CALL", dst="@ALLCALL"),
                         [os.path.join("groups", "ALLCALL.txt")])

    def test_nothing_known(self):
        self.assertEqual(self.targets(), ["UNKNOWN.txt"])

    def test_group_name_is_sanitised(self):
        self.assertEqual(self.targets(dst="@GHOST-NET"),
                         [os.path.join("groups", "GHOSTNET.txt")])


class SinkTest(unittest.TestCase):
    def write(self, sink, **kw):
        base = {"ts": "2026-11-25 15:30:00", "dial": 7078000, "snr": 3,
                "offset": 1500, "src": "K1ABC", "dst": "@SITREP",
                "text": "TNX OM", "coherence": 100, "heartbeat": False,
                "kind": "directed"}
        base.update(kw)
        sink.write(base, records.render(base))

    def test_record_lands_in_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = config.ensure_dirs(config.paths(tmp))
            sink = records.Sink(p, dict(config.DEFAULTS))
            self.write(sink)
            sink.close()
            self.assertTrue(os.path.exists(
                os.path.join(p["conversations"], "K1ABC.txt")))
            self.assertTrue(os.path.exists(
                os.path.join(p["conversations"], "groups", "SITREP.txt")))

    def test_heartbeat_lands_nowhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = config.ensure_dirs(config.paths(tmp))
            sink = records.Sink(p, dict(config.DEFAULTS))
            self.write(sink, dst="@HB", heartbeat=True, text="@HB HEARTBEAT")
            sink.close()
            self.assertEqual(os.listdir(p["conversations"]), [])

    def test_old_flat_group_files_are_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = config.ensure_dirs(config.paths(tmp))
            sink = records.Sink(p, dict(config.DEFAULTS))
            self.write(sink)
            sink.close()
            self.assertFalse(os.path.exists(
                os.path.join(p["conversations"], "GROUP-SITREP.txt")))


if __name__ == "__main__":
    unittest.main()
