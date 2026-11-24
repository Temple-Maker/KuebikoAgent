"""``kuebiko eval SUITE [--baseline scan|instructions] [--limit N] [--model NAME] [--max-cost USD]``

Scores a tool or baseline on a labelled suite and writes ``eval.json`` into a
run directory, in the shape contracts/cli.md gives. Exits 1 when a floor in
``evals/thresholds.toml`` is missed.
"""

from __future__ import annotations

import json
import secrets
import sys
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from kuebiko import __version__
from kuebiko.evals import act, instructions, scoring, tools

SUITES = ("act-subset", "act", "apg-seeded", "todomvc-seeded", "a11ybench-lite")
IMPLEMENTED = ("act-subset",)
THRESHOLDS = act.REPO_ROOT / "evals" / "thresholds.toml"
FLOOR_KEYS = ("min_recall", "max_false_discovery_rate")
DEFAULT_MAX_COST = 15.0
WORKERS = {"scan": 4, "audit": 2, "instructions": 8}

EXIT_OK, EXIT_THRESHOLD, EXIT_USAGE, EXIT_RUNTIME, EXIT_NOT_IMPLEMENTED = 0, 1, 2, 3, 4


def add_arguments(parser) -> None:
    parser.add_argument("suite", choices=SUITES, metavar="SUITE", help=" | ".join(SUITES))
    parser.add_argument("--baseline", choices=("scan", "instructions"),
                        help="score a baseline instead of kuebiko audit")
    parser.add_argument("--limit", type=int, metavar="N", help="score N evenly spaced cases; floors are not checked")
    parser.add_argument("--model", metavar="NAME", help=f"model for audit or the instructions baseline "
                        f"(instructions default: {instructions.DEFAULT_MODEL})")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST, metavar="USD",
                        help=f"stop with exit 3 before spend would pass this (default {DEFAULT_MAX_COST})")


class BudgetExceeded(RuntimeError):
    pass


class Skipped(Exception):
    """A queued case that did not start because the eval had already stopped."""


class Budget:
    """Each case reserves its worst-case cost before it starts; a case whose reserve would pass the cap never starts."""

    def __init__(self, cap: float):
        self.cap, self.spent, self.reserved = cap, 0.0, 0.0
        self._lock = threading.Lock()

    def reserve(self, worst_case: float) -> None:
        with self._lock:
            if self.spent + self.reserved + worst_case > self.cap:
                raise BudgetExceeded(f"the next case could pass --max-cost {self.cap:.2f} USD (spent {self.spent:.2f})")
            self.reserved += worst_case

    def settle(self, worst_case: float, cost: float) -> None:
        with self._lock:
            self.reserved -= worst_case
            self.spent += cost


def load_floors(suite: str, kind: str, path: Path = THRESHOLDS) -> dict[str, float]:
    floors = tomllib.loads(path.read_text(encoding="utf-8")).get(suite, {}).get(kind, {})
    unknown = set(floors) - set(FLOOR_KEYS)
    if unknown:
        raise ValueError(f"{path}: unknown keys in [{suite}.{kind}]: {', '.join(sorted(unknown))}")
    return floors


def missed_floors(result: dict, floors: dict[str, float]) -> list[str]:
    missed = []
    recall, fdr = result["recall"], result["false_discovery_rate"]
    if "min_recall" in floors and (recall is None or recall < floors["min_recall"]):
        missed.append(f"recall {recall} is below the floor {floors['min_recall']}")
    if "max_false_discovery_rate" in floors and fdr is not None and fdr > floors["max_false_discovery_rate"]:
        missed.append(f"false-discovery rate {fdr} is above the limit {floors['max_false_discovery_rate']}")
    return missed


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(args) -> int:
    say = (lambda *a: None) if (args.quiet or args.json) else (lambda *a: print(*a, file=sys.stderr))
    if args.suite not in IMPLEMENTED:
        print(f"kuebiko eval {args.suite} is not implemented yet; only {', '.join(IMPLEMENTED)} is.", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    if args.limit is not None and args.limit < 1:
        print("--limit must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    kind = args.baseline or "audit"
    model = args.model
    if kind == "instructions":
        model = model or instructions.DEFAULT_MODEL
        if model not in instructions.PRICES:
            print(f"no price known for {model}; add it to PRICES in {instructions.__file__}", file=sys.stderr)
            return EXIT_USAGE
    elif kind == "scan" and model:
        print("--model does not apply to the scan baseline", file=sys.stderr)
        return EXIT_USAGE
    try:
        floors = load_floors(args.suite, kind)
        root = act.checkout()
        client = instructions.make_client() if kind == "instructions" else None
    except (ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return EXIT_RUNTIME

    commit = act.pinned_commit()
    cases = act.load_cases(args.suite, root, args.limit)
    started = _now()
    run_id = f"run_{started.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(2)}"
    run_dir = Path(args.out) / run_id
    run_dir.mkdir(parents=True)
    budget = Budget(args.max_cost)
    # An audit gets an even share of the cap as its own --max-cost, so that share is its worst case.
    audit_cap = args.max_cost / len(cases)

    # Queued cases see this and return at once. Cancelling futures instead would hang
    # as_completed, which never yields a future cancelled before it ran.
    halt = threading.Event()

    def one(case: act.Case, base: str) -> tuple[list[dict], float, str | None]:
        if halt.is_set():
            raise Skipped
        url = base + case.relative_path
        if kind == "instructions":
            html = (root / act.ASSETS / case.relative_path).read_text(encoding="utf-8", errors="replace")
            worst_case = instructions.worst_case_cost(model, html)
        else:
            worst_case = audit_cap if kind == "audit" else 0.0
        budget.reserve(worst_case)
        findings, cost, error = [], 0.0, None
        try:
            if kind == "instructions":
                failures, cost = instructions.ask(client, model, html)
                findings = instructions.to_findings(
                    failures, run_id=run_id, url=url, app=act.ACT_REPO, repo=act.ACT_REPO, commit=commit, model=model
                )
            else:
                # Under artifacts/ so the nested runs' evidence paths stay valid from this run directory.
                case_dir = run_dir / "artifacts" / "cases" / case.rule_id / case.testcase_id
                findings, cost = tools.run_command(
                    kind, url, case_dir, run_dir, model, audit_cap if kind == "audit" else None
                )
        except tools.CaseError as failure:
            cost, error = failure.cost_usd, str(failure)
        finally:
            budget.settle(worst_case, cost)
        return findings, cost, error

    results: dict[str, tuple[list[dict], float, str | None]] = {}
    stopped: str | None = None
    say(f"eval {args.suite} {kind}: {len(cases)} cases -> {run_dir}")
    try:
        with act.serve(root) as base:
            pool = ThreadPoolExecutor(WORKERS[kind])
            try:
                futures = {pool.submit(one, case, base): case for case in cases}
                for future in as_completed(futures):
                    try:
                        results[futures[future].key] = future.result()
                    except Skipped:
                        continue
                    except Exception as error:  # budget, model or tool failure: start no more cases
                        halt.set()
                        stopped = stopped or str(error)
                        if isinstance(error, tools.NotImplementedTool):
                            raise
                        continue
                    if len(results) % 25 == 0:
                        say(f"  {len(results)}/{len(cases)} cases, {budget.spent:.2f} USD")
            finally:
                pool.shutdown(cancel_futures=True)
    except tools.NotImplementedTool as error:
        print(f"kuebiko {kind} is not implemented yet, so it cannot be scored: {error}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    finished = _now()

    done = [case for case in cases if case.key in results]
    cost = round(sum(r[1] for r in results.values()), 4)
    found = {key: scoring.failed_criteria(r[0]) for key, r in results.items()}
    write_run_files(run_dir, run_id, done, results, found, model, started, finished, cost)
    if stopped:
        # No eval.json: numbers over a partial set would read as a score.
        print(f"eval stopped after {len(done)} of {len(cases)} cases, {cost} USD spent: {stopped}", file=sys.stderr)
        print(f"finished cases are in {run_dir / 'cases.jsonl'}; no eval.json was written", file=sys.stderr)
        return EXIT_RUNTIME

    report = {
        "suite": args.suite,
        "baseline": args.baseline,
        "corpus_commit": commit,
        **scoring.score(cases, found),
        "seconds": round((finished - started).total_seconds(), 1),
        "cost_usd": cost,
        "model": model,
    }
    (run_dir / "eval.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2))
    say(f"recall {report['recall']}  false-discovery rate {report['false_discovery_rate']}  "
        f"(TP {report['true_positives']}, FP {report['false_positives']}, FN {report['false_negatives']})  "
        f"{report['cost_usd']} USD, {report['seconds']} s")
    say(f"wrote {run_dir / 'eval.json'}")

    errors = {key: r[2] for key, r in results.items() if r[2]}
    if errors:
        for key, message in sorted(errors.items()):
            print(f"case {key} failed: {message}", file=sys.stderr)
        print(f"{len(errors)} cases could not be run and count as not flagged", file=sys.stderr)
        return EXIT_RUNTIME
    if args.limit:
        say("--limit given: floors not checked")
        return EXIT_OK
    missed = missed_floors(report, floors)
    for line in missed:
        print(f"floor missed: {line}", file=sys.stderr)
    return EXIT_THRESHOLD if missed else EXIT_OK


def write_run_files(run_dir: Path, run_id: str, done: list[act.Case], results: dict, found: dict,
                    model: str | None, started: datetime, finished: datetime, cost: float) -> None:
    """Write findings.jsonl, cases.jsonl and run.json for the cases that finished."""
    with (run_dir / "findings.jsonl").open("w", encoding="utf-8") as handle:
        for case in done:
            for record in results[case.key][0]:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (run_dir / "cases.jsonl").open("w", encoding="utf-8") as handle:
        for case in done:
            handle.write(json.dumps({
                "rule_id": case.rule_id,
                "testcase_id": case.testcase_id,
                "expected": case.expected,
                "criteria": sorted(case.criteria),
                "reported": sorted(found[case.key]),
                "flagged": bool(case.criteria & found[case.key]),
                "cost_usd": round(results[case.key][1], 6),
                "error": results[case.key][2],
            }) + "\n")
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id,
        "kind": "eval",
        "app": act.ACT_REPO,
        "url": None,
        "profile": "wcag22-aa",
        "tool_version": __version__,
        "model": model,
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "cost_usd": cost,
        "criteria": [],
    }, indent=2) + "\n", encoding="utf-8")
