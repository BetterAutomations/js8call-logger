"""Tests for retention: archive by default, delete on request."""

import gzip
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from js8call_logger import config, records, retention

OLD = "2026-01-01"
NEW = datetime.now().strftime("%Y-%m-%d")


def build(home):
    p = config.ensure_dirs(config.paths(home))
    with open(os.path.join(p["logs"], f"js8call-logger-{OLD}.txt"), "w") as fh:
        fh.write("old line\n")
    with open(os.path.join(p["logs"], f"js8call-logger-{NEW}.txt"), "w") as fh:
        fh.write("new line\n")
    conv = os.path.join(p["conversations"], "K1ABC.txt")
    with open(conv, "w") as fh:
        fh.write("old conversation\n")
    old_ts = time.time() - 60 * 86400
    os.utime(conv, (old_ts, old_ts))
    groups = os.path.join(p["conversations"], "groups")
    os.makedirs(groups, exist_ok=True)
    with open(os.path.join(groups, "SITREP.txt"), "w") as fh:
        fh.write("fresh group\n")

    with open(os.path.join(p["logs"], "js8call-logger.jsonl"), "w") as fh:
        fh.write(json.dumps({"ts": f"{OLD} 12:00:00", "text": "old"}) + "\n")
        fh.write(json.dumps({"ts": f"{NEW} 12:00:00", "text": "new"}) + "\n")

    db = sqlite3.connect(os.path.join(p["logs"], "js8call-logger.db"))
    db.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, ts TEXT, text TEXT)")
    db.execute("INSERT INTO records (ts, text) VALUES (?, 'old')", (f"{OLD} 12:00:00",))
    db.execute("INSERT INTO records (ts, text) VALUES (?, 'new')", (f"{NEW} 12:00:00",))
    db.commit()
    db.close()
    return p


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.p = build(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_changes_nothing(self):
        before = sorted(os.listdir(self.p["logs"]))
        result = retention.prune(self.p, 30, dry_run=True)
        self.assertGreaterEqual(result["files"], 2)
        self.assertEqual(sorted(os.listdir(self.p["logs"])), before)
        self.assertFalse(os.path.exists(os.path.join(self.home, "archive")))

    def test_archive_is_the_default(self):
        result = retention.prune(self.p, 30)
        logs = os.listdir(self.p["logs"])
        self.assertIn(f"js8call-logger-{NEW}.txt", logs)
        self.assertNotIn(f"js8call-logger-{OLD}.txt", logs)
        self.assertNotIn("K1ABC.txt", os.listdir(self.p["conversations"]))
        self.assertIn("SITREP.txt",
                      os.listdir(os.path.join(self.p["conversations"], "groups")))
        arch = os.listdir(os.path.join(self.home, "archive"))
        self.assertIn(f"js8call-logger-{OLD}.txt.gz", arch)
        self.assertIn("K1ABC.txt.gz", arch)
        self.assertIn(f"js8call-logger-{result['cutoff']}.jsonl.gz", arch)
        with gzip.open(os.path.join(self.home, "archive",
                                   f"js8call-logger-{OLD}.txt.gz"), "rt") as fh:
            self.assertEqual(fh.read(), "old line\n")

    def test_jsonl_keeps_only_new_lines(self):
        retention.prune(self.p, 30)
        lines = open(os.path.join(self.p["logs"], "js8call-logger.jsonl")).read().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("new", lines[0])

    def test_database_keeps_only_new_rows(self):
        retention.prune(self.p, 30)
        db = sqlite3.connect(os.path.join(self.p["logs"], "js8call-logger.db"))
        rows = db.execute("SELECT ts, text FROM records").fetchall()
        db.close()
        self.assertEqual(rows, [(f"{NEW} 12:00:00", "new")])

    def test_delete_removes_instead_of_archiving(self):
        result = retention.prune(self.p, 30, delete=True)
        self.assertTrue(result["delete"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "archive",
                                                     "K1ABC.txt.gz")))
        self.assertNotIn(f"js8call-logger-{OLD}.txt", os.listdir(self.p["logs"]))

    def test_nothing_old_is_a_no_op(self):
        result = retention.prune(self.p, 3650)
        self.assertEqual(result["files"], 0)
        self.assertEqual(result["jsonl_lines"], 0)
        self.assertEqual(result["rows"], 0)

    def test_summary_is_readable(self):
        text = retention.summary_text(retention.prune(self.p, 30, dry_run=True))
        self.assertIn("dry run", text)
        self.assertIn("cutoff", text)


if __name__ == "__main__":
    unittest.main()
