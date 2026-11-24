"""kuebiko scan: axe mapping and records offline, plus one browser scan of a local page when Chromium is installed."""

from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

from kuebiko import cli, ledger
from kuebiko.checks import scan
from kuebiko.checks.cli import _criteria, _load_steps
from kuebiko.contracts import validate_finding

BASE = {"run_id": "run_20261124T000000Z_abcd", "target": {"app": "fixture", "repo": None, "commit": None},
        "url": "http://127.0.0.1:9/page.html", "steps": [], "viewport": scan.VIEWPORT}

VIOLATION = {
    "id": "image-alt", "impact": "critical", "help": "Images must have alternative text",
    "tags": ["cat.text-alternatives", "wcag2a", "wcag111", "section508"],
    "nodes": [{"selector": "main > img", "html": "<img src=\"x.png\">", "failureSummary": "Fix any of: add alt",
               "any": [{"data": None}]}],
}


def test_criteria_of_reads_number_and_level() -> None:
    assert scan.criteria_of(["wcag2a", "wcag111"]) == [("1.1.1", "A")]
    assert scan.criteria_of(["wcag21aa", "wcag1410"]) == [("1.4.10", "AA")]
    assert scan.criteria_of(["wcag2a", "wcag244", "wcag412"]) == [("2.4.4", "A"), ("4.1.2", "A")]
    assert scan.criteria_of(["wcag2a", "wcag411"]) == []  # obsolete in WCAG 2.2
    assert scan.criteria_of(["best-practice"]) == []


def test_axe_records_are_valid_rule_findings() -> None:
    rules = {"image-alt": {"actIds": ["23a2a8"]}}
    [record] = scan.axe_records([VIOLATION], rules, {"main > img": "image"}, axe_version="4.14.1", **BASE)
    validate_finding(record)
    assert record["check"] == {"id": "axe:image-alt", "source": "rule", "tool": "axe-core",
                               "tool_version": "4.14.1", "act_rule": "23a2a8"}
    assert record["severity"] == "blocker"
    assert {"kind": "sr-transcript", "text": "image"} in record["evidence"]


@pytest.mark.parametrize("check_id", sorted(scan.KUEBIKO_CHECKS))
def test_kuebiko_records_are_valid(check_id: str) -> None:
    element = {"selector": "main > a", "html": "<a href=\"#\">x</a>"}
    record = scan.kuebiko_record(check_id, element, [{"kind": "log", "text": "measured"}], **BASE)
    validate_finding(record)
    assert record["criterion"]["id"] == scan.KUEBIKO_CHECKS[check_id][0]


def test_criteria_without_findings_need_a_person() -> None:
    rows = _criteria({"axe:image-alt": [("1.1.1", "A")], "kuebiko:keyboard-trap": [("2.1.2", "A")]},
                     [{"id": "fnd_0000000000000001", "criterion": {"id": "1.1.1"}}], None)
    assert [(r["id"], r["result"]) for r in rows] == [("1.1.1", "fail"), ("2.1.2", "needs-person")]
    assert _criteria({"axe:image-alt": [("1.1.1", "A")]}, [], {"4.1.2"}) == []


def test_steps_are_checked_against_the_contract(tmp_path) -> None:
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps([{"action": "press", "key": "Tab"}]))
    bad.write_text(json.dumps([{"action": "goto", "url": "https://elsewhere.example/"}]))
    assert _load_steps(str(good)) == [{"action": "press", "key": "Tab"}]
    with pytest.raises(ValueError):
        _load_steps(str(bad))


def test_usage_and_runtime_errors(tmp_path) -> None:
    common = ["--out", str(tmp_path / "runs"), "--ledger", str(tmp_path / "k.db")]
    assert cli.main(["scan", "http://127.0.0.1:9/", "--criteria", "1.1", *common]) == cli.EXIT_USAGE
    assert cli.main(["scan", "nobody/nothing", *common]) == cli.EXIT_RUNTIME


PAGE = """<!doctype html><html lang="en"><head><title>Fixture</title><style>
a:focus { outline: none }
.wide { width: 900px }
</style></head><body><main><h1>Fixture</h1>
<a href="#">No focus ring</a> <button>Fine</button> <button></button> <img src="x.png">
<div class="wide">wide box</div>
<a class="trap" href="#">trap</a> <a href="#">after</a>
<script>document.querySelector('.trap').addEventListener('keydown', e => e.preventDefault())</script>
</main></body></html>"""


@pytest.fixture
def served(tmp_path):
    pytest.importorskip("playwright")
    try:
        scan.load_assets()
    except FileNotFoundError as error:
        pytest.skip(str(error))
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text(PAGE)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path / "site"))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/index.html"
    server.shutdown()


def test_scan_finds_planted_barriers(served, tmp_path, capsys) -> None:
    code = cli.main(["scan", served, "--out", str(tmp_path / "runs"), "--ledger", str(tmp_path / "k.db"),
                     "--json", "--fail-on", "serious"])
    if code == cli.EXIT_RUNTIME:
        pytest.skip("Chromium is not installed: " + capsys.readouterr().err)
    summary = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_THRESHOLD
    run_dir = tmp_path / "runs" / summary["run_id"]
    records = [json.loads(line) for line in (run_dir / "findings.jsonl").read_text().splitlines()]
    for record in records:
        validate_finding(record)
    found = {(r["check"]["id"], r["element"]["selector"]) for r in records}
    assert {
        ("axe:image-alt", "body > main > img"),
        ("axe:button-name", "body > main > button:nth-of-type(2)"),
        ("kuebiko:focus-visible", "body > main > a:nth-of-type(1)"),
        ("kuebiko:keyboard-trap", "body > main > a:nth-of-type(2)"),
        ("kuebiko:reflow-320", "body > main > div"),
    } <= found
    assert ("kuebiko:focus-visible", "body > main > button:nth-of-type(1)") not in found  # default focus ring
    conn = ledger.connect(tmp_path / "k.db")
    assert conn.execute("SELECT count(*) FROM findings WHERE last_run_id = ?", (summary["run_id"],)).fetchone()[0] \
        == len(records)
    assert (run_dir / "artifacts" / "index.html" / "sr-transcript.txt").read_text().startswith("document")
