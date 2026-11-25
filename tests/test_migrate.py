"""Tests for folding legacy flat GROUP*.txt files into groups/."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from js8call_logger import config, migrate


def build(home):
    p = config.ensure_dirs(config.paths(home))
    conv = p["conversations"]
    with open(os.path.join(conv, "GROUPALLCALL.txt"), "w") as fh:
        fh.write("2026-11-25 09:15:27  old line one\n"
                 "2026-11-25 10:07:42  old line two\n")
    os.makedirs(os.path.join(conv, "groups"), exist_ok=True)
    with open(os.path.join(conv, "groups", "ALLCALL.txt"), "w") as fh:
        fh.write("2026-11-25 15:32:11  new line\n"
                 "2026-11-25 10:07:42  old line two\n")   # overlap
    with open(os.path.join(conv, "K1ABC.txt"), "w") as fh:
        fh.write("2026-11-25 11:00:00  not a group\n")
    return p


class FoldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = build(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, *parts):
        path = os.path.join(self.p["conversations"], *parts)
        return open(path, encoding="utf-8").read().splitlines()

    def test_dry_run_leaves_everything(self):
        report = migrate.fold_legacy_groups(self.p, dry_run=True)
        self.assertEqual(len(report), 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.p["conversations"], "GROUPALLCALL.txt")))
        self.assertEqual(len(self.read("groups", "ALLCALL.txt")), 2)

    def test_merges_in_date_order_without_duplicates(self):
        report = migrate.fold_legacy_groups(self.p)
        self.assertEqual(report[0]["from"], "GROUPALLCALL.txt")
        self.assertEqual(report[0]["to"], "groups/ALLCALL.txt")
        self.assertEqual(report[0]["duplicates"], 1)
        lines = self.read("groups", "ALLCALL.txt")
        self.assertEqual(lines, [
            "2026-11-25 09:15:27  old line one",
            "2026-11-25 10:07:42  old line two",
            "2026-11-25 15:32:11  new line",
        ])

    def test_legacy_file_is_removed(self):
        migrate.fold_legacy_groups(self.p)
        self.assertFalse(os.path.exists(
            os.path.join(self.p["conversations"], "GROUPALLCALL.txt")))

    def test_other_files_untouched(self):
        migrate.fold_legacy_groups(self.p)
        self.assertEqual(self.read("K1ABC.txt"),
                         ["2026-11-25 11:00:00  not a group"])

    def test_second_run_is_a_no_op(self):
        migrate.fold_legacy_groups(self.p)
        report = migrate.fold_legacy_groups(self.p)
        self.assertEqual(report, [])
        self.assertIn("no legacy", migrate.report_text(report, False))

    def test_report_text(self):
        text = migrate.report_text(migrate.fold_legacy_groups(self.p, dry_run=True), True)
        self.assertIn("dry run", text)
        self.assertIn("GROUPALLCALL.txt", text)


if __name__ == "__main__":
    unittest.main()
