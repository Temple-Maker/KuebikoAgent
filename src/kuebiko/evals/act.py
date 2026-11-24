"""The ACT suites: load labelled cases and serve their pages offline."""

from __future__ import annotations

import csv
import functools
import json
import os
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ACT_REPO = "w3c/wcag-act-rules"
ASSETS = Path("content-assets/wcag-act-rules")


@dataclass(frozen=True)
class Case:
    rule_id: str
    testcase_id: str
    expected: str  # "failed" or "passed"
    relative_path: str
    criteria: frozenset[str]  # success criteria the rule maps to, e.g. {"1.1.1"}

    @property
    def key(self) -> str:
        return f"{self.rule_id}/{self.testcase_id}"

    @property
    def positive(self) -> bool:
        return self.expected == "failed"


class CorpusError(RuntimeError):
    """The pinned checkout is missing or at the wrong commit."""


def pinned_commit() -> str:
    with (REPO_ROOT / "corpus" / "manifest.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["repo"] == ACT_REPO:
                return row["commit"]
    raise CorpusError(f"{ACT_REPO} is not in corpus/manifest.csv")


def checkout() -> Path:
    """Return the pinned ACT checkout, after checking its commit.

    shortcut: assumes workstream 1 clones to .corpus/OWNER/NAME; KUEBIKO_ACT_DIR
    overrides it. Switch to kuebiko.corpus's path helper once that is merged.
    """
    path = Path(os.environ.get("KUEBIKO_ACT_DIR") or REPO_ROOT / ".corpus" / ACT_REPO)
    if not (path / ASSETS / "testcases.json").is_file():
        raise CorpusError(
            f"no ACT checkout at {path}. Run `kuebiko corpus fetch --tier A-act-test-cases`, "
            "or set KUEBIKO_ACT_DIR to a checkout of w3c/wcag-act-rules."
        )
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    if head != pinned_commit():
        raise CorpusError(f"{path} is at {head or 'an unknown commit'}, expected {pinned_commit()}")
    return path


def criteria_of(requirements: dict | None) -> frozenset[str]:
    """Success criteria in a case's ruleAccessibilityRequirements, secondary ones included.

    Keys look like "wcag20:1.1.1"; technique and ARIA keys are not criteria.
    """
    return frozenset(
        key.split(":", 1)[1] for key in (requirements or {}) if key.startswith("wcag2")
    )


def load_cases(suite: str, root: Path, limit: int | None = None) -> list[Case]:
    if suite != "act-subset":
        raise ValueError(suite)
    data = json.loads((root / ASSETS / "testcases.json").read_text(encoding="utf-8"))
    by_key = {(c["ruleId"], c["testcaseId"]): c for c in data["testcases"]}
    with (REPO_ROOT / "corpus" / "act-subset.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    cases = [
        Case(
            rule_id=row["rule_id"],
            testcase_id=row["testcase_id"],
            expected=row["expected"],
            relative_path=row["relative_path"],
            criteria=criteria_of(by_key[(row["rule_id"], row["testcase_id"])].get("ruleAccessibilityRequirements")),
        )
        for row in rows
    ]
    if limit and limit < len(cases):
        # Evenly spaced, so a small run still covers many rules and both classes.
        cases = [cases[i * len(cases) // limit] for i in range(limit)]
    return cases


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def serve(root: Path) -> Iterator[str]:
    """Serve the ACT assets on 127.0.0.1 and yield the base URL, ending in a slash."""
    handler = functools.partial(_QuietHandler, directory=str(root / ASSETS))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
