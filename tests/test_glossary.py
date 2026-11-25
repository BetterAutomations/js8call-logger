"""Tests for shorthand expansion in the log."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from js8call_logger import config, glossary, records


def rec(text, **kw):
    base = {"ts": "2026-11-25 15:30:00", "dial": 7078000, "snr": 3,
            "offset": 1500, "src": "W4CAT", "text": text, "coherence": 100,
            "fragment": False, "heartbeat": False, "kind": "directed"}
    base.update(kw)
    return base


class GlossaryTest(unittest.TestCase):
    def test_protocol_tokens(self):
        units, n = glossary.translate_units("@ALLCALL CQ N0CALL: MSG ACK")
        self.assertEqual(n, 4)
        self.assertIn("all stations[@ALLCALL]", units)
        self.assertIn("message[MSG]", units)
        self.assertIn("acknowledged[ACK]", units)
        self.assertIn("N0CALL:", units)          # callsigns untouched

    def test_trailing_punctuation_kept(self):
        text, _ = glossary.translate("SNR? GRID?")
        self.assertEqual(text, "signal report[SNR]? locator[GRID]?")

    def test_repeat_expanded_once(self):
        text, n = glossary.translate("CQ CQ CQ")
        self.assertEqual(text, "calling all stations[CQ] CQ CQ")
        self.assertEqual(n, 1)

    def test_other_groups_untouched(self):
        text, n = glossary.translate("@GHOSTNET @APRSIS @TTP")
        self.assertEqual(n, 0)
        self.assertEqual(text, "@GHOSTNET @APRSIS @TTP")

    def test_plain_words_untouched(self):
        text, n = glossary.translate("NO TO IN ID ON THE YOU")
        self.assertEqual(n, 0)

    def test_q_signals_and_classic_shorthand(self):
        text, _ = glossary.translate("TNX FER QSO 73 GL BURO")
        self.assertIn("thanks[TNX]", text)
        self.assertIn("best regards[73]", text)
        self.assertIn("bureau[BURO]", text)

    def test_replace_mode_has_no_brackets(self):
        text, _ = glossary.translate("TNX OM", mode="replace")
        self.assertEqual(text, "thanks old man")

    def test_low_coherence_is_left_alone(self):
        units, n = glossary.translate_units("TNX OM", min_coherence=45,
                                            coherence_fn=lambda t: 10)
        self.assertEqual(n, 0)
        self.assertEqual(units, ["TNX", "OM"])

    def test_below_threshold_keeps_raw_text(self):
        r = rec("TNX OM", coherence=10)
        line = records.render(r, expand="gloss", expand_min_coh=45)[0]
        self.assertIn("TNX OM", line)
        self.assertNotIn("thanks", line)


class RenderExpansionTest(unittest.TestCase):
    def test_units_stay_together_when_wrapping(self):
        long_text = "WORD " * 30 + "TNX FER THE QSO"
        r = rec(long_text)
        lines = records.render(r, width=150, expand="gloss")
        self.assertTrue(len(lines) > 1)
        joined = " ".join(lines)
        self.assertIn("thanks[TNX]", joined)
        self.assertNotIn("thanks\nTNX", joined)
        for line in lines:
            self.assertLessEqual(len(line), 150)

    def test_raw_text_survives(self):
        r = rec("TNX FER QSO")
        records.render(r, expand="gloss")
        self.assertEqual(r["text"], "TNX FER QSO")           # never overwritten
        self.assertEqual(r["text_xlated"], "thanks[TNX] for[FER] contact[QSO]")
        self.assertEqual(r["expansions"], 3)

    def test_off_leaves_everything(self):
        r = rec("TNX FER QSO")
        line = records.render(r, expand="off")[0]
        self.assertTrue(line.endswith("TNX FER QSO"))
        self.assertNotIn("text_xlated", r)

    def test_fragment_marker_survives_expansion(self):
        r = rec("TNX FER THE QSO", fragment=True)
        line = records.render(r, width=150, expand="gloss")[-1]
        self.assertTrue(line.rstrip().endswith("\u2026"))


class LegendAndColumnsTest(unittest.TestCase):
    def test_legend_written_once_per_new_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = config.ensure_dirs(config.paths(tmp))
            cfg = dict(config.DEFAULTS)
            sink = records.Sink(p, cfg)
            r = rec("TNX OM", kind="activity")
            sink.write(r, records.render(r, expand="gloss"))
            sink.write(r, records.render(r, expand="gloss"))
            sink.close()
            lines = open(os.path.join(p["logs"], "js8call-logger-2026-11-25.txt"),
                         encoding="utf-8").read().splitlines()
            self.assertEqual(sum(1 for ln in lines if ln.startswith("meaning[TOKEN]")), 1)
            self.assertEqual(len(lines), 3)      # legend + two records

    def test_translated_text_in_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = config.ensure_dirs(config.paths(tmp))
            sink = records.Sink(p, dict(config.DEFAULTS))
            r = rec("TNX OM", kind="activity")
            sink.write(r, records.render(r, expand="gloss"))
            sink.close()
            import sqlite3
            db = sqlite3.connect(os.path.join(p["logs"], "js8call-logger.db"))
            text, xlated, n = db.execute(
                "SELECT text, text_xlated, expansions FROM records").fetchone()
            self.assertEqual(text, "TNX OM")
            self.assertEqual(xlated, "thanks[TNX] old man[OM]")
            self.assertEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
