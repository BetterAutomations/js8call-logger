"""Tests for the pactl parsing that decides what gets moved where."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from js8call_logger import pulse  # noqa: E402

PACTL = """Sink Input #269298
\tDriver: PipeWire
\tOwner Module: n/a
\tClient: 51
\tSink: 71
\tSample Specification: float32le 1ch 48000Hz
\tapplication.name = "pw-play"
\tmedia.name = "/tmp/tone.wav"
\tCorked: no

Sink Input #172927
\tSink: 71
\tapplication.name = "?"
\tmedia.name = "loopback-2167-13 output"
\tCorked: no
"""


class ParseTest(unittest.TestCase):
    def test_sink_index_resolved_to_name(self):
        rows = pulse.parse_sink_inputs(PACTL, {"71": "js8call-logger_cable"})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["index"], 269298)
        self.assertEqual(rows[0]["sink"], "71")
        self.assertEqual(rows[0]["sink_name"], "js8call-logger_cable")
        self.assertEqual(rows[0]["app"], "pw-play")
        self.assertEqual(rows[0]["stream"], "/tmp/tone.wav")

    def test_unknown_sink_keeps_index(self):
        rows = pulse.parse_sink_inputs(PACTL, {})
        self.assertEqual(rows[0]["sink_name"], "71")

    def test_missing_app_is_question_mark(self):
        rows = pulse.parse_sink_inputs(PACTL, {"71": "x"})
        self.assertEqual(rows[1]["app"], "?")

    def test_own_loopback_detected(self):
        rows = pulse.parse_sink_inputs(PACTL, {"71": "x"})
        self.assertFalse(pulse.is_own_loopback(rows[0]))
        self.assertTrue(pulse.is_own_loopback(rows[1]))

    def test_channel_names(self):
        self.assertEqual(pulse.sink_name(1), "js8call-logger_cable")
        self.assertEqual(pulse.sink_name(2), "js8call-logger_cable_2")
        self.assertEqual(pulse.source_name(1), "js8call-logger_cable_src")
        self.assertEqual(pulse.source_name(2), "js8call-logger_cable_src_2")

    def test_blank_input(self):
        self.assertEqual(pulse.parse_sink_inputs("", {}), [])


if __name__ == "__main__":
    unittest.main()
