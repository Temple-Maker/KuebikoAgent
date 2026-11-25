"""Unit tests for kuebiko eval. No network: the model client and the ACT checkout are faked."""

from __future__ import annotations

import csv
import json
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from kuebiko import cli
from kuebiko.contracts import finding_errors
from kuebiko.evals import act, instructions, scoring, tools
from kuebiko.evals import cli as eval_cli


def case(key: str, expected: str, *criteria: str) -> act.Case:
    rule, testcase = key.split("/")
    return act.Case(rule, testcase, expected, f"testcases/{key}.html", frozenset(criteria))


def test_criteria_of_keeps_only_wcag_criteria() -> None:
    requirements = {"wcag20:1.4.6": {}, "wcag-technique:G17": {}, "wcag21:1.4.12": {"secondary": "x"}, "aria12:x": {}}
    assert act.criteria_of(requirements) == {"1.4.6", "1.4.12"}
    assert act.criteria_of(None) == frozenset()


def test_score_counts_a_case_only_for_its_rules_criteria() -> None:
    cases = [
        case("r/1", "failed", "1.1.1"),  # flagged: true positive
        case("r/2", "failed", "1.1.1"),  # reported another criterion: false negative
        case("r/3", "passed", "1.1.1"),  # flagged: false positive
        case("r/4", "passed", "1.1.1"),  # nothing: true negative
        case("r/5", "failed"),           # rule maps to no criterion: can never be flagged
    ]
    found = {"r/1": {"1.1.1"}, "r/2": {"4.1.2"}, "r/3": {"1.1.1", "4.1.2"}, "r/5": {"1.1.1"}}
    result = scoring.score(cases, found)
    assert result == {
        "cases": 5, "positives": 3, "flagged": 2, "true_positives": 1, "false_positives": 1,
        "false_negatives": 2, "recall": 0.3333, "false_discovery_rate": 0.5, "per_criterion": {},
    }


def test_per_criterion_needs_enough_cases() -> None:
    cases = [case(f"r/{i}", "failed" if i % 2 else "passed", "4.1.2") for i in range(scoring.MIN_CASES_PER_CRITERION)]
    cases.append(case("s/1", "failed", "1.1.1"))
    result = scoring.score(cases, {c.key: {"4.1.2"} for c in cases})
    assert list(result["per_criterion"]) == ["4.1.2"]
    assert result["per_criterion"]["4.1.2"]["recall"] == 1.0
    assert result["per_criterion"]["4.1.2"]["false_discovery_rate"] == 0.5


def test_failed_criteria_ignores_cant_tell() -> None:
    findings = [{"outcome": "failed", "criterion": {"id": "1.1.1"}}, {"outcome": "cantTell", "criterion": {"id": "2.4.3"}}]
    assert scoring.failed_criteria(findings) == {"1.1.1"}


def test_model_answers_become_valid_findings() -> None:
    failures = [
        {"criterion": "1.1.1", "level": "A", "selector": "img", "severity": "serious", "summary": "The image has no text alternative."},
        {"criterion": "1.1.1", "level": "A", "selector": "img", "severity": "serious", "summary": "Same place reported twice."},
        {"criterion": "3.1.1", "level": "A", "selector": "", "severity": "moderate", "summary": "No lang"},
        {"criterion": "WCAG 1.1.1", "level": "A", "selector": "", "severity": "minor", "summary": "Not a criterion number."},
    ]
    records = instructions.to_findings(
        failures, run_id="run_20261124T000000Z_abcd", url="http://127.0.0.1:1/testcases/r/1.html",
        app="w3c/wcag-act-rules", repo="w3c/wcag-act-rules", commit="4ce3d61b6f7b04cbf2df0082a3446e97248b9124",
        model="claude-opus-5-5",
    )
    assert [r["criterion"]["id"] for r in records] == ["1.1.1", "3.1.1"]
    assert records[1]["element"] is None
    for record in records:
        assert finding_errors(record) == []
        assert record["check"]["source"] == "agent" and record["confidence"] == "judged"


def test_cost_of() -> None:
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000, cache_creation_input_tokens=0, cache_read_input_tokens=None)
    assert instructions.cost_of("claude-opus-5-5", usage) == pytest.approx(6.0)


def test_floors(tmp_path: Path) -> None:
    path = tmp_path / "t.toml"
    path.write_text("[act-subset.scan]\nmin_recall = 0.5\nmax_false_discovery_rate = 0.1\n")
    floors = eval_cli.load_floors("act-subset", "scan", path)
    assert eval_cli.missed_floors({"recall": 0.6, "false_discovery_rate": 0.05}, floors) == []
    assert len(eval_cli.missed_floors({"recall": 0.4, "false_discovery_rate": 0.2}, floors)) == 2
    assert len(eval_cli.missed_floors({"recall": None, "false_discovery_rate": None}, floors)) == 1
    assert eval_cli.load_floors("act-subset", "audit", path) == {}
    path.write_text("[act-subset.scan]\nmin_recal = 0.5\n")
    with pytest.raises(ValueError, match="min_recal"):
        eval_cli.load_floors("act-subset", "scan", path)


def test_repository_thresholds_parse() -> None:
    for kind in ("scan", "instructions", "audit"):
        eval_cli.load_floors("act-subset", kind)


def test_budget_refuses_a_case_that_could_pass_the_cap_when_nothing_runs() -> None:
    budget = eval_cli.Budget(1.0)
    budget.reserve(0.6)
    budget.settle(0.6, 0.5)
    with pytest.raises(eval_cli.BudgetExceeded):
        budget.reserve(0.6)  # 0.5 spent + 0.6 could pass 1.0
    budget.reserve(0.5)
    with pytest.raises(eval_cli.BudgetExceeded):
        eval_cli.Budget(0.0).reserve(0.01)


def test_budget_waits_for_running_cases_to_settle() -> None:
    budget = eval_cli.Budget(1.0)
    budget.reserve(0.6)
    started = threading.Event()
    waiter = threading.Thread(target=lambda: (budget.reserve(0.6), started.set()))
    waiter.start()
    assert not started.wait(0.2)  # 0.6 reserved + 0.6 could pass 1.0, so it waits
    budget.settle(0.6, 0.1)  # the running case cost far less than it reserved
    assert started.wait(2)
    waiter.join(2)
    assert budget.running == 1 and budget.spent == pytest.approx(0.1)


def test_worst_case_covers_a_full_output() -> None:
    usage = SimpleNamespace(input_tokens=2000, output_tokens=instructions.MAX_TOKENS,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    assert instructions.worst_case_cost("claude-opus-5-5", "<p>x</p>") > instructions.cost_of("claude-opus-5-5", usage)


# End to end, against a fake checkout built from the real subset file.

@pytest.fixture
def fake_act(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "act"
    assets = root / act.ASSETS
    entries = []
    with (act.REPO_ROOT / "corpus" / "act-subset.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            page = assets / row["relative_path"]
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text("<img src=x>" if row["expected"] == "failed" else "<p>fine</p>", encoding="utf-8")
            entries.append({"ruleId": row["rule_id"], "testcaseId": row["testcase_id"],
                            "ruleAccessibilityRequirements": {"wcag20:1.1.1": {}}})
    (assets / "testcases.json").write_text(json.dumps({"testcases": entries}), encoding="utf-8")
    monkeypatch.setattr(act, "checkout", lambda: root)
    return root


class FakeMessages:
    calls = 0

    def create(self, **request):
        FakeMessages.calls += 1
        page = request["messages"][0]["content"]
        failures = [{"criterion": "1.1.1", "level": "A", "selector": "img", "severity": "serious",
                     "summary": "The image has no text alternative."}] if "<img" in page else []
        assert "rule" not in page.lower()  # the model never sees the rule or the expected outcome
        return SimpleNamespace(
            stop_reason="end_turn", _request_id="req_test",
            content=[SimpleNamespace(type="text", text=json.dumps({"failures": failures}))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )


def test_instructions_baseline_end_to_end(fake_act: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(instructions, "make_client", lambda: SimpleNamespace(messages=FakeMessages()))
    out = tmp_path / "runs"
    code = cli.main(["eval", "act-subset", "--baseline", "instructions", "--limit", "20", "--out", str(out), "--json"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    (run_dir,) = out.iterdir()
    assert json.loads((run_dir / "eval.json").read_text()) == report
    assert list(report) == ["suite", "baseline", "corpus_commit", "cases", "positives", "flagged", "true_positives",
                            "false_positives", "false_negatives", "recall", "false_discovery_rate", "per_criterion",
                            "seconds", "cost_usd", "model"]
    assert report["cases"] == 20 and 0 < report["positives"] == report["true_positives"] == report["flagged"] < 20
    assert report["recall"] == 1.0 and report["false_discovery_rate"] == 0.0
    assert report["model"] == "claude-opus-5-5" and report["cost_usd"] == pytest.approx(20 * 0.014)
    for line in (run_dir / "findings.jsonl").read_text().splitlines():
        assert finding_errors(json.loads(line)) == []
    assert len((run_dir / "cases.jsonl").read_text().splitlines()) == 20
    assert json.loads((run_dir / "run.json").read_text())["kind"] == "eval"


def test_zero_budget_makes_no_model_call(fake_act: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instructions, "make_client", lambda: SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setattr(FakeMessages, "calls", 0)
    code = cli.main(["eval", "act-subset", "--baseline", "instructions", "--limit", "20",
                     "--max-cost", "0", "--out", str(tmp_path), "--quiet"])
    assert code == 3 and FakeMessages.calls == 0


def test_a_small_cap_still_runs_every_case_that_fits(fake_act: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    # Codex's case: 8 workers each reserve about 0.33 USD, but 20 cases cost 0.28 USD in all.
    monkeypatch.setattr(instructions, "make_client", lambda: SimpleNamespace(messages=FakeMessages()))
    code = cli.main(["eval", "act-subset", "--baseline", "instructions", "--limit", "20",
                     "--max-cost", "1", "--out", str(tmp_path / "runs"), "--quiet"])
    assert code == 0
    (run_dir,) = (tmp_path / "runs").iterdir()
    assert json.loads((run_dir / "eval.json").read_text())["cases"] == 20


def test_budget_stop_keeps_finished_cases(fake_act: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instructions, "make_client", lambda: SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setattr(FakeMessages, "calls", 0)
    monkeypatch.setattr(eval_cli, "WORKERS", {"instructions": 1})
    code = cli.main(["eval", "act-subset", "--baseline", "instructions", "--limit", "20",
                     "--max-cost", "0.5", "--out", str(tmp_path / "runs"), "--quiet"])
    assert code == 3
    (run_dir,) = (tmp_path / "runs").iterdir()
    assert not (run_dir / "eval.json").exists()  # a partial set is not a score
    finished = [json.loads(line) for line in (run_dir / "cases.jsonl").read_text().splitlines()]
    assert 0 < len(finished) == FakeMessages.calls < 20
    summary = json.loads((run_dir / "run.json").read_text())
    assert summary["cost_usd"] == pytest.approx(sum(c["cost_usd"] for c in finished)) and summary["cost_usd"] <= 0.5


def test_scan_baseline_runs_the_scan_command(fake_act: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_run_command(command, url, case_dir, base_dir, model, max_cost):
        calls.append((command, url))
        with urllib.request.urlopen(url) as response:  # loopback only: the eval's own page server
            failing = b"<img" in response.read()
        return ([{"outcome": "failed", "criterion": {"id": "1.1.1"}}] if failing else []), 0.0

    monkeypatch.setattr(eval_cli.tools, "run_command", fake_run_command)
    code = cli.main(["eval", "act-subset", "--baseline", "scan", "--limit", "6", "--out", str(tmp_path), "--quiet"])
    assert code == 0
    assert len(calls) == 6 and all(c == "scan" and u.startswith("http://127.0.0.1:") for c, u in calls)


def test_unwritten_suite_exits_4(tmp_path: Path) -> None:
    assert cli.main(["eval", "act", "--baseline", "scan", "--out", str(tmp_path)]) == 4


def test_unknown_suite_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["eval", "nope"])
    assert exit_info.value.code == 2


# The scan and audit runner, with the subprocess faked.

def fake_command(monkeypatch: pytest.MonkeyPatch, *, returncode: int, files: dict[str, str]) -> list[list[str]]:
    argvs = []

    def run(argv, **kwargs):
        argvs.append(argv)
        out = Path(argv[argv.index("--out") + 1]) / "run_20261124T000000Z_abcd"
        (out / "artifacts").mkdir(parents=True)
        for name, text in files.items():
            (out / name).write_text(text, encoding="utf-8")
        return SimpleNamespace(returncode=returncode, stderr="boom")

    monkeypatch.setattr(tools.subprocess, "run", run)
    return argvs


def example_finding() -> dict:
    record = json.loads((act.REPO_ROOT / "contracts" / "examples" / "rule-confirmed.json").read_text())
    record["evidence"] = [{"kind": "screenshot", "path": "artifacts/shot.png"}]  # not part of the fingerprint
    assert finding_errors(record) == []
    return record


def test_runner_rewrites_evidence_paths_to_the_eval_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_command(monkeypatch, returncode=0, files={
        "findings.jsonl": json.dumps(example_finding()) + "\n", "run.json": json.dumps({"cost_usd": 0.25})})
    case_dir = tmp_path / "artifacts" / "cases" / "r" / "t"
    findings, cost = tools.run_command("scan", "http://127.0.0.1:1/x.html", case_dir, tmp_path)
    path = findings[0]["evidence"][0]["path"]
    assert path == "artifacts/cases/r/t/run_20261124T000000Z_abcd/artifacts/shot.png"
    assert finding_errors(findings[0]) == [] and cost == 0.25


@pytest.mark.parametrize("missing", ["findings.jsonl", "run.json"])
def test_runner_rejects_a_run_without_its_contract_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         missing: str) -> None:
    files = {"findings.jsonl": "", "run.json": json.dumps({"cost_usd": 0.0})}
    del files[missing]
    fake_command(monkeypatch, returncode=0, files=files)
    with pytest.raises(tools.CaseError, match=missing):
        tools.run_command("scan", "http://127.0.0.1:1/x.html", tmp_path / "artifacts" / "c", tmp_path)


def test_failed_audit_keeps_its_cost_and_gets_a_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    argvs = fake_command(monkeypatch, returncode=3, files={"run.json": json.dumps({"cost_usd": 0.42})})
    with pytest.raises(tools.CaseError) as failure:
        tools.run_command("audit", "http://127.0.0.1:1/x.html", tmp_path / "artifacts" / "c", tmp_path, "m", 0.05)
    assert failure.value.cost_usd == 0.42
    assert argvs[0][-4:] == ["--model", "m", "--max-cost", "0.050000"]


def test_runner_turns_a_broken_findings_file_into_one_failed_case(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    fake_command(monkeypatch, returncode=0, files={"findings.jsonl": "{not json\n", "run.json": json.dumps({"cost_usd": 0.3})})
    with pytest.raises(tools.CaseError, match="not JSON") as failure:
        tools.run_command("audit", "http://127.0.0.1:1/x.html", tmp_path / "artifacts" / "c", tmp_path)
    assert failure.value.cost_usd == 0.3


def test_checkout_refuses_local_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "w3c" / "wcag-act-rules"
    data = repo / act.ASSETS / "testcases.json"
    data.parent.mkdir(parents=True)
    data.write_text("{}", encoding="utf-8")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    monkeypatch.setenv("KUEBIKO_ACT_DIR", str(repo))
    monkeypatch.setattr(act, "pinned_commit", lambda: head)
    assert act.checkout() == repo
    data.write_text('{"edited": true}', encoding="utf-8")
    with pytest.raises(act.CorpusError, match="local edits"):
        act.checkout()


def test_a_case_waiting_for_budget_does_not_start_after_the_run_stops(
        fake_act: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FailingMessages:
        def create(self, **request):
            calls.append(1)
            time.sleep(0.2)  # long enough for the second worker to be waiting on the budget
            raise RuntimeError("bad key")  # not transient: stops the run

    monkeypatch.setattr(instructions, "make_client", lambda: SimpleNamespace(messages=FailingMessages()))
    monkeypatch.setattr(eval_cli, "WORKERS", {"instructions": 2})
    # Each case reserves about 0.33 USD, so under 0.5 USD the second worker waits for the first.
    code = cli.main(["eval", "act-subset", "--baseline", "instructions", "--limit", "5",
                     "--max-cost", "0.5", "--out", str(tmp_path / "runs"), "--quiet"])
    assert code == 3
    assert len(calls) == 1
