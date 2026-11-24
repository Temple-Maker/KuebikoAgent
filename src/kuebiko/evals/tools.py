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


def run_command(command: str, url: str, case_dir: Path, base_dir: Path, model: str | None = None,
                max_cost: float | None = None) -> tuple[list[dict], float]:
    """Run one command on ``url`` and return its validated findings and its cost in USD.

    ``case_dir`` must sit under ``base_dir/artifacts/``: evidence paths in the returned
    findings are rewritten to be relative to ``base_dir``, the eval's run directory.
    """
    argv = [
        sys.executable, "-m", "kuebiko.cli", command, url,
        "--out", str(case_dir),
        # Eval pages are not targets; keep them out of the findings ledger.
        "--ledger", str(case_dir / "ledger.db"),
        "--quiet",
    ]
    if command == "audit":
        argv += ["--model", model] if model else []
        argv += ["--max-cost", f"{max_cost:.6f}"] if max_cost is not None else []
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=CASE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise CaseError(f"kuebiko {command} timed out after {CASE_TIMEOUT_SECONDS}s", _cost(case_dir)) from error
    if proc.returncode == EXIT_NOT_IMPLEMENTED:
        raise NotImplementedTool(proc.stderr.strip() or f"kuebiko {command} exits 4")
    if proc.returncode not in (0, 1):  # 1 only means a --fail-on threshold was crossed
        raise CaseError(f"kuebiko {command} exited {proc.returncode}: {proc.stderr.strip()[-500:]}", _cost(case_dir))
    run_dir = _run_dir(case_dir)
    missing = [name for name in ("findings.jsonl", "run.json") if not (run_dir / name).is_file()]
    if missing:
        raise CaseError(f"kuebiko {command} wrote no {' or '.join(missing)} in {run_dir}", _cost(case_dir))
    cost = _cost(case_dir)
    prefix = run_dir.relative_to(base_dir).as_posix()
    findings = []
    findings_file = run_dir / "findings.jsonl"
    for number, line in enumerate(findings_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as error:
            raise CaseError(f"{findings_file}:{number} is not JSON: {error}", cost) from error
        errors = finding_errors(record)
        if errors:
            raise CaseError(f"{findings_file}:{number} is not a valid finding: {errors[0]}", cost)
        for item in record["evidence"]:
            if "path" in item:
                item["path"] = f"{prefix}/{item['path']}"
        findings.append(record)
    return findings, cost


def _run_dir(case_dir: Path) -> Path:
    runs = sorted(case_dir.glob("run_*/"))
    if len(runs) != 1:
        raise CaseError(f"expected one run directory in {case_dir}, found {len(runs)}")
    return runs[0]


def _cost(case_dir: Path) -> float:
    """The cost the run recorded, or 0.0 when it recorded none, including after a failure."""
    try:
        summary = json.loads((_run_dir(case_dir) / "run.json").read_text(encoding="utf-8"))
        return float(summary.get("cost_usd") or 0.0)
    except (CaseError, OSError, ValueError):
        return 0.0
