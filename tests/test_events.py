"""Repair events: visible in the log, on the desktop, and in status."""

import os
import tempfile
import time
import unittest
from unittest import mock

from js8call_logger import config, engine, records


class EventLineTest(unittest.TestCase):
    def test_event_goes_to_the_daily_text_with_a_hash(self):
        with tempfile.TemporaryDirectory() as home:
            p = config.ensure_dirs(config.paths(home))
            sink = records.Sink(p, dict(config.DEFAULTS, expand=False))
            line = sink.event("watchdog: restarted the decoder", ts="2026-11-26 02:00:00")
            self.assertIn("2026-11-26 02:00:00  # watchdog: restarted", line)
            path = os.path.join(p["logs"], "js8call-logger-2026-11-26.txt")
            with open(path) as f:
                self.assertEqual(f.read().strip(), line)
            # an event is not traffic: no JSONL, no database row
            self.assertFalse(os.path.exists(os.path.join(p["logs"],
                                                         "js8call-logger.jsonl")))

    def test_events_append_in_order(self):
        with tempfile.TemporaryDirectory() as home:
            p = config.ensure_dirs(config.paths(home))
            sink = records.Sink(p, dict(config.DEFAULTS, expand=False))
            sink.event("first", ts="2026-11-26 02:00:00")
            sink.event("second", ts="2026-11-26 02:01:00")
            with open(os.path.join(p["logs"],
                                   "js8call-logger-2026-11-26.txt")) as f:
                lines = f.read().splitlines()
            self.assertTrue(lines[0].endswith("# first"))
            self.assertTrue(lines[1].endswith("# second"))


class NotifyTest(unittest.TestCase):
    def test_no_notify_send_is_not_an_error(self):
        with mock.patch.object(engine.shutil, "which", return_value=None):
            self.assertFalse(engine.notify_desktop("hi"))

    def test_failure_of_the_helper_is_swallowed(self):
        with mock.patch.object(engine.shutil, "which", return_value="/usr/bin/notify-send"):
            with mock.patch.object(engine.subprocess, "run",
                                   side_effect=OSError("no dbus")):
                self.assertFalse(engine.notify_desktop("hi"))

    def test_success_reports_true(self):
        with mock.patch.object(engine.shutil, "which", return_value="/usr/bin/notify-send"):
            with mock.patch.object(engine.subprocess, "run") as run:
                self.assertTrue(engine.notify_desktop("hi"))
            args = run.call_args[0][0]
            self.assertEqual(args[0], "/usr/bin/notify-send")
            self.assertIn("hi", args)


class EventDedupeTest(unittest.TestCase):
    def _engine(self):
        eng = engine.Engine.__new__(engine.Engine)
        eng.sink = mock.Mock()
        eng.cfg = {"notify": False}
        return eng

    def test_same_event_twice_is_written_once(self):
        eng = self._engine()
        eng._event("same")
        eng._event("same")
        self.assertEqual(eng.sink.event.call_count, 1)

    def test_different_event_is_written(self):
        eng = self._engine()
        eng._event("one")
        eng._event("two")
        self.assertEqual(eng.sink.event.call_count, 2)

    def test_repeat_after_ten_minutes_is_written_again(self):
        eng = self._engine()
        eng._event("same")
        eng._last_event_at = time.time() - 601
        eng._event("same")
        self.assertEqual(eng.sink.event.call_count, 2)


if __name__ == "__main__":
    unittest.main()
