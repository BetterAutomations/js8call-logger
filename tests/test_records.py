"""Tests for the log pipeline: rendering, dedupe, filters, file writers."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from js8call_logger import config, records  # noqa: E402


def rec(**kw):
    base = {"ts": "2026-11-25 02:38:00", "dial": 14078000, "snr": 33,
            "offset": 1500, "src": "N0CALL", "dst": "@ALLCALL", "cmd": " CQ",
            "text": "CQ CQ CQ EM48", "kind": "directed", "utc_ms": 1000,
            "heartbeat": False}
    base.update(kw)
    return base


class RenderTest(unittest.TestCase):
    def test_short_record_is_one_line(self):
        lines = records.render(rec())
        self.assertEqual(len(lines), 1)
        self.assertIn("2026-11-25 02:38:00", lines[0])
        self.assertIn("14.078MHz", lines[0])
        self.assertIn("snr= +33", lines[0])
        self.assertIn("coh=100", lines[0])
        self.assertIn("1500Hz", lines[0])
        self.assertIn("N0CALL", lines[0])
        self.assertTrue(lines[0].endswith("CQ CQ CQ EM48"))

    def test_text_starts_in_a_fixed_column(self):
        a = records.render(rec())[0]
        b = records.render(rec(dial=3578000, snr=-12, src="W1AW", text="TNX 73"))[0]
        c = records.render(rec(dial=None, snr=None, text="GRID FN42"))[0]
        cols = {ln.index(ln.rsplit("]", 1)[1].strip()) for ln in (a, b, c)}
        self.assertEqual(len(cols), 1, cols)

    def test_unknown_fields_are_dashes(self):
        line = records.render(rec(dial=None, snr=None, offset=None))[0]
        self.assertIn("---MHz", line)
        self.assertIn("snr=  --", line)
        self.assertIn("  -Hz", line)

    def test_coherence_lands_in_the_record(self):
        r = rec(text="CQ CQ CQ EM48")
        records.render(r)
        self.assertEqual(r["coherence"], 100)
        garbage = rec(text="hq tz bv kw qx zz")
        records.render(garbage)
        self.assertEqual(garbage["coherence"], 0)

    def test_every_line_under_cap(self):
        for width in (150, 120, 80):
            lines = records.render(rec(text="CQ CQ CQ EM48 " * 30), width)
            self.assertTrue(lines, "no output")
            for ln in lines:
                self.assertLessEqual(len(ln), width, ln)

    def test_continuation_keeps_timestamp(self):
        lines = records.render(rec(text="word " * 80))
        self.assertGreater(len(lines), 1)
        for ln in lines:
            self.assertTrue(ln.startswith("2026-11-25 02:38:00"), ln)
        self.assertTrue(lines[1].startswith("2026-11-25 02:38:00  ... "))

    def test_oversized_token_moves_then_truncates(self):
        lines = records.render(rec(text="A" + "x" * 400 + " B"))
        for ln in lines:
            self.assertLessEqual(len(ln), 150, ln)
        joined = "".join(ln.split("...", 1)[-1] if "..." in ln else ln
                         for ln in lines)
        self.assertIn("\u2026", "".join(lines))
        # nothing invented, nothing silently lost beyond the ellipsis markers
        self.assertGreaterEqual(joined.count("x"), 350)

    def test_word_is_not_split_when_it_fits(self):
        words = ["abcdefghij"] * 30
        lines = records.render(rec(text=" ".join(words)))
        for ln in lines[1:]:
            body = ln.split("... ", 1)[-1]
            for w in body.split():
                self.assertEqual(w, "abcdefghij")


class DedupeTest(unittest.TestCase):
    ACT = {"kind": "activity", "utc_ms": 7, "dial": 1, "offset": 2, "text": "a"}
    DIR = {"kind": "directed", "utc_ms": 7, "dial": 1, "offset": 2, "text": "b"}

    def test_pair_merges_to_directed(self):
        d = records.Deduper(grace_s=2.0)
        self.assertEqual(d.add(self.ACT, now=100), [])
        self.assertEqual(d.add(self.DIR, now=100.1), [])
        out = d.flush(now=102.5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "directed")

    def test_reverse_order_also_merges(self):
        d = records.Deduper(grace_s=2.0)
        d.add(self.DIR, now=100)
        d.add(self.ACT, now=100.1)
        out = d.flush(now=102.5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "directed")

    def test_duplicate_after_write_is_dropped(self):
        d = records.Deduper(grace_s=0.0)
        first = d.add(self.DIR, now=100)
        self.assertEqual(len(first), 1)
        self.assertEqual(d.add(self.ACT, now=101), [])
        self.assertEqual(d.dropped_duplicates, 1)

    def test_different_transmissions_both_survive(self):
        d = records.Deduper(grace_s=0.0)
        out = d.add({"kind": "directed", "utc_ms": 7, "dial": 1, "offset": 2,
                     "text": "a"}, now=100)
        out += d.add({"kind": "directed", "utc_ms": 8, "dial": 1, "offset": 2,
                      "text": "b"}, now=101)
        self.assertEqual(len(out), 2)

    def test_grace_holds_then_releases(self):
        d = records.Deduper(grace_s=2.0)
        self.assertEqual(d.add({"kind": "directed", "utc_ms": 1, "dial": 1,
                                "offset": 1, "text": "x"}, now=100), [])
        self.assertEqual(len(d.flush(now=101)), 0)
        self.assertEqual(len(d.flush(now=102.5)), 1)


class ParseTest(unittest.TestCase):
    def test_directed(self):
        r = records.parse_event({
            "type": "RX.DIRECTED", "value": "K1ABC: CQ",
            "params": {"FROM": "K1ABC", "TO": "@ALLCALL", "CMD": " CQ",
                       "TEXT": "CQ CQ", "SNR": 5, "OFFSET": 1200,
                       "DIAL": 7078000, "UTC": 1769740328005,
                       "TDRIFT": -1.2}})
        self.assertEqual(r["src"], "K1ABC")
        self.assertEqual(r["snr"], 5)
        self.assertAlmostEqual(r["tdrift"], -1.2)
        self.assertTrue(r["ts"].startswith("2026-01-"))

    def test_activity_splits_sender(self):
        r = records.parse_event({"type": "RX.ACTIVITY",
                                 "value": "N0CALL: @ALLCALL CQ CQ CQ EM48 ",
                                 "params": {"UTC": 1769740328005, "OFFSET": 1500}})
        self.assertEqual(r["src"], "N0CALL")
        self.assertEqual(r["text"], "@ALLCALL CQ CQ CQ EM48")

    def test_non_rx_ignored(self):
        self.assertIsNone(records.parse_event({"type": "RIG.PTT", "params": {}}))

    def test_heartbeat_variants(self):
        self.assertTrue(records.is_heartbeat(" HEARTBEAT", "@HB", "x"))
        self.assertTrue(records.is_heartbeat("", "@HB", "x"))
        self.assertTrue(records.is_heartbeat("", "", "HB FN42"))
        self.assertTrue(records.is_heartbeat("", "", "@HB HEARTBEAT"))
        self.assertFalse(records.is_heartbeat(" MSG", "K1ABC", "hello there"))

    def test_station_of(self):
        self.assertEqual(records.station_of(rec(src="K1ABC", dst="N0CALL"), "N0CALL"),
                         "K1ABC")
        self.assertEqual(records.station_of(rec(src="N0CALL", dst="K1ABC"), "N0CALL"),
                         "K1ABC")
        self.assertEqual(records.station_of(rec(src="K1ABC", dst="@SITREP"), "N0CALL"),
                         "K1ABC")


class SinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = config.ensure_dirs(config.paths(self.tmp))
        self.cfg = dict(config.DEFAULTS)
        self.sink = records.Sink(self.p, self.cfg)

    def tearDown(self):
        self.sink.close()

    def _text(self):
        path = os.path.join(self.p["logs"], "js8call-logger-2026-11-25.txt")
        if not os.path.exists(path):
            return ""
        with open(path) as f:
            return f.read()

    def _jsonl(self):
        with open(os.path.join(self.p["logs"], "js8call-logger.jsonl")) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_heartbeat_out_of_text_but_in_jsonl(self):
        r = rec(heartbeat=True, text="@HB HEARTBEAT")
        self.sink.write(r, records.render(r))
        self.assertNotIn("@HB", self._text())
        self.assertEqual(len(self._jsonl()), 1)

    def test_heartbeat_in_text_when_asked(self):
        self.sink.close()
        self.cfg["heartbeats_in_text"] = True
        self.sink = records.Sink(self.p, self.cfg)
        r = rec(heartbeat=True, text="@HB HEARTBEAT")
        self.sink.write(r, records.render(r))
        self.assertIn("@HB", self._text())

    def test_conversation_file_per_correspondent(self):
        r = rec(src="K1ABC", dst="N0CALL", text="hello")
        self.sink.write(r, records.render(r))
        path = os.path.join(self.p["conversations"], "K1ABC.txt")
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            self.assertIn("hello", f.read())

    def test_snr_floor_applies_to_text_only(self):
        self.sink.close()
        self.cfg["snr_min_text"] = 0
        self.sink = records.Sink(self.p, self.cfg)
        r = rec(snr=-12, text="weak")
        self.sink.write(r, records.render(r))
        self.assertNotIn("weak", self._text())
        self.assertEqual(len(self._jsonl()), 1)


if __name__ == "__main__":
    unittest.main()
