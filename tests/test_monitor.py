import argparse
import unittest
from unittest import mock

from js8call_logger import cli, pulse


def ns(**kw):
    base = {"monitor": False, "no_monitor": False}
    base.update(kw)
    return argparse.Namespace(**base)


class WantTest(unittest.TestCase):
    def test_off_flag_wins(self):
        self.assertFalse(cli._want_monitor(ns(no_monitor=True), {"monitor": True}))

    def test_on_flag_wins(self):
        self.assertTrue(cli._want_monitor(ns(monitor=True), {"monitor": False}))

    def test_saved_setting_without_flags(self):
        self.assertFalse(cli._want_monitor(ns(), {"monitor": False}))
        self.assertTrue(cli._want_monitor(ns(), {"monitor": True}))


CABLE = "js8call-logger_cable"
SPK = "Headphones"


def trap(blocks):
    return mock.patch.object(pulse, "_module_blocks", return_value=blocks), \
           mock.patch.object(pulse, "default_sink_name", return_value=SPK), \
           mock.patch.object(pulse, "cable_channels", return_value=[1])


class EchoTest(unittest.TestCase):
    def test_our_monitor_to_speakers_is_found(self):
        a, b, c = trap([(7, "module-loopback source=%s.monitor sink=%s" % (CABLE, SPK))])
        with a, b, c:
            self.assertEqual(pulse.monitor_loops_to_default(), [7])

    def test_feed_into_the_cable_is_not_the_trap(self):
        a, b, c = trap([(8, "module-loopback source=morse-logger_cable.monitor sink=" + CABLE)])
        with a, b, c:
            self.assertEqual(pulse.monitor_loops_to_default(), [])

    def test_another_programs_monitor_is_not_ours(self):
        a, b, c = trap([(9, "module-loopback source=morse-logger_cable.monitor sink=" + SPK)])
        with a, b, c:
            self.assertEqual(pulse.monitor_loops_to_default(), [])
