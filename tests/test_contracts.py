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


def test_fingerprint_keeps_hash_routes_apart() -> None:
    record = load(ROOT / "contracts" / "examples" / "rule-confirmed.json")
    cart, checkout = json.loads(json.dumps(record)), json.loads(json.dumps(record))
    cart["target"]["url"] = "http://localhost:8080/#/cart"
    checkout["target"]["url"] = "http://localhost:8080/#/checkout"
    assert contracts.finding_id(cart) != contracts.finding_id(checkout)


def with_change(change) -> dict:
    """The agent-judged example after one change, with its id recomputed."""
    record = load(ROOT / "contracts" / "examples" / "agent-judged.json")
    change(record)
    record["id"] = contracts.finding_id(record)
    return record


def add_step(step: dict):
    return lambda record: record["state"]["steps"].append(step)


def add_artifact(path: str):
    return lambda record: record["evidence"].append({"kind": "log", "path": path})


@pytest.mark.parametrize(
    "change",
    [
        add_step({"action": "goto", "url": "//outside.example/page"}),
        add_step({"action": "goto", "url": "/\\outside.example/page"}),
        add_step({"action": "goto"}),
        add_step({"action": "click"}),
        add_step({"action": "fill", "selector": "input#email"}),
        add_step({"action": "press"}),
        add_step({"action": "wait"}),
        add_artifact("artifacts/../../outside.txt"),
        add_artifact("artifacts/shots/../../outside.txt"),
        add_artifact("artifacts/.."),
        add_artifact("artifacts\\..\\outside.txt"),
        add_artifact("artifacts/"),
    ],
)
def test_unsafe_or_unreplayable_records_are_rejected(change) -> None:
    assert contracts.finding_errors(with_change(change))


@pytest.mark.parametrize(
    "change",
    [
        add_step({"action": "goto", "url": "/cart?step=2#/review"}),
        add_step({"action": "press", "key": "Tab"}),
        add_step({"action": "wait", "ms": 500}),
        add_artifact("artifacts/shots/v1..v2.png"),
    ],
)
def test_safe_records_still_validate(change) -> None:
    assert contracts.finding_errors(with_change(change)) == []


def test_tester_block_cannot_hold_contact_details() -> None:
    record = load(ROOT / "contracts" / "examples" / "tester-session.json")
    for field in ("name", "email", "handle", "phone"):
        with_field = json.loads(json.dumps(record))
        with_field["tester"][field] = "anything"
        assert contracts.finding_errors(with_field), field


def record_with_tester_text(**text: str) -> dict:
    """The tester-session example with its setup, task or a comment replaced."""
    record = load(ROOT / "contracts" / "examples" / "tester-session.json")
    if "setup" in text:
        record["tester"]["setup"] = [text["setup"]]
    if "task" in text:
        record["tester"]["task"] = text["task"]
    if "comment" in text:
        record["evidence"].append({"kind": "tester-comment", "text": text["comment"]})
    return record  # tester text is not part of the fingerprint, so the id still holds


@pytest.mark.parametrize(
    "text",
    [
        {"comment": "Stuck here. Email me at jane.doe@example.com"},
        {"comment": "Call 617-555-0100 if you need more"},
        {"comment": "WhatsApp +44 20 7946 0958"},
        {"setup": "JAWS on Windows, reach me at j@example.org"},
        {"task": "Book an appointment, then text (617) 555 0100"},
    ],
)
def test_contact_details_in_tester_text_are_rejected(text: dict) -> None:
    assert contracts.finding_errors(record_with_tester_text(**text))


@pytest.mark.parametrize(
    "text",
    [
        {"setup": "NVDA 2026.3.1 on Windows 11"},
        {"setup": "400% zoom"},
        {"comment": "Took 3 tries and about 45 seconds to find the submit button"},
        {"task": "Book an appointment for 10:30 on 2026-12-01"},
    ],
)
def test_ordinary_tester_text_still_validates(text: dict) -> None:
    assert contracts.finding_errors(record_with_tester_text(**text)) == []


@pytest.fixture
def ledger():
    """An empty ledger holding one run, one finding and two sessions, one synthetic."""
    connection = sqlite3.connect(":memory:")
    connection.executescript(contracts.ledger_sql())
    connection.executescript(
        """
        INSERT INTO runs (run_id, kind, app, tool_version, started_at)
          VALUES ('run_test01', 'scan', 'example/shop', '0.0.1', '2026-11-21T10:15:00Z');
        INSERT INTO findings (id, first_run_id, last_run_id, app, page, criterion_id, criterion_level,
          check_id, source, outcome, confidence, severity, summary, record_json, created_at, updated_at)
          VALUES ('fnd_0123456789abcdef', 'run_test01', 'run_test01', 'example/shop', '/', '1.1.1', 'A',
          'axe:image-alt', 'rule', 'failed', 'confirmed', 'serious', 'Image has no text alternative.',
          '{}', '2026-11-21T10:15:00Z', '2026-11-21T10:15:00Z');
        INSERT INTO sessions (session_code, app, task, setup_json, result, synthetic, consent, created_at)
          VALUES ('ses_realtester1', 'example/shop', 'Pay', '["NVDA on Windows"]', 'completed', 0, 1,
          '2026-11-21T11:00:00Z'),
                 ('ses_scripted001', 'example/shop', 'Pay', '["keyboard only"]', 'completed', 1, 1,
          '2026-11-21T11:00:00Z');
        """
    )
    yield connection
    connection.close()


def add_verification(connection: sqlite3.Connection, kind: str, session_code: str | None) -> None:
    connection.execute(
        "INSERT INTO verifications (finding_id, kind, result, session_code, created_at) "
        "VALUES ('fnd_0123456789abcdef', ?, 'pass', ?, '2026-11-21T12:00:00Z')",
        (kind, session_code),
    )


@pytest.mark.parametrize("session_code", [None, "ses_scripted001"])
def test_tester_verification_needs_a_real_session(session_code: str | None, ledger: sqlite3.Connection) -> None:
    connection = ledger
    with pytest.raises(sqlite3.IntegrityError):
        add_verification(connection, "tester-session", session_code)


def test_tester_verification_cannot_be_switched_to_a_synthetic_session(ledger: sqlite3.Connection) -> None:
    connection = ledger
    add_verification(connection, "tester-session", "ses_realtester1")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE verifications SET session_code = 'ses_scripted001'")


def test_real_and_synthetic_sessions_are_recorded_as_their_own_kind(ledger: sqlite3.Connection) -> None:
    connection = ledger
    add_verification(connection, "tester-session", "ses_realtester1")
    add_verification(connection, "scripted-session", "ses_scripted001")
    add_verification(connection, "rescan", None)
    assert connection.execute("SELECT COUNT(*) FROM verifications").fetchone()[0] == 3


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
