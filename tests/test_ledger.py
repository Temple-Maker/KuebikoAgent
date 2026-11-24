"""The ledger creates its schema, stores validated findings and guards status changes."""

from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from kuebiko import ledger
from kuebiko.contracts import contracts_dir

RECORD = json.loads((contracts_dir() / "examples" / "rule-confirmed.json").read_text(encoding="utf-8"))
FID = RECORD["id"]


def run(run_id: str = RECORD["run_id"], **extra) -> dict:
    return {"run_id": run_id, "kind": "scan", "app": "example/shop", "url": "http://localhost:8080/",
            "tool_version": "0.0.1", "started_at": "2026-11-21T10:15:00Z", **extra}


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    conn = ledger.connect(tmp_path / "kuebiko.db")
    ledger.record_run(conn, run())
    return conn


def finding(conn, fid: str = FID) -> sqlite3.Row:
    return conn.execute("SELECT * FROM findings WHERE id = ?", (fid,)).fetchone()


def test_connect_creates_schema_once(tmp_path) -> None:
    path = tmp_path / "kuebiko.db"
    ledger.connect(path).close()
    conn = ledger.connect(path)  # second open must not re-run the schema
    assert conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == "0.1"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_record_run_updates_in_place(conn) -> None:
    ledger.record_run(conn, run(finished_at="2026-11-21T10:19:30Z", criteria=[]))
    row = conn.execute("SELECT * FROM runs").fetchall()
    assert len(row) == 1
    assert row[0]["finished_at"] == "2026-11-21T10:19:30Z"
    assert json.loads(row[0]["summary_json"])["criteria"] == []


def test_upsert_new_finding_is_open(conn) -> None:
    assert ledger.upsert_finding(conn, RECORD) == FID
    row = finding(conn)
    assert row["status"] == "open"
    assert row["page"] == "/checkout"
    assert row["selector"] == "form#payment > button.icon-only"
    assert json.loads(row["record_json"]) == RECORD


def test_upsert_known_finding_keeps_status_and_first_run(conn) -> None:
    ledger.upsert_finding(conn, RECORD)
    ledger.set_status(conn, FID, "wont-fix")
    ledger.record_run(conn, run("run_20261122T090000Z_c3d4"))
    again = copy.deepcopy(RECORD) | {"run_id": "run_20261122T090000Z_c3d4", "severity": "serious"}
    ledger.upsert_finding(conn, again)
    row = finding(conn)
    assert (row["first_run_id"], row["last_run_id"]) == (RECORD["run_id"], again["run_id"])
    assert (row["status"], row["severity"]) == ("wont-fix", "serious")
    assert conn.execute("SELECT count(*) FROM findings").fetchone()[0] == 1


def test_invalid_record_is_rejected_and_not_written(conn) -> None:
    bad = copy.deepcopy(RECORD) | {"confidence": "judged"}
    with pytest.raises(ValueError, match="invalid finding record"):
        ledger.upsert_finding(conn, bad)
    assert finding(conn) is None


def test_finding_needs_its_run(tmp_path) -> None:
    conn = ledger.connect(tmp_path / "kuebiko.db")
    with pytest.raises(sqlite3.IntegrityError):
        ledger.upsert_finding(conn, RECORD)


def test_set_status_rejects_unknown_id_and_status(conn) -> None:
    ledger.upsert_finding(conn, RECORD)
    with pytest.raises(KeyError):
        ledger.set_status(conn, "fnd_0000000000000000", "open")
    with pytest.raises(ValueError, match="unknown status"):
        ledger.set_status(conn, FID, "done")


def test_person_statuses_need_nothing(conn) -> None:
    ledger.upsert_finding(conn, RECORD)
    ledger.set_status(conn, FID, "false-positive")
    assert finding(conn)["status"] == "false-positive"


def add_fix(conn, state: str) -> int:
    return conn.execute(
        "INSERT INTO fixes (finding_id, repo, branch, state, created_at, updated_at) "
        "VALUES (?, 'example/shop', 'fix/x', ?, 'x', 'x')", (FID, state)).lastrowid


def test_fix_proposed_and_fixed_need_ledger_rows(conn) -> None:
    ledger.upsert_finding(conn, RECORD)
    with pytest.raises(ValueError, match="proposed fix"):
        ledger.set_status(conn, FID, "fix-proposed")
    add_fix(conn, "proposed")
    ledger.set_status(conn, FID, "fix-proposed")

    merged = add_fix(conn, "merged")
    with pytest.raises(ValueError, match="passing rescan"):
        ledger.set_status(conn, FID, "fixed")
    conn.execute("INSERT INTO verifications (finding_id, fix_id, kind, result, created_at) "
                 "VALUES (?, ?, 'rescan', 'pass', 'x')", (FID, merged))
    ledger.set_status(conn, FID, "fixed")
    assert finding(conn)["status"] == "fixed"


@pytest.mark.parametrize("synthetic, allowed", [(1, False), (0, True)])
def test_only_real_tester_sessions_verify(conn, synthetic: int, allowed: bool) -> None:
    ledger.upsert_finding(conn, RECORD)
    conn.execute("INSERT INTO sessions (session_code, app, task, setup_json, result, synthetic, consent, created_at) "
                 "VALUES ('ses_abcdefgh', 'example/shop', 'pay', '[\"NVDA on Windows\"]', 'completed', ?, 1, 'x')",
                 (synthetic,))
    conn.execute("INSERT INTO verifications (finding_id, kind, result, session_code, created_at) "
                 "VALUES (?, 'tester-session', 'pass', 'ses_abcdefgh', 'x')", (FID,))
    if allowed:
        ledger.set_status(conn, FID, "verified")
        assert finding(conn)["status"] == "verified"
    else:
        with pytest.raises(ValueError, match="real tester"):
            ledger.set_status(conn, FID, "verified")
