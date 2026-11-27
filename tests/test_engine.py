"""Tests for per-channel decoder setup (ini name, port, log tagging)."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from js8call_logger import cli, config, engine, records  # noqa: E402


class PortTest(unittest.TestCase):
    def test_ports_count_up_from_the_configured_one(self):
        cfg = dict(config.DEFAULTS)
        self.assertEqual(engine.channel_port(cfg, 1), 2442)
        self.assertEqual(engine.channel_port(cfg, 2), 2443)
        self.assertEqual(engine.channel_port(cfg, 3), 2444)


class IniTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = config.ensure_dirs(config.paths(self.tmp))
        self.cfg = dict(config.DEFAULTS)
        self.cfg["mycall"] = "N0CALL"

    def _ini(self, channel):
        path = engine.write_ini(self.cfg, self.p, channel)
        with open(path) as f:
            return path, f.read()

    def test_channel_one_keeps_the_plain_name(self):
        path, text = self._ini(1)
        self.assertTrue(path.endswith("JS8Call.ini"))
        self.assertIn("TCPServerPort=2442", text)
        self.assertIn("SoundInName=js8call-logger_cable_src", text)
        self.assertIn("AcceptTCPRequests=true", text)

    def test_channel_two_uses_the_rig_name_file(self):
        # JS8Call reads "JS8Call - ch2.ini" when started with -r ch2; the plain
        # name would give an instance on defaults with no TCP port.
        path, text = self._ini(2)
        self.assertTrue(path.endswith("JS8Call - ch2.ini"), path)
        self.assertIn("TCPServerPort=2443", text)
        self.assertIn("SoundInName=js8call-logger_cable_src_2", text)

    def test_channels_have_separate_directories(self):
        p1 = engine.write_ini(self.cfg, self.p, 1)
        p2 = engine.write_ini(self.cfg, self.p, 2)
        self.assertNotEqual(os.path.dirname(p1), os.path.dirname(p2))


class ChannelTagTest(unittest.TestCase):
    BASE = {"ts": "2026-11-25 08:31:57", "dial": 7078000, "snr": 33,
            "offset": 1500, "src": "N0CALL", "dst": "@ALLCALL",  # tone shown
            "text": "CQ CQ CQ EM48", "kind": "directed", "utc_ms": 1,
            "heartbeat": False}

    def test_single_channel_line_has_no_tag(self):
        rec = dict(self.BASE, channel=1, channels_total=1)
        line = records.render(rec)[0]
        self.assertNotIn("ch1", line)
        self.assertIn("N0CALL", line)

    def test_multi_channel_line_is_tagged(self):
        rec = dict(self.BASE, channel=2, channels_total=2)
        line = records.render(rec)[0]
        self.assertIn("ch2", line)
        self.assertIn("N0CALL", line)

    def test_tag_keeps_the_width_cap(self):
        rec = dict(self.BASE, channel=2, channels_total=2, text="word " * 90)
        for line in records.render(rec, 150):
            self.assertLessEqual(len(line), 150)


class SinkChannelTest(unittest.TestCase):
    def test_channel_reaches_sqlite(self):
        tmp = tempfile.mkdtemp()
        p = config.ensure_dirs(config.paths(tmp))
        cfg = dict(config.DEFAULTS)
        sink = records.Sink(p, cfg)
        rec = dict(ChannelTagTest.BASE, channel=2, channels_total=2)
        sink.write(rec, records.render(rec))
        sink.close()
        import sqlite3
        db = sqlite3.connect(os.path.join(p["logs"], "js8call-logger.db"))
        self.assertEqual(db.execute("select channel from records").fetchone()[0], 2)
        db.close()


class StatusErrorTest(unittest.TestCase):
    """A failure must stop being reported once the daemon is healthy again.

    status.json is merged rather than replaced, so an error written once used
    to stay for the life of the file - and `start` would then report a failure
    from the previous run while the service was coming up fine.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = {"run": self.tmp}

    def _decoder(self, error=""):
        class D:
            last_error = error
        return D()

    def test_error_clears_when_a_later_write_says_so(self):
        engine.write_status(self.p, running=False,
                            error="decoder 1 exited with -15")
        self.assertEqual(engine.read_status(self.p)["error"],
                         "decoder 1 exited with -15")
        engine.write_status(self.p, running=True, error="")
        self.assertEqual(engine.read_status(self.p)["error"], "")

    def test_a_healthy_decoder_reports_nothing(self):
        self.assertEqual(engine.decoder_error([self._decoder()]), "")

    def test_the_first_failure_is_the_one_reported(self):
        decs = [self._decoder(), self._decoder("API did not come up")]
        self.assertEqual(engine.decoder_error(decs), "API did not come up")


class StatusStampTest(unittest.TestCase):
    """`start` must be able to tell this launch's status from the last one's."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = {"run": self.tmp}

    def test_missing_status_has_no_stamp(self):
        self.assertEqual(cli._status_stamp(self.p), 0)

    def test_a_rewrite_changes_the_stamp(self):
        path = os.path.join(self.tmp, "status.json")
        engine.write_status(self.p, running=False)
        first = cli._status_stamp(self.p)
        self.assertGreater(first, 0)
        engine.write_status(self.p, running=True)
        self.assertNotEqual(cli._status_stamp(self.p), first)


if __name__ == "__main__":
    unittest.main()
