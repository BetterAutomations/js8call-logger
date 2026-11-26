"""Choosing the capture device, and every measurement following it."""

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from js8call_logger import cli, config


class InputDeviceTest(unittest.TestCase):
    def test_default_is_the_cable(self):
        cfg = dict(config.DEFAULTS)
        self.assertEqual(config.input_device(cfg), "js8call-logger_cable_src")

    def test_chosen_device_wins(self):
        cfg = dict(config.DEFAULTS, input_device="alsa_input.usb-X")
        self.assertEqual(config.input_device(cfg), "alsa_input.usb-X")

    def test_extra_channels_use_their_own_cable(self):
        cfg = dict(config.DEFAULTS, input_device="alsa_input.usb-X")
        self.assertEqual(config.input_device(cfg, 2), "js8call-logger_cable_src_2")


class CmdInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ,
                                   {"JS8CALL_LOGGER_HOME": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_cmd(self, **kw):
        args = argparse.Namespace(device=None, list=False, clear=False)
        for k, v in kw.items():
            setattr(args, k, v)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch("js8call_logger.pulse.sources",
                           return_value=[["0", "js8call-logger_cable_src"],
                                         ["1", "alsa_input.usb-X"]]):
            rc = cli.cmd_input(args)
        return rc, out.getvalue()

    def test_list_marks_the_current_one(self):
        rc, out = self.run_cmd(list=True)
        self.assertEqual(rc, 0)
        self.assertIn("* js8call-logger_cable_src", out)
        self.assertIn("  alsa_input.usb-X", out)

    def test_set_unknown_device_refuses(self):
        rc, out = self.run_cmd(device="nope")
        self.assertEqual(rc, 2)
        self.assertIn("no such capture source", out)
        self.assertIsNone(config.load().get("input_device"))

    def test_set_saves_and_says_when_it_applies(self):
        rc, out = self.run_cmd(device="alsa_input.usb-X")
        self.assertEqual(rc, 0)
        self.assertIn("applies at the next start", out)
        self.assertEqual(config.load()["input_device"], "alsa_input.usb-X")

    def test_clear_goes_back_to_the_cable(self):
        config.save(dict(config.DEFAULTS, input_device="alsa_input.usb-X"))
        rc, out = self.run_cmd(clear=True)
        self.assertEqual(rc, 0)
        self.assertIn("back to the cable", out)
        self.assertIsNone(config.load().get("input_device"))
