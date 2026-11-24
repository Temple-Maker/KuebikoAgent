"""Run ``kuebiko scan`` or ``kuebiko audit`` on one page, through the command-line contract.

The eval talks to those commands only as contracts/cli.md describes them, so it
works the same whether or not their modules are importable here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from kuebiko.contracts import finding_errors

EXIT_NOT_IMPLEMENTED = 4
CASE_TIMEOUT_SECONDS = 600


class CaseError(RuntimeError):
    """One case could not be run. The case counts as not flagged and the eval exits 3."""

    def __init__(self, message: str, cost_usd: float = 0.0):
        super().__init__(message)
        self.cost_usd = cost_usd


class NotImplementedTool(RuntimeError):
    """The command under test exits 4."""


def run_command(command: str, url: str, case_dir: Path, model: str | None = None) -> tuple[list[dict], float]:
    """Run one command on ``url`` and return its validated findings and its cost in USD."""
    argv = [
        sys.executable, "-m", "kuebiko.cli", command, url,
        "--out", str(case_dir),
        # Eval pages are not targets; keep them out of the findings ledger.
        "--ledger", str(case_dir / "ledger.db"),
        "--quiet",
    ]
    if model and command == "audit":
        argv += ["--model", model]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=CASE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise CaseError(f"kuebiko {command} timed out after {CASE_TIMEOUT_SECONDS}s") from error
    if proc.returncode == EXIT_NOT_IMPLEMENTED:
        raise NotImplementedTool(proc.stderr.strip() or f"kuebiko {command} exits 4")
    if proc.returncode not in (0, 1):  # 1 only means a --fail-on threshold was crossed
        raise CaseError(f"kuebiko {command} exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    runs = sorted(case_dir.glob("run_*/"))
    if len(runs) != 1:
        raise CaseError(f"expected one run directory in {case_dir}, found {len(runs)}")
    findings = []
    findings_file = runs[0] / "findings.jsonl"
    if findings_file.exists():
        for number, line in enumerate(findings_file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            errors = finding_errors(record)
            if errors:
                raise CaseError(f"{findings_file}:{number} is not a valid finding: {errors[0]}")
            findings.append(record)
    cost = 0.0
    run_json = runs[0] / "run.json"
    if run_json.exists():
        cost = float(json.loads(run_json.read_text(encoding="utf-8")).get("cost_usd") or 0.0)
    return findings, cost
