"""Scoring a decode against what was expected (morse-logger's measure)."""

import json
import os
import shutil
import tempfile
import unittest

from js8call_logger import cli


class SimilarityTest(unittest.TestCase):
    def test_identical_is_one(self):
        self.assertAlmostEqual(cli._similarity("K2ROG ACK", "K2ROG ACK"), 1.0)

    def test_case_and_punctuation_do_not_matter(self):
        self.assertAlmostEqual(cli._similarity("K2ROG ACK", "k2rog ack\u2662"),
                               1.0)

    def test_wrapped_text_still_scores(self):
        expected = "@ALLCALL CQ N0CALL: @ALLCALL CQ CQ CQ EM48"
        got = "@ALLCALL CQ N0CALL: @ALLCALL CQ CQ CQ EM"
        self.assertGreater(cli._similarity(expected, got), 0.8)

    def test_unrelated_text_scores_low(self):
        self.assertLess(cli._similarity("K2ROG ACK", "KD2UWR HEARTBEAT"), 0.5)

    def test_empty_expected_is_zero(self):
        self.assertEqual(cli._similarity("", "anything"), 0.0)


class LastTextTest(unittest.TestCase):
    """The live self test reports the newest record, not the oldest in the window."""

    def _home(self, texts):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        logs = os.path.join(d, "logs")
        os.makedirs(logs)
        with open(os.path.join(logs, "js8call-logger.jsonl"), "w",
                  encoding="utf-8") as fh:
            for text in texts:
                fh.write(json.dumps({"text": text}) + "\n")
        return {"logs": logs}

    def test_without_expectation_the_newest_record_wins(self):
        p = self._home(["OLDEST CALL", "MIDDLE", "NEWEST CALL"])
        self.assertEqual(cli._last_text(p), "NEWEST CALL")

    def test_with_expectation_the_best_match_wins(self):
        p = self._home(["unrelated traffic", "@ALLCALL CQ N0CALL CQ CQ EM48"])
        self.assertIn("CQ", cli._last_text(p, "@ALLCALL CQ N0CALL CQ CQ EM48"))

    def test_missing_log_is_empty_not_an_error(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertEqual(cli._last_text({"logs": d}), "")
