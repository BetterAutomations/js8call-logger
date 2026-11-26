#!/usr/bin/env python3
"""Every verb must be documented, and every documented verb must exist.

Run after adding a subcommand:  python3 tools/audit_verbs.py   (exit 1 on gap)
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from js8call_logger import cli                             # noqa: E402

MENTION = re.compile(r"js8call-logger\s+([a-z][a-z0-9-]+)")


def main() -> int:
    ap = cli.build_parser()
    sub = next(a for a in ap._actions
               if a.__class__.__name__ == "_SubParsersAction")
    verbs = sorted(sub.choices.keys())
    helps = {a.dest: (a.help or "") for a in sub._choices_actions}
    NOT_VERBS = {"launcher"}
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    problems = []

    for name in verbs:
        if not helps.get(name):
            problems.append(f"{name}: no help text")
        if not re.search(rf"js8call-logger\s+{re.escape(name)}\b", readme):
            problems.append(f"{name}: not mentioned in README.md")

    for mention in sorted(set(MENTION.findall(readme))):
        if mention not in sub.choices and mention not in NOT_VERBS:
            problems.append(f"{mention}: documented in README but not a verb")

    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print("  " + p)
        return 1
    print(f"{len(verbs)} verbs, all documented, nothing stale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
