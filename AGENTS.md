# KuebikoAgent: rules for every agent

KuebikoAgent finds accessibility barriers in web apps, proposes fixes as pull
requests, and collects feedback from people who use assistive technology.

This file is the single rulebook. Claude Code, Codex and human contributors all
follow it. `CLAUDE.md` only imports this file, so edit rules here and nowhere else.

## Hard rules

1. **Never claim compliance.** Reports say which checks passed, which failed and
   which still need a person. The words "compliant", "conformant" and "certified"
   never appear in output about a target.
2. **Fixes are pull requests.** Never push to `main`. Never merge your own pull
   request. A person approves every merge.
3. **Do not game the checks.** A fix must remove the barrier, not hide it. The
   known ways to cheat are listed in `docs/gaming-patterns.md`. If you find a new
   one, add it there and write a check for it.
4. **Contracts are frozen.** Everything under `contracts/` changes only through
   its own pull request, titled `contract: ...` and approved by the maintainer.
   If a contract blocks you, open that pull request and keep working behind an
   adapter in your own directory.
5. **Stay in your lane.** Edit only the directories your workstream owns (see the
   table below). Shared files (`pyproject.toml`, `.github/`, `src/kuebiko/cli.py`,
   this file) change in small dedicated pull requests.
6. **Corpus code stays out of this repository.** Clone pinned apps into
   `.corpus/`, which is git-ignored. Several of them are AGPL or GPL.
7. **No personal data about testers.** No names, emails, handles or contact
   details in code, fixtures, logs, issues or the ledger. Sessions use an
   anonymous code. Record a tester's setup ("NVDA on Windows"), never a diagnosis.
8. **Label synthetic evidence.** Scripted and self-run sessions are marked
   `synthetic`. Never present them as feedback from users.
9. **Never weaken a test or a threshold to get a green build.** If a check is
   wrong, say so in the pull request and let the maintainer decide.

## Accessibility rules for any interface you build

These apply to the tester harness, HTML reports and every page this project
serves. Models write far more accessible markup when told to, so read this list
before writing any HTML.

- Use native HTML elements before ARIA. A button is a `<button>`.
- Every input has a visible `<label>`. Every image has `alt`, empty when the
  image is decorative.
- Everything works by keyboard alone, in a logical order, with a visible focus
  indicator and no keyboard traps.
- One `<h1>` per page, headings in order, and landmarks for header, navigation
  and main content.
- Text contrast is at least 4.5:1. Colour is never the only way meaning is shown.
- Status changes are announced with `role="status"` or `aria-live`. Error
  messages say what to fix.
- No time limits. Honour `prefers-reduced-motion`. Layout works at 320 px wide
  and at 200% zoom.
- The visible label of a control is contained in its accessible name, so voice
  control works.
- Before opening a pull request that touches an interface: complete its main
  task with the keyboard only, run the rule scan on the page, and paste both
  results into the pull request description.

## Stack

These are the defaults for the first release. Change them only through a
dedicated pull request.

- Python 3.12 or later. The package is `kuebiko` under `src/kuebiko`, and the
  command is `kuebiko`.
- Playwright for Python drives Chromium.
- axe-core runs inside the page for the rule scan.
- Guidepup's virtual screen reader, a JavaScript package, supplies screen-reader
  output. Workstream 2 chooses how to load it and documents the choice.
- SQLite holds the findings ledger (`kuebiko.db`). Its schema is
  `contracts/ledger.sql`.
- pytest for tests. Unit tests make no network calls.

## Contracts

| Contract | File | What it fixes |
| --- | --- | --- |
| Finding record | `contracts/finding.schema.json` | The one shape every check, agent and tester session emits |
| Ledger | `contracts/ledger.sql` | Where findings, fixes, verifications and sessions are stored |
| Command line | `contracts/cli.md` | Command names, options, outputs and exit codes |

`src/kuebiko/contracts.py` loads and validates them. Use it; do not re-implement
validation or the finding fingerprint.

## Workstreams

Each workstream is one agent in its own git worktree, working from its own
GitHub issue.

| # | Workstream | Owns | Done when |
| --- | --- | --- | --- |
| 1 | Target runner and corpus | `src/kuebiko/runner/`, `src/kuebiko/corpus/`, `corpus/` | `kuebiko corpus fetch --tier D-core` clones the six core apps at their pinned commits, and the runner can load a URL and replay recorded steps |
| 2 | Deterministic checks and ledger | `src/kuebiko/checks/`, `src/kuebiko/ledger/` | `kuebiko scan` emits valid finding records for the planted-barrier pages and writes them to the ledger |
| 3 | Audit agent | `src/kuebiko/audit/` | `kuebiko audit` beats scan-only recall on `corpus/act-subset.csv` with a false-discovery rate under 10% |
| 4 | Standards and report | `src/kuebiko/standards/`, `src/kuebiko/report/`, `standards/` | Every check maps to one success criterion and passes its ACT cases, and the report lists pass, fail, needs a person and not checked |
| 5 | Evals | `src/kuebiko/evals/`, `evals/` | `kuebiko eval act-subset` produces `eval.json` for both baselines, and CI runs the scan baseline |
| 6 | Remediation agent | `src/kuebiko/remediate/` | On A11YBench Lite and seeded TodoMVC, fixes clear the finding, keep tests green and trip no gaming guard |
| 7 | Tester harness | `src/kuebiko/harness/` | The maintainer completes a task in it by keyboard alone and by screen reader, and it passes its own audit |

Order matters on day one. Workstreams 1, 2 and 5 go first because the others
import them. Workstream 2 ships the ledger module as its own small pull request
before anything else. Workstream 4 can run in parallel from the start.

## How to work

- **Branch.** Name it `ws/`, the workstream number and a short name, for example
  `ws/3-audit-agent`. One worktree per agent.
- **One pull request per milestone, not per commit.** The automated reviewers on
  this repository are rate-limited.
- **Describe the verification.** Every pull request says what changed, how you
  verified it (the commands and their output), eval numbers before and after
  when you touch a check or an agent, and anything left undone.
- **Cross-review.** A pull request written by Claude gets a Codex review before
  the maintainer looks, and the reverse.
- **Blocked for more than 20 minutes?** Write what you tried in the issue and
  move to the next item.

## Commands

```bash
python -m pip install -e ".[dev]"
python scripts/validate_contracts.py
pytest -q
```

## Evals

- A **positive** is a test case whose expected outcome is `failed`.
- **Recall** is flagged positives divided by all positives.
- **False-discovery rate** is flagged cases that are not positives divided by all
  flagged cases.
- Tune only on `corpus/act-subset.csv`: 150 failed and 150 passed cases, fixed.
  Report full-set numbers once, at the end of a phase.
- Two baselines must exist before any agent work counts: the rule scan alone,
  and a general model given only an accessibility instruction file.
- Passing a scanner never counts as success on its own.

## Scope of the first release

- Web only.
- Five success criteria for the audit agent: 1.1.1, 1.3.1, 2.4.3, 4.1.2, 4.1.3.
- WCAG 2.2 Level AA only, with no legal profiles.
- Fixes only for rule-confirmed findings, in React and plain HTML.
- The virtual screen reader only. Real VoiceOver and NVDA runs come later.
- Six core apps booted (tier `D-core` in `corpus/manifest.csv`).
- No session recording in the harness.

Anything outside this list waits until people have used the first release.
