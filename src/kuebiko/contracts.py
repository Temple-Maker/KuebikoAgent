"""Load and enforce the contracts in the repository's ``contracts/`` directory.

Every workstream imports from here. Do not re-implement validation or the
finding fingerprint anywhere else.

The contracts are read from a source checkout, which is the only supported way
to run the project for now (``pip install -e .``).
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "0.1"

# An email address, or a phone number of ten or more digits. Ten keeps version
# numbers such as "NVDA 2026.3.1" out. Names cannot be caught this way.
_CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|\+?(?:\d[\s().-]{0,2}){9,}\d")


def contracts_dir() -> Path:
    """Return the ``contracts/`` directory of this checkout."""
    path = Path(__file__).resolve().parents[2] / "contracts"
    if not path.is_dir():
        raise FileNotFoundError(
            f"contracts directory not found at {path}; run from a source checkout"
        )
    return path


@lru_cache(maxsize=1)
def finding_schema() -> dict[str, Any]:
    """Return the finding record JSON Schema."""
    return json.loads((contracts_dir() / "finding.schema.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _finding_validator() -> Draft202012Validator:
    schema = finding_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def ledger_sql() -> str:
    """Return the SQL that creates an empty ledger."""
    return (contracts_dir() / "ledger.sql").read_text(encoding="utf-8")


def page_of(url: str) -> str:
    """Return the path, query and fragment of a URL, without scheme, host or port.

    The fragment stays because single-page apps route with it (`/#/cart`).
    """
    parts = urlsplit(url)
    page = parts.path or "/"
    if parts.query:
        page += "?" + parts.query
    if parts.fragment:
        page += "#" + parts.fragment
    return page


def finding_id(record: dict[str, Any]) -> str:
    """Return the fingerprint id for a finding record.

    The rule is written out in ``contracts/README.md``. The record does not
    need an ``id`` yet, so call this to fill one in.
    """
    element = record.get("element")
    selector = element["selector"] if element else ""
    steps = json.dumps(
        record["state"]["steps"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    parts = [
        record["target"]["app"],
        page_of(record["target"]["url"]),
        steps,
        selector,
        record["criterion"]["id"],
        record["check"]["id"],
    ]
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return "fnd_" + digest[:16]


def finding_errors(record: Any) -> list[str]:
    """Return every reason a record is not a valid finding. Empty means valid.

    Checks the JSON Schema, that tester text holds no contact details, and that
    ``id`` matches the fingerprint.
    """
    errors = [
        f"{'/'.join(str(p) for p in error.absolute_path) or '(record)'}: {error.message}"
        for error in sorted(_finding_validator().iter_errors(record), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        return errors
    errors += _contact_errors(record)
    if record["id"] != finding_id(record):
        errors.append(f"id: expected {finding_id(record)}, the fingerprint of this record")
    return errors


def _contact_errors(record: dict[str, Any]) -> list[str]:
    """Flag contact details in text a tester writes (AGENTS.md rule 7)."""
    texts = []
    if tester := record.get("tester"):
        texts += [(f"tester/setup/{i}", text) for i, text in enumerate(tester["setup"])]
        texts.append(("tester/task", tester["task"]))
    texts += [
        (f"evidence/{i}/text", item["text"])
        for i, item in enumerate(record["evidence"])
        if item["kind"] == "tester-comment" and "text" in item
    ]
    return [
        f"{path}: looks like an email address or phone number; testers stay anonymous"
        for path, text in texts
        if _CONTACT.search(text)
    ]


def validate_finding(record: Any) -> None:
    """Raise ``ValueError`` listing every problem if the record is not valid."""
    errors = finding_errors(record)
    if errors:
        raise ValueError("invalid finding record:\n  " + "\n  ".join(errors))
