"""Scoring a decode against what was expected (morse-logger's measure)."""

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
