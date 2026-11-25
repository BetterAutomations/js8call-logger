"""Fold legacy flat GROUP*.txt files into conversations/groups/.

Before group conversations were split by group, the logger wrote one flat file
per addressee and named groups ``GROUPALLCALL.txt``. This merges each of those
into ``groups/ALLCALL.txt``, in date order, without duplicating lines, and
removes the old file only after the new one is written.

Run it while the logger is stopped: the daemon keeps conversation files open,
so a rewrite underneath it would lose whatever it writes to the old inode.
"""

from __future__ import annotations

import os
import re

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _stamp(line: str) -> str:
    m = TS_RE.match(line)
    return m.group(1) if m else ""


def _read_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def fold_legacy_groups(paths: dict, dry_run: bool = False) -> list[dict]:
    """Merge every legacy GROUP*.txt into groups/<GROUP>.txt. Returns a report."""
    conv = paths["conversations"]
    if not os.path.isdir(conv):
        return []
    groups_dir = os.path.join(conv, "groups")
    report: list[dict] = []
    for name in sorted(os.listdir(conv)):
        upper = name.upper()
        if not (upper.startswith("GROUP") and name.endswith(".txt")):
            continue
        src = os.path.join(conv, name)
        if not os.path.isfile(src):
            continue
        group = re.sub(r"[^A-Z0-9]", "", upper[len("GROUP"):-len(".txt")])
        if not group:
            continue
        dst = os.path.join(groups_dir, f"{group}.txt")

        old = _read_lines(src)
        new = _read_lines(dst)
        seen = set()
        merged = []
        for line in sorted(old + new, key=_stamp):
            if line in seen:          # the transition window can overlap
                continue
            seen.add(line)
            merged.append(line)

        report.append({"from": name, "to": f"groups/{group}.txt",
                       "lines": len(merged), "added": len(merged) - len(new),
                       "duplicates": len(old) + len(new) - len(merged)})
        if dry_run:
            continue
        os.makedirs(groups_dir, exist_ok=True)
        tmp = dst + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(merged) + ("\n" if merged else ""))
        os.replace(tmp, dst)
        os.remove(src)
    return report


def report_text(report: list[dict], dry_run: bool) -> str:
    if not report:
        return "no legacy GROUP*.txt files to fold"
    lines = []
    for row in report:
        lines.append(f"{row['from']} -> {row['to']}  "
                     f"{row['added']} line(s) added, {row['lines']} total"
                     + (f", {row['duplicates']} duplicate(s) dropped"
                        if row["duplicates"] else ""))
    if dry_run:
        lines.insert(0, "dry run - nothing was touched")
    else:
        lines.append(f"folded {len(report)} file(s); the old ones are gone")
    return "\n".join(lines)
