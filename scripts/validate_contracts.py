#!/usr/bin/env python3
"""Check that the contracts, their examples and the corpus files are consistent.

Usage:
    python scripts/validate_contracts.py

Exits 0 when everything holds and 1 otherwise, printing each failure. CI runs
this on every pull request.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from touhou import cli, contracts  # noqa: E402

EXPECTED_TABLES = {"schema_meta", "runs", "findings", "fixes", "sessions", "verifications"}
TIERS = {
    "A-act-test-cases",
    "A-known-good-widgets",
    "A-planted-barriers",
    "A-negative-control",
    "C-one-app-many-frameworks",
    "D-core",
    "D-wide",
}

failures: list[str] = []
passed = 0


def check(condition: bool, message: str) -> None:
    global passed
    if condition:
        passed += 1
    else:
        failures.append(message)


def check_schema() -> None:
    try:
        contracts._finding_validator()
    except Exception as error:  # a broken schema must fail loudly, whatever the cause
        failures.append(f"finding.schema.json is not a valid JSON Schema: {error}")
        return
    check(
        contracts.finding_schema()["properties"]["schema_version"]["const"] == contracts.SCHEMA_VERSION,
        "finding.schema.json schema_version does not match touhou.contracts.SCHEMA_VERSION",
    )


def check_examples() -> None:
    valid = sorted((ROOT / "contracts" / "examples").glob("*.json"))
    check(len(valid) >= 3, "contracts/examples needs at least three valid finding records")
    sources = set()
    for path in valid:
        record = json.loads(path.read_text(encoding="utf-8"))
        errors = contracts.finding_errors(record)
        check(not errors, f"{path.relative_to(ROOT)} should be valid: {errors}")
        sources.add(record.get("check", {}).get("source"))
    check(sources >= {"rule", "agent", "tester"}, "examples must cover rule, agent and tester findings")

    invalid = sorted((ROOT / "tests" / "fixtures" / "invalid-findings").glob("*.json"))
    check(len(invalid) >= 5, "tests/fixtures/invalid-findings needs at least five records")
    for path in invalid:
        record = json.loads(path.read_text(encoding="utf-8"))
        check(
            bool(contracts.finding_errors(record)),
            f"{path.relative_to(ROOT)} should be rejected but was accepted",
        )


def check_ledger() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(contracts.ledger_sql())
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        check(tables == EXPECTED_TABLES, f"ledger tables are {sorted(tables)}, expected {sorted(EXPECTED_TABLES)}")
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        check(
            version is not None and version[0] == contracts.SCHEMA_VERSION,
            "ledger schema_version does not match touhou.contracts.SCHEMA_VERSION",
        )
        # The enumerations in the ledger must accept every value the finding schema allows.
        properties = contracts.finding_schema()["properties"]
        ledger_sql = contracts.ledger_sql()
        for field in ("outcome", "confidence", "severity"):
            for value in properties[field]["enum"]:
                check(f"'{value}'" in ledger_sql, f"ledger.sql does not allow {field} value {value!r}")
        for value in properties["check"]["properties"]["source"]["enum"]:
            check(f"'{value}'" in ledger_sql, f"ledger.sql does not allow source value {value!r}")
    except sqlite3.Error as error:
        failures.append(f"ledger.sql does not load in SQLite: {error}")
    finally:
        connection.close()


def check_manifest() -> None:
    path = ROOT / "corpus" / "manifest.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        check(reader.fieldnames == ["repo", "commit", "tier"], "manifest.csv header must be repo,commit,tier")
        rows = list(reader)
    repos = [row["repo"] for row in rows]
    check(len(repos) == len(set(repos)), "manifest.csv lists a repository twice")
    for row in rows:
        check(
            re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", row["repo"]) is not None,
            f"manifest.csv: {row['repo']!r} is not owner/name",
        )
        check(
            re.fullmatch(r"[0-9a-f]{40}", row["commit"]) is not None,
            f"manifest.csv: {row['repo']} is not pinned to a full commit hash",
        )
        check(row["tier"] in TIERS, f"manifest.csv: {row['repo']} has unknown tier {row['tier']!r}")
    check(
        sum(row["tier"] == "D-core" for row in rows) == 6,
        "manifest.csv must have exactly six D-core applications",
    )


def check_act_subset() -> None:
    path = ROOT / "corpus" / "act-subset.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    failed = sum(row["expected"] == "failed" for row in rows)
    passed_cases = sum(row["expected"] == "passed" for row in rows)
    check(len(rows) == 300, f"act-subset.csv has {len(rows)} rows, expected 300")
    check(failed == 150 and passed_cases == 150, "act-subset.csv must be 150 failed and 150 passed")
    keys = [(row["rule_id"], row["testcase_id"]) for row in rows]
    check(len(keys) == len(set(keys)), "act-subset.csv repeats a rule and test case pair")


def check_cli_contract() -> None:
    text = (ROOT / "contracts" / "cli.md").read_text(encoding="utf-8")
    table = text.split("## Commands", 1)[1].split("##", 1)[0]
    documented = set(re.findall(r"^\| `touhou ([a-z]+)` \|", table, flags=re.MULTILINE))
    implemented = set(cli.COMMANDS)
    check(
        documented == implemented,
        f"contracts/cli.md lists {sorted(documented)} but src/touhou/cli.py has {sorted(implemented)}",
    )
    for name in implemented:
        check(f"## touhou {name}" in text, f"contracts/cli.md has no section for 'touhou {name}'")


def main() -> int:
    for step in (check_schema, check_examples, check_ledger, check_manifest, check_act_subset, check_cli_contract):
        step()
    if failures:
        print(f"{len(failures)} problem(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"contracts OK ({passed} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
