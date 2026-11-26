"""The band survey: frequency parsing, scoring, and the dwell loop."""

import json
import os
import tempfile
import time
import unittest

from js8call_logger import scan


class FreqTest(unittest.TestCase):
    def test_mhz_and_khz_give_the_same_hz(self):
        self.assertEqual(scan.parse_freqs("7.078 7078"), [7078000, 7078000])


class ScoreTest(unittest.TestCase):
    def test_per_minute_and_median_snr(self):
        recs = [{"snr": -10}, {"snr": -20}, {"snr": -14}]
        r = scan.sample_row(7078000, recs, 120)
        self.assertEqual(r["decodes"], 3)
        self.assertAlmostEqual(r["per_min"], 1.5)
        self.assertEqual(r["snr"], -14)
        self.assertEqual(r["best_snr"], -10)

    def test_empty_band_scores_zero_without_snr(self):
        r = scan.sample_row(3578000, [], 60)
        self.assertEqual(r["per_min"], 0.0)
        self.assertIsNone(r["snr"])

    def test_rank_prefers_decodes_then_snr(self):
        rows = [scan.sample_row(21078000, [{"snr": -5}], 60),
                scan.sample_row(14078000, [{"snr": -8}, {"snr": -9}], 60),
                scan.sample_row(7078000, [{"snr": -3}, {"snr": -4}], 60)]
        self.assertEqual([r["dial"] for r in scan.rank(rows)],
                         [7078000, 14078000, 21078000])


class ReaderTest(unittest.TestCase):
    def test_filters_by_time_and_dial(self):
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 5))
        old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 600))
        rows = [{"ts": stamp, "dial": 7078000, "snr": -10},
                {"ts": stamp, "dial": 14078000, "snr": -10},
                {"ts": old, "dial": 7078000, "snr": -10}]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.jsonl")
            with open(p, "w") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
            got = scan.records_since(p, 7078000, now - 30)
        self.assertEqual(len(got), 1)


class SurveyTest(unittest.TestCase):
    def test_retunes_each_and_keeps_rows(self):
        seen = []

        class Api:
            def set_freq(self, hz, off):
                seen.append(hz)

            def close(self):
                pass

        rows = scan.survey(Api(), lambda hz, t0: [{"snr": -12}],
                           dwell_s=1, freqs=[7078000, 14078000],
                           log=lambda *a: None, sleep=lambda s: None)
        self.assertEqual(seen, [7078000, 14078000])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["decodes"], 1)

    def test_a_dead_receiver_skips_that_band(self):
        class Api:
            def set_freq(self, hz, off):
                if hz > 10000000:
                    raise RuntimeError("rig says no")

        rows = scan.survey(Api(), lambda hz, t0: [],
                           dwell_s=1, freqs=[7078000, 14078000],
                           log=lambda *a: None, sleep=lambda s: None)
        self.assertEqual([r["dial"] for r in rows], [7078000])
