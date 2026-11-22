"""The contracts accept what they should and reject what they should."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kuebiko import contracts

ROOT = Path(__file__).resolve().parents[1]
VALID = sorted((ROOT / "contracts" / "examples").glob("*.json"))
INVALID = sorted((ROOT / "tests" / "fixtures" / "invalid-findings").glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_examples_are_valid(path: Path) -> None:
    assert contracts.finding_errors(load(path)) == []
    contracts.validate_finding(load(path))


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_fixtures_are_rejected(path: Path) -> None:
    assert contracts.finding_errors(load(path))
    with pytest.raises(ValueError):
        contracts.validate_finding(load(path))


def test_fingerprint_ignores_host_and_port() -> None:
    record = load(ROOT / "contracts" / "examples" / "rule-confirmed.json")
    moved = json.loads(json.dumps(record))
    moved["target"]["url"] = "https://staging.example.org:4443/checkout"
    assert contracts.finding_id(moved) == contracts.finding_id(record)


def test_fingerprint_changes_with_the_element() -> None:
    record = load(ROOT / "contracts" / "examples" / "rule-confirmed.json")
    other = json.loads(json.dumps(record))
    other["element"]["selector"] = "form#payment > button.other"
    assert contracts.finding_id(other) != contracts.finding_id(record)


def test_tester_block_cannot_hold_contact_details() -> None:
    record = load(ROOT / "contracts" / "examples" / "tester-session.json")
    for field in ("name", "email", "handle", "phone"):
        with_field = json.loads(json.dumps(record))
        with_field["tester"][field] = "anything"
        assert contracts.finding_errors(with_field), field


def test_ledger_loads_and_enforces_status() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(contracts.ledger_sql())
    connection.execute(
        "INSERT INTO runs (run_id, kind, app, tool_version, started_at) "
        "VALUES ('run_test01', 'scan', 'example/shop', '0.0.1', '2026-11-21T10:15:00Z')"
    )
    record = load(ROOT / "contracts" / "examples" / "rule-confirmed.json")
    insert = (
        "INSERT INTO findings (id, first_run_id, last_run_id, app, page, selector, criterion_id, "
        "criterion_level, check_id, source, outcome, confidence, severity, summary, record_json, "
        "status, created_at, updated_at) VALUES (?, 'run_test01', 'run_test01', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    values = [
        record["id"],
        record["target"]["app"],
        contracts.page_of(record["target"]["url"]),
        record["element"]["selector"],
        record["criterion"]["id"],
        record["criterion"]["level"],
        record["check"]["id"],
        record["check"]["source"],
        record["outcome"],
        record["confidence"],
        record["severity"],
        record["summary"],
        json.dumps(record),
        "open",
        record["created_at"],
        record["created_at"],
    ]
    connection.execute(insert, values)
    with pytest.raises(sqlite3.IntegrityError):
        bad = list(values)
        bad[0] = "fnd_ffffffffffffffff"
        bad[13] = "compliant"
        connection.execute(insert, bad)
    connection.close()
