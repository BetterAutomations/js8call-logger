"""RTL-SDR plumbing, exercised with stub executables (no dongle needed)."""

import os
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from js8call_logger import sdr


def make_exe(path, body):
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


class CommandTest(unittest.TestCase):
    def cfg(self, **kw):
        base = {"dial_hz": 7078000, "cable_rate": 48000, "rtl_sample_rate": 240000}
        base.update(kw)
        return base

    def test_hf_needs_direct_sampling(self):
        cmd = sdr.fm_command(self.cfg())
        self.assertIn("7078000", cmd)
        self.assertIn("direct2", cmd)
        self.assertIn("usb", cmd)

    def test_vhf_does_not(self):
        cmd = sdr.fm_command(self.cfg(dial_hz=144078000))
        self.assertNotIn("direct2", cmd)

    def test_stick_options(self):
        cmd = sdr.fm_command(self.cfg(rtl_gain="49.6", rtl_ppm="12", rtl_serial="00000001"))
        for value in ("49.6", "12", "00000001"):
            self.assertIn(value, cmd)

    def test_no_dial_is_refused(self):
        with self.assertRaises(sdr.SdrError):
            sdr.fm_command({"dial_hz": None})


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "played.raw")
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = self.bin + os.pathsep + self.old_path
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ["PATH"] = self.old_path

    def source(self):
        cfg = {"dial_hz": 7078000, "cable_rate": 48000}
        return sdr.RtlSource(cfg, "test-sink", log=lambda m: None)

    def test_audio_reaches_the_player_and_death_is_noticed(self):
        make_exe(os.path.join(self.bin, "rtl_fm"),
                 "head -c 96000 /dev/zero; sleep 30\n")
        make_exe(os.path.join(self.bin, "pacat"),
                 f'cat > {self.out}\n')
        src = self.source()
        src.start()
        time.sleep(1.2)
        self.assertTrue(src.running())
        self.assertIsNone(src.check())
        self.assertGreater(os.path.getsize(self.out), 0)
        src.tuner.kill()
        time.sleep(0.5)
        self.assertIn("stopped", src.check())
        src.stop()

    def test_missing_device_explains_the_dvb_driver(self):
        make_exe(os.path.join(self.bin, "rtl_fm"),
                 'echo "No supported devices found." >&2; exit 1\n')
        make_exe(os.path.join(self.bin, "pacat"), "cat > /dev/null\n")
        src = self.source()
        src.start()
        time.sleep(1.0)
        self.assertEqual(src.last_error, sdr.DVB_HINT)
        self.assertIn("rtl28xxu", src.check())
        src.stop()

    def test_missing_binary_is_refused(self):
        make_exe(os.path.join(self.bin, "pacat"), "cat > /dev/null\n")
        src = self.source()
        with mock.patch.object(sdr, "binary", return_value=None):
            with self.assertRaises(sdr.SdrError):
                src.start()
