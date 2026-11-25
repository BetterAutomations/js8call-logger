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


class PlaceholderDuplicateTest(unittest.TestCase):
    """A decode answered twice: real text plus JS8Call's "……" placeholder.

    Observed on 2026-11-25 13:39:43 at 7.078 MHz, offset 1152, SNR -24: the
    same utc_ms arrived twice, ~20 s apart, once with text and once with the
    placeholder. One transmission, one log line, and the line keeps the text.
    """

    BASE = {"kind": "activity", "utc_ms": 1795631983375, "dial": 7078000,
            "offset": 1152, "snr": -24, "src": "", "dst": "",
            "ts": "2026-11-25 13:39:43"}

    def test_late_placeholder_is_dropped(self):
        d = records.Deduper(grace_s=2.0)          # default 60 s memory
        out = d.add(dict(self.BASE, text="HEY! ARE YOU ON T"), now=100.0)
        out += d.flush(now=102.5)                 # written already
        out += d.add(dict(self.BASE, text="\u2026\u2026"), now=122.0)
        out += d.flush(now=140.0)
        self.assertEqual([r["text"] for r in out], ["HEY! ARE YOU ON T"])
        self.assertEqual(d.dropped_duplicates, 1)

    def test_placeholder_does_not_replace_text(self):
        d = records.Deduper(grace_s=2.0)
        d.add(dict(self.BASE, text="\u2026\u2026"), now=100.0)
        d.add(dict(self.BASE, text="HEY! ARE YOU ON T"), now=101.0)
        out = d.flush(now=110.0)
        self.assertEqual([r["text"] for r in out], ["HEY! ARE YOU ON T"])

    def test_placeholder_never_reaches_the_text_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = config.ensure_dirs(config.paths(tmp))
            sink = records.Sink(p, dict(config.DEFAULTS))
            rec = dict(self.BASE, text="\u2026\u2026")
            rec["coherence"] = 0
            sink.write(rec, records.render(rec))
            sink.close()
            path = os.path.join(p["logs"], "js8call-logger-2026-11-25.txt")
            text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
            jsonl = open(os.path.join(p["logs"], "js8call-logger.jsonl"),
                         encoding="utf-8").read()
            self.assertNotIn("\u2026\u2026", text)
            self.assertIn("\u2026\u2026", jsonl)


if __name__ == "__main__":
    unittest.main()


class FragmentTest(unittest.TestCase):
    """A decode is one frame of a longer transmission: say so in the log.

    Frame types come from the API's BITS field, mapped against ALL.TXT:
    3 = directed (complete), 1/2 = compound message header and body chunks,
    0 = heartbeat OR free text - free text only counts as a fragment when it
    fills a frame (13+ characters), so a one-word "73" is not marked.
    """

    BASE = {"ts": "2026-11-25 13:39:43", "dial": 7078000, "snr": -24,
            "offset": 1152, "src": "", "dst": ""}

    def test_frame_types(self):
        cases = [
            (0, "HEY! ARE YOU ON T", False, True),    # free-text chunk
            (0, "73", False, False),                  # short, probably whole
            (0, "@HB HEARTBEAT EM48", True, False),   # heartbeat
            (1, "W4CAT: ", False, True),              # compound header
            (2, "EMAIL CHECK", False, True),          # compound body
            (3, "K1ABC: K2DEF NO", False, False),     # complete directed
            (None, "ANY TEXT AT ALL", False, False),  # older API, no BITS
        ]
        for frame, text, hb, want in cases:
            self.assertEqual(records.is_fragment(frame, text, hb), want,
                             f"frame={frame} text={text!r}")

    def test_fragment_line_ends_with_a_marker(self):
        rec = dict(self.BASE, text="HEY! ARE YOU ON T", frame=0,
                   heartbeat=False, fragment=True)
        line = records.render(rec)[0]
        self.assertTrue(line.endswith("\u2026"))
        self.assertLessEqual(len(line), 150)

    def test_complete_line_has_no_marker(self):
        rec = dict(self.BASE, text="K1ABC: K2DEF NO", frame=3,
                   heartbeat=False, fragment=False)
        self.assertFalse(records.render(rec)[0].endswith("\u2026"))

    def test_columns_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = config.ensure_dirs(config.paths(tmp))
            sink = records.Sink(p, dict(config.DEFAULTS))
            rec = dict(self.BASE, text="HEY! ARE YOU ON T", frame=0,
                       heartbeat=False, fragment=True, kind="activity")
            sink.write(rec, records.render(rec))
            sink.close()
            import sqlite3
            db = sqlite3.connect(os.path.join(p["logs"], "js8call-logger.db"))
            row = db.execute("SELECT frame, fragment FROM records").fetchone()
            self.assertEqual(row, (0, 1))


class DirectedFragmentTest(unittest.TestCase):
    """A DIRECTED record without the EOT character is not the last frame."""

    def ev(self, text):
        return {"type": "RX.DIRECTED", "value": text,
                "params": {"FROM": "K1ABC", "TO": "@ALLCALL", "CMD": " CQ",
                           "TEXT": text, "SNR": 3, "OFFSET": 1500,
                           "DIAL": 7078000, "UTC": 1795631983375}}

    def test_complete_message_has_no_marker(self):
        rec = records.parse_event(self.ev("K1ABC: @ALLCALL CQ CQ \u2662"))
        self.assertFalse(rec["fragment"])

    def test_missing_eot_marks_a_fragment(self):
        rec = records.parse_event(self.ev("HEY! ARE YOU ON T"))
        self.assertTrue(rec["fragment"])
        self.assertTrue(records.render(rec)[0].endswith("\u2026"))

    def test_custom_eot(self):
        rec = records.parse_event(self.ev("K1ABC: TEST ~~"), eot="~~")
        self.assertFalse(rec["fragment"])

    def test_frame_type_survives_the_merge(self):
        d = records.Deduper(grace_s=2.0)
        act = dict(self.ev("HEY! ARE YOU ON T"))
        act["type"] = "RX.ACTIVITY"
        act["params"] = {"BITS": 1, "SNR": -24, "OFFSET": 1152,
                         "DIAL": 7078000, "UTC": 1795631983375}
        a = records.parse_event(act)
        b = records.parse_event(self.ev("HEY! ARE YOU ON T"))
        self.assertIsNone(b["frame"])
        d.add(a, now=100.0)
        got = d.flush(now=110.0)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["frame"], 1)
