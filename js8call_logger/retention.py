"""Retention: archive or delete what is older than N days.

Nothing is removed unless you ask: `retention_days` defaults to 0, which
means keep everything. When you do ask, the default is to *archive* - the old
text is gzipped into `<home>/archive/` rather than deleted - because a log you
pruned last month is often the one you want this month. `--delete` removes
instead of archiving, for people who really mean it.

What is covered: the daily text files, the per-correspondent and per-group
conversation files, the JSONL (rewritten, old lines archived) and the SQLite
rows. The canonical JSONL and database are both pruned to the same cutoff, so
the two views never disagree.
"""

from __future__ import annotations

import glob
import gzip
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta


def _archive_dir(paths: dict) -> str:
    d = os.path.join(paths["home"], "archive")
    os.makedirs(d, exist_ok=True)
    return d


def _gzip_file(src: str, dest_dir: str) -> int:
    size = os.path.getsize(src)
    name = os.path.basename(src)
    dest = os.path.join(dest_dir, name + ".gz")
    tmp = dest + ".part"
    with open(src, "rb") as fin, gzip.open(tmp, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    os.replace(tmp, dest)
    return size


def _daily_date(name: str):
    """2026-11-25 from js8call-logger-2026-11-25.txt, or None."""
    if not (name.startswith("js8call-logger-") and name.endswith(".txt")):
        return None
    try:
        return datetime.strptime(name[len("js8call-logger-"):-4], "%Y-%m-%d")
    except ValueError:
        return None


def prune(paths: dict, days: int, dry_run: bool = False,
          delete: bool = False) -> dict:
    """Remove or archive everything older than `days`. Returns a summary."""
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    arch = None if dry_run else _archive_dir(paths)
    out = {"days": days, "cutoff": cutoff.strftime("%Y-%m-%d"),
           "files": 0, "bytes": 0, "jsonl_lines": 0, "rows": 0,
           "notes": []}

    def handle(path: str):
        """Archive or delete one file that is old enough."""
        out["files"] += 1
        out["bytes"] += os.path.getsize(path)
        if not dry_run:
            if delete:
                os.remove(path)
            else:
                _gzip_file(path, arch)
                os.remove(path)
        return True

    # 1. Daily text files, dated by name (an edit does not make them young).
    for path in sorted(glob.glob(os.path.join(paths["logs"], "js8call-logger-*.txt"))):
        day = _daily_date(os.path.basename(path))
        if day is not None and day < cutoff:
            handle(path)

    # 2. Conversation files: callsigns at the top, groups in a subdirectory.
    #    These are cumulative, so their age is their last write.
    for pattern in ("*.txt", os.path.join("groups", "*.txt")):
        for path in sorted(glob.glob(os.path.join(paths["conversations"], pattern))):
            if os.path.getmtime(path) < cutoff_ts:
                handle(path)

    # 3. JSONL: one append-only file, so rewrite it and archive the old lines.
    jsonl = os.path.join(paths["logs"], "js8call-logger.jsonl")
    if os.path.exists(jsonl):
        keep, old = [], []
        with open(jsonl, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    ts = line.split('"ts": "', 1)[1][:10]
                    (old if ts < cutoff.strftime("%Y-%m-%d") else keep).append(line)
                except IndexError:
                    keep.append(line)          # unparsable: never silently drop
        if old:
            out["jsonl_lines"] = len(old)
            out["bytes"] += os.path.getsize(jsonl)
            if not dry_run:
                dest = os.path.join(arch, f"js8call-logger-{cutoff:%Y-%m-%d}.jsonl.gz")
                with gzip.open(dest + ".part", "wt", encoding="utf-8") as fh:
                    fh.writelines(old)
                os.replace(dest + ".part", dest)
                tmp = jsonl + ".part"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.writelines(keep)
                os.replace(tmp, jsonl)

    # 4. SQLite: same cutoff, then give the space back.
    db_path = os.path.join(paths["logs"], "js8call-logger.db")
    if os.path.exists(db_path):
        db = sqlite3.connect(db_path)
        try:
            n = db.execute("SELECT COUNT(*) FROM records WHERE ts < ?",
                           (cutoff.strftime("%Y-%m-%d"),)).fetchone()[0]
            out["rows"] = n
            if n and not dry_run:
                db.execute("DELETE FROM records WHERE ts < ?",
                           (cutoff.strftime("%Y-%m-%d"),))
                db.commit()
                db.execute("VACUUM")
        finally:
            db.close()

    out["delete"] = bool(delete)
    out["dry_run"] = bool(dry_run)
    return out


def _state_path(paths: dict) -> str:
    return os.path.join(paths["run"], "prune.json")


def maybe_auto_prune(paths: dict, cfg: dict) -> dict | None:
    """Run prune at start when the schedule says it is due.

    Called before the daemon opens its log files, because pruning rewrites the
    JSONL and a running process holding it open would lose whatever it writes
    next. That is also why the daemon cannot do this while it runs.
    """
    if not cfg.get("prune_auto"):
        return None
    days = int(cfg.get("retention_days") or 0)
    if days <= 0:
        return None
    interval = float(cfg.get("prune_interval_h") or 24) * 3600
    state = _state_path(paths)
    try:
        with open(state, encoding="utf-8") as f:
            last = float(json.load(f).get("last") or 0)
    except (OSError, ValueError):
        last = 0.0
    if time.time() - last < interval:
        return None
    result = prune(paths, days)
    os.makedirs(paths["run"], exist_ok=True)
    tmp = state + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"last": time.time(), "days": days,
                   "files": result["files"], "rows": result["rows"]}, f)
    os.replace(tmp, state)
    return result


def summary_text(result: dict) -> str:
    verb = "deleted" if result["delete"] else "archived"
    lines = [
        f"cutoff       {result['cutoff']} ({result['days']} days)",
        f"files        {result['files']} {verb}",
        f"jsonl lines  {result['jsonl_lines']} {verb}",
        f"db rows      {result['rows']} {'deleted' if not result['dry_run'] else 'would delete'}",
        f"bytes        {result['bytes']}",
    ]
    if result["dry_run"]:
        lines.insert(0, "dry run - nothing was touched")
    return "\n".join(lines)
