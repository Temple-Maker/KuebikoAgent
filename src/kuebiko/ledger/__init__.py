"""The findings ledger: a SQLite file created from ``contracts/ledger.sql``.

Every workstream writes through these four functions::

    conn = ledger.connect("kuebiko.db")      # creates the schema on first use
    ledger.record_run(conn, run_summary)     # insert or update a run (run.json keys)
    ledger.upsert_finding(conn, record)      # validated, keyed by the fingerprint id
    ledger.set_status(conn, finding_id, "false-positive")

Records are validated with ``kuebiko.contracts.validate_finding``. Status
changes follow the rules written at the top of ``contracts/ledger.sql``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kuebiko.contracts import ledger_sql, page_of, validate_finding


def now() -> str:
    """UTC time in the ledger's form, for example 2026-11-21T10:15:00Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | Path = "kuebiko.db") -> sqlite3.Connection:
    """Open the ledger, creating the file and its schema when they do not exist."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
    ).fetchone()
    if not exists:
        conn.executescript(ledger_sql())
    return conn


def record_run(conn: sqlite3.Connection, summary: dict[str, Any]) -> None:
    """Insert a run, or update it when ``run_id`` is already there.

    ``summary`` uses the ``run.json`` keys from ``contracts/cli.md``; ``repo`` and
    ``commit`` are optional extras. Call it once when a run starts, so findings
    can refer to it, and again with the full summary when it finishes.
    """
    row = {
        "run_id": summary["run_id"],
        "kind": summary["kind"],
        "app": summary["app"],
        "target_url": summary.get("url"),
        "target_repo": summary.get("repo"),
        "target_commit": summary.get("commit"),
        "profile": summary.get("profile") or "wcag22-aa",
        "tool_version": summary["tool_version"],
        "model": summary.get("model"),
        "cost_usd": summary.get("cost_usd"),
        "started_at": summary["started_at"],
        "finished_at": summary.get("finished_at"),
        "summary_json": json.dumps(summary, sort_keys=True),
    }
    columns = ", ".join(row)
    updates = ", ".join(f"{c} = excluded.{c}" for c in row if c != "run_id")
    with conn:
        conn.execute(
            f"INSERT INTO runs ({columns}) VALUES ({', '.join(':' + c for c in row)}) "
            f"ON CONFLICT (run_id) DO UPDATE SET {updates}",
            row,
        )


def upsert_finding(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    """Store a finding record and return its id.

    A new id starts as ``open``. A known id keeps its status, first run and
    creation time; its latest record and last run are replaced.
    """
    validate_finding(record)
    element = record["element"]
    stamp = now()
    row = {
        "id": record["id"],
        "first_run_id": record["run_id"],
        "last_run_id": record["run_id"],
        "app": record["target"]["app"],
        "page": page_of(record["target"]["url"]),
        "selector": element["selector"] if element else "",
        "criterion_id": record["criterion"]["id"],
        "criterion_level": record["criterion"]["level"],
        "check_id": record["check"]["id"],
        "source": record["check"]["source"],
        "outcome": record["outcome"],
        "confidence": record["confidence"],
        "severity": record["severity"],
        "summary": record["summary"],
        "record_json": json.dumps(record, sort_keys=True, ensure_ascii=False),
        "created_at": stamp,
        "updated_at": stamp,
    }
    latest = ("last_run_id", "criterion_level", "source", "outcome", "confidence",
              "severity", "summary", "record_json", "updated_at")
    with conn:
        conn.execute(
            f"INSERT INTO findings ({', '.join(row)}) VALUES ({', '.join(':' + c for c in row)}) "
            f"ON CONFLICT (id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in latest)}",
            row,
        )
    return record["id"]


# What must already be in the ledger before a finding may take each status.
_STATUS_NEEDS = {
    "fix-proposed": (
        "SELECT 1 FROM fixes WHERE finding_id = ? AND state = 'proposed'",
        "a proposed fix",
    ),
    "fixed": (
        "SELECT 1 FROM fixes f JOIN verifications v ON v.fix_id = f.id "
        "WHERE f.finding_id = ? AND f.state = 'merged' AND v.kind = 'rescan' AND v.result = 'pass'",
        "a merged fix with a passing rescan",
    ),
    # Synthetic sessions never verify a finding (AGENTS.md rule 8).
    "verified": (
        "SELECT 1 FROM verifications v JOIN sessions s ON s.session_code = v.session_code "
        "WHERE v.finding_id = ? AND v.kind = 'tester-session' AND v.result = 'pass' AND s.synthetic = 0",
        "a passing session from a real tester",
    ),
}


def set_status(conn: sqlite3.Connection, finding_id: str, status: str) -> None:
    """Change a finding's status, refusing when the ledger does not back it up.

    ``open``, ``false-positive`` and ``wont-fix`` are a person's call and need
    nothing recorded first. Raises ``KeyError`` for an unknown id and
    ``ValueError`` for an unknown status or one the ledger does not support.
    """
    if status not in {"open", "false-positive", "wont-fix", *_STATUS_NEEDS}:
        raise ValueError(f"unknown status {status!r}")
    if not conn.execute("SELECT 1 FROM findings WHERE id = ?", (finding_id,)).fetchone():
        raise KeyError(finding_id)
    if status in _STATUS_NEEDS:
        query, needed = _STATUS_NEEDS[status]
        if not conn.execute(query, (finding_id,)).fetchone():
            raise ValueError(f"{finding_id} cannot be {status!r} without {needed} in the ledger")
    with conn:
        conn.execute(
            "UPDATE findings SET status = ?, updated_at = ? WHERE id = ?",
            (status, now(), finding_id),
        )
