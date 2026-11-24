#!/usr/bin/env python3
"""Build the fixed 300-case tuning subset of the W3C ACT test cases.

Usage:
    python scripts/make_act_subset.py PATH_TO_TESTCASES_JSON

PATH_TO_TESTCASES_JSON is ``content-assets/wcag-act-rules/testcases.json`` in a
checkout of w3c/wcag-act-rules at the commit pinned in ``corpus/manifest.csv``.

The subset is 150 cases expected to fail and 150 expected to pass. Selection is
deterministic: HTML cases only, rules approved by W3C first, then one case per
rule in turn (rules and cases in id order) until each class has 150. Running it
again on the same file writes the same rows.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

PER_CLASS = 150
OUT = Path(__file__).resolve().parents[1] / "corpus" / "act-subset.csv"
FIELDS = ["testcase_id", "rule_id", "expected", "approved", "relative_path", "url"]


def pick(cases: list[dict], expected: str) -> list[dict]:
    """Round-robin over rules, approved rules first, until PER_CLASS cases."""
    chosen: list[dict] = []
    for approved in (True, False):
        by_rule: dict[str, list[dict]] = defaultdict(list)
        for case in cases:
            if case["expected"] == expected and bool(case.get("approved")) is approved:
                by_rule[case["ruleId"]].append(case)
        for rule_cases in by_rule.values():
            rule_cases.sort(key=lambda c: c["testcaseId"])
        depth = 0
        while len(chosen) < PER_CLASS and any(len(v) > depth for v in by_rule.values()):
            for rule_id in sorted(by_rule):
                if len(by_rule[rule_id]) > depth and len(chosen) < PER_CLASS:
                    chosen.append(by_rule[rule_id][depth])
            depth += 1
    if len(chosen) < PER_CLASS:
        raise SystemExit(f"only {len(chosen)} {expected} cases available, need {PER_CLASS}")
    return chosen


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    cases = [c for c in data["testcases"] if c["relativePath"].endswith(".html")]
    rows = pick(cases, "failed") + pick(cases, "passed")
    rows.sort(key=lambda c: (c["ruleId"], c["expected"], c["testcaseId"]))
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for case in rows:
            writer.writerow(
                {
                    "testcase_id": case["testcaseId"],
                    "rule_id": case["ruleId"],
                    "expected": case["expected"],
                    "approved": "yes" if case.get("approved") else "no",
                    "relative_path": case["relativePath"],
                    "url": case["url"],
                }
            )
    print(f"wrote {len(rows)} rows to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
