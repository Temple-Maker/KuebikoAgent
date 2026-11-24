"""``kuebiko scan TARGET``: the rule scan and deterministic checks (contracts/cli.md)."""

from __future__ import annotations

import csv
import functools
import http.server
import json
import re
import secrets
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from kuebiko import __version__, ledger
from kuebiko.contracts import contracts_dir, finding_schema

EXIT_OK, EXIT_THRESHOLD, EXIT_USAGE, EXIT_RUNTIME = 0, 1, 2, 3
MAX_PAGES = 300


def add_arguments(parser) -> None:
    from kuebiko.checks.scan import SEVERITY_RANK

    parser.add_argument("target", metavar="TARGET", help="a URL, or a corpus key such as alphagov/accessibility-tool-audit")
    parser.add_argument("--steps", metavar="FILE", help="JSON array of steps to replay after each page loads")
    parser.add_argument("--criteria", metavar="LIST", help="comma-separated criterion numbers to report, e.g. 1.1.1,4.1.2")
    parser.add_argument("--fail-on", metavar="SEVERITY", choices=list(SEVERITY_RANK),
                        help="exit 1 when a finding is at or above this severity")


def _first_line(error: Exception) -> str:
    return (getattr(error, "message", None) or str(error)).splitlines()[0]


def _err(message: str) -> None:
    print(f"kuebiko scan: {message}", file=sys.stderr)


def _load_steps(path: str | None) -> list[dict]:
    if not path:
        return []
    steps = json.loads(Path(path).read_text(encoding="utf-8"))
    schema = {"type": "array", "items": finding_schema()["$defs"]["step"]}
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(steps)]
    if errors:
        raise ValueError("; ".join(errors))
    return steps


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def _corpus_target(key: str) -> tuple[Path, dict]:
    """Local clone and target fields for a corpus key. Raises LookupError with the fix."""
    root = contracts_dir().parent
    rows = {r["repo"]: r for r in csv.DictReader((root / "corpus" / "manifest.csv").open(encoding="utf-8"))}
    if key not in rows:
        raise LookupError(f"{key!r} is neither a URL nor a corpus key in corpus/manifest.csv")
    clone = root / ".corpus" / key
    if not clone.is_dir():
        raise LookupError(f"{clone} not found; run: kuebiko corpus fetch --tier {rows[key]['tier']}")
    if not (clone / "index.html").is_file():
        # shortcut: only static corpus items are served here; booted apps come from workstream 1's runner.
        raise LookupError(f"{key} is not a static site; start it and pass its URL instead")
    head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True)
    commit = head.stdout.strip() if head.returncode == 0 else None
    if commit and commit != rows[key]["commit"]:
        _err(f"warning: {key} is at {commit}, not the pinned {rows[key]['commit']}")
    return clone, {"app": key, "repo": key, "commit": commit}


def run(args) -> int:
    from kuebiko.checks import scan

    try:
        steps = _load_steps(args.steps)
    except (OSError, ValueError) as error:
        _err(f"--steps: {error}")
        return EXIT_USAGE
    criteria = None
    if args.criteria:
        criteria = {c.strip() for c in args.criteria.split(",") if c.strip()}
        if bad := [c for c in criteria if not re.fullmatch(r"[1-4]\.\d{1,2}\.\d{1,2}", c)]:
            _err(f"--criteria: not a criterion number: {', '.join(sorted(bad))}")
            return EXIT_USAGE

    server = None
    try:
        if "://" in args.target:
            start, crawl = args.target, False
            target = {"app": urlsplit(args.target).netloc[:100] or "target", "repo": None, "commit": None}
        else:
            clone, target = _corpus_target(args.target)
            server = http.server.ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(clone)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            start, crawl = f"http://127.0.0.1:{server.server_port}/", True
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
        scan.load_assets()
    except (LookupError, FileNotFoundError) as error:
        _err(str(error))
        return EXIT_RUNTIME
    except ImportError:
        _err("Playwright is not installed; run: python -m pip install playwright && playwright install chromium")
        return EXIT_RUNTIME

    started_at = ledger.now()
    run_id = f"run_{started_at.replace('-', '').replace(':', '')}_{secrets.token_hex(2)}"
    run_dir = Path(args.out) / run_id
    (run_dir / "artifacts").mkdir(parents=True)
    summary = {"run_id": run_id, "kind": "scan", "app": target["app"], "url": start, "profile": "wcag22-aa",
               "tool_version": __version__, "model": None, "started_at": started_at, "finished_at": None,
               "cost_usd": 0.0, "criteria": []}
    conn = ledger.connect(args.ledger)
    ledger.record_run(conn, summary | {"repo": target["repo"], "commit": target["commit"]})

    records: dict[str, dict] = {}
    pages: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(bypass_csp=True)  # so the checks can inject their scripts
            scanner = scan.Scanner(context, run_id=run_id, target=target, steps=steps, run_dir=run_dir)
            queue, queued = [start], {start}
            while queue:
                url = queue.pop(0)
                try:
                    found, links = scanner.scan(url)
                except (PlaywrightError, scan.PageNotLoaded) as error:
                    if not pages:
                        raise
                    _err(f"skipped {url}: {_first_line(error)}")
                    continue
                pages.append(url)
                records.update((r["id"], r) for r in found)
                if not args.quiet and not args.json:
                    print(f"{len(found):4d} findings  {url}", file=sys.stderr)
                for link in links if crawl else []:
                    link = re.sub(r"/index\.html?$", "/", link)  # the static server answers both the same
                    if link not in queued and len(queued) < MAX_PAGES and re.search(r"(/|\.html?)$", urlsplit(link).path):
                        queued.add(link)
                        queue.append(link)
            browser.close()
    except (PlaywrightError, scan.PageNotLoaded) as error:
        _err(f"could not scan {start}: {_first_line(error)}")
        return EXIT_RUNTIME
    finally:
        if server:
            server.shutdown()

    kept = [r for r in records.values() if criteria is None or r["criterion"]["id"] in criteria]
    with (run_dir / "findings.jsonl").open("w", encoding="utf-8") as out:
        for record in kept:
            ledger.upsert_finding(conn, record)  # validates against the contract
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    (run_dir / "artifacts" / "pages.json").write_text(json.dumps(pages, indent=1), encoding="utf-8")

    summary["criteria"] = _criteria(scanner.rules, kept, criteria)
    summary["finished_at"] = ledger.now()
    ledger.record_run(conn, summary | {"repo": target["repo"], "commit": target["commit"]})
    (run_dir / "run.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary))
    elif not args.quiet:
        print(f"{len(kept)} findings on {len(pages)} pages. Run written to {run_dir}")
    if args.fail_on and any(scan.SEVERITY_RANK[r["severity"]] >= scan.SEVERITY_RANK[args.fail_on] for r in kept):
        return EXIT_THRESHOLD
    return EXIT_OK


def _criteria(rules: dict[str, list[tuple[str, str]]], records: list[dict], only: set[str] | None) -> list[dict]:
    """Per-criterion results for run.json.

    Rule checks can only show a barrier, never its absence, so a criterion whose
    checks found nothing is ``needs-person``, not ``pass``. Criteria no check
    covers are left out, which reads as ``not-checked``.
    """
    rows: dict[str, dict] = {}
    for check_id, crits in rules.items():
        for cid, level in crits:
            row = rows.setdefault(cid, {"id": cid, "level": level, "checks": set(), "finding_ids": set()})
            row["checks"].add(check_id)
    for r in records:
        rows[r["criterion"]["id"]]["finding_ids"].add(r["id"])
    key = lambda cid: tuple(int(x) for x in cid.split("."))  # noqa: E731
    return [
        {"id": row["id"], "level": row["level"], "result": "fail" if row["finding_ids"] else "needs-person",
         "checks": sorted(row["checks"]), "finding_ids": sorted(row["finding_ids"])}
        for cid, row in sorted(rows.items(), key=lambda kv: key(kv[0]))
        if only is None or cid in only
    ]
