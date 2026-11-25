"""Tests for the dial verb and its unit handling."""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from js8call_logger import cli, config


def run(argv):
    out = io.StringIO()
    ns = argparse.Namespace(**argv)
    with contextlib.redirect_stdout(out):
        rc = cli.cmd_dial(ns)
    return rc, out.getvalue()


class ParseFreqTest(unittest.TestCase):
    def test_units(self):
        cases = {
            "7.078": 7078000, "7078": 7078000, "7078.00": 7078000,
            "7041.09": 7041090, "7078000": 7078000,
            "14.078MHz": 14078000, "3578kHz": 3578000, "7078000Hz": 7078000,
            "7,078": 7078000, "0.472": 472000,
        }
        for text, hz in cases.items():
            self.assertEqual(cli.parse_freq(text), hz, text)

    def test_nonsense_raises(self):
        with self.assertRaises(ValueError):
            cli.parse_freq("forty metres")


class DialVerbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = config.ensure_dirs(config.paths(self.tmp.name))
        self._old = os.environ.get("JS8CALL_LOGGER_HOME")
        os.environ["JS8CALL_LOGGER_HOME"] = self.tmp.name
        config.save(dict(config.DEFAULTS, dial_hz=7078000))

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JS8CALL_LOGGER_HOME", None)
        else:
            os.environ["JS8CALL_LOGGER_HOME"] = self._old
        self.tmp.cleanup()

    def test_show(self):
        rc, out = run({"freq": None, "clear": False, "offset": 1500})
        self.assertEqual(rc, 0)
        self.assertIn("dial: 7.078 MHz", out)
        self.assertIn("not running", out)

    def test_set_khz_with_note(self):
        rc, out = run({"freq": "7078.00", "clear": False, "offset": 1500})
        self.assertEqual(rc, 0)
        self.assertIn("looks like kHz", out)
        self.assertIn("dial set to 7.078 MHz", out)
        self.assertIn("applied at next start", out)
        saved = json.load(open(self.home["config"]))
        self.assertEqual(saved["dial_hz"], 7078000)

    def test_set_mhz_has_no_note(self):
        _, out = run({"freq": "14.078", "clear": False, "offset": 1500})
        self.assertNotIn("looks like kHz", out)
        self.assertIn("dial set to 14.078 MHz", out)

    def test_clear(self):
        rc, out = run({"freq": None, "clear": True, "offset": 1500})
        self.assertEqual(rc, 0)
        self.assertIn("cleared", out)
        self.assertIsNone(json.load(open(self.home["config"]))["dial_hz"])

    def test_bad_value_refused(self):
        rc, out = run({"freq": "somewhere", "clear": False, "offset": 1500})
        self.assertEqual(rc, 2)
        self.assertIn("not a frequency", out)


class HelpTest(unittest.TestCase):
    def test_dial_is_a_documented_verb(self):
        parser = cli.build_parser()
        choices = parser._subparsers._group_actions[0].choices
        self.assertIn("dial", choices)
        self.assertIn("dial", parser.format_help())


if __name__ == "__main__":
    unittest.main()
