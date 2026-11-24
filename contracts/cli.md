# Command-line contract

Version 0.1. The command is `kuebiko`. This file fixes command names, options,
output files and exit codes so that seven workstreams can build against each
other without talking.

Words in CAPITALS below are values you supply. They are not typed literally.

## Commands

| Command | Owner | What it does |
| --- | --- | --- |
| `kuebiko corpus` | Workstream 1 | Fetches pinned corpus items into `.corpus/` |
| `kuebiko scan` | Workstream 2 | Runs the rule scan and deterministic checks on a target |
| `kuebiko audit` | Workstream 3 | Runs the scan, then the audit agent, on a target |
| `kuebiko report` | Workstream 4 | Writes the per-criterion report for a run |
| `kuebiko eval` | Workstream 5 | Scores a tool or baseline on a labelled suite |
| `kuebiko fix` | Workstream 6 | Proposes a fix for findings as a pull request |
| `kuebiko verify` | Workstream 6 | Re-tests a finding and records the result |
| `kuebiko preview` | Workstream 7 | Serves a preview build with a task and a feedback form |
| `kuebiko session` | Workstream 7 | Imports or lists tester sessions |

`src/kuebiko/cli.py` dispatches each command to the module its owner provides.
A command with no module yet exits with code 4. Owners never edit `cli.py`;
they add the module named there, with two functions:

```python
def add_arguments(parser): ...   # declare the command's options
def run(args) -> int: ...        # do the work, return the exit code
```

## Options every command accepts

| Option | Meaning |
| --- | --- |
| `--ledger PATH` | Ledger database file. Default `kuebiko.db` in the current directory |
| `--out DIR` | Where to write the run directory. Default `runs/` |
| `--json` | Print the machine-readable result to standard output and nothing else |
| `--quiet` | Print errors only |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The command ran and nothing crossed a threshold |
| 1 | The command ran and a threshold was crossed: findings at or above `--fail-on`, an eval below its floor, or a verification that failed |
| 2 | Usage error |
| 3 | Runtime failure: target unreachable, tool crash, model error |
| 4 | Not implemented yet |

## Run directory

`scan`, `audit`, `eval` and `verify` each create `DIR/RUN_ID/`, where RUN_ID
starts with `run_`:

| File | Content |
| --- | --- |
| `findings.jsonl` | One finding record per line, each valid against `finding.schema.json` |
| `run.json` | The run summary below |
| `artifacts/` | Screenshots, DOM snapshots and transcripts that findings point to |
| `report.md` | Written by `kuebiko report` |

`run.json` has these keys:

```json
{
  "run_id": "run_20261121T101500Z_a1b2",
  "kind": "audit",
  "app": "example/shop",
  "url": "http://localhost:8080/",
  "profile": "wcag22-aa",
  "tool_version": "0.0.1",
  "model": null,
  "started_at": "2026-11-21T10:15:00Z",
  "finished_at": "2026-11-21T10:19:30Z",
  "cost_usd": 0.0,
  "criteria": [
    {
      "id": "1.1.1",
      "level": "A",
      "result": "fail",
      "checks": ["axe:image-alt"],
      "finding_ids": ["fnd_0123456789abcdef"]
    }
  ]
}
```

`result` for a criterion is one of `pass`, `fail`, `needs-person` or
`not-checked`. A criterion with no check is `not-checked`, never `pass`.

## kuebiko corpus

```
kuebiko corpus fetch [--tier TIER] [--dest DIR]
kuebiko corpus list
```

Clones each row of `corpus/manifest.csv` at its pinned commit into `.corpus/`.
TIER is a value from the manifest's `tier` column, for example `D-core`. It never
writes inside the repository's tracked files.

## kuebiko scan

```
kuebiko scan TARGET [--steps FILE] [--criteria LIST] [--fail-on SEVERITY]
```

TARGET is a URL or a corpus key. FILE is a JSON array of steps in the shape
`finding.schema.json` defines. LIST is comma-separated criterion numbers.
Emits only findings with `check.source` equal to `rule`.

## kuebiko audit

```
kuebiko audit TARGET [--steps FILE] [--criteria LIST] [--fail-on SEVERITY]
                     [--model NAME] [--max-cost USD]
```

Runs everything `scan` runs, then the audit agent. Agent findings carry
`check.source` equal to `agent` and never the confidence `confirmed`. The
command stops and exits 3 when spend would pass `--max-cost`.

## kuebiko report

```
kuebiko report RUN_ID [--format md|json|html]
```

Lists every criterion in the profile as pass, fail, needs a person or not
checked, with the findings behind each. The report never uses the words
"compliant", "conformant" or "certified".

## kuebiko eval

```
kuebiko eval SUITE [--baseline scan|instructions] [--limit N] [--model NAME]
                   [--max-cost USD]
```

SUITE is one of `act-subset`, `act`, `apg-seeded`, `todomvc-seeded` or
`a11ybench-lite`. Without `--baseline` it scores `kuebiko audit`. It writes
`eval.json` into the run directory:

```json
{
  "suite": "act-subset",
  "baseline": null,
  "corpus_commit": "4ce3d61b6f7b04cbf2df0082a3446e97248b9124",
  "cases": 300,
  "positives": 150,
  "flagged": 131,
  "true_positives": 120,
  "false_positives": 11,
  "false_negatives": 30,
  "recall": 0.8,
  "false_discovery_rate": 0.084,
  "per_criterion": {},
  "seconds": 4100.0,
  "cost_usd": 2.9,
  "model": "NAME"
}
```

The numbers in that example are made up to show the shape. Floors live in
`evals/thresholds.toml`, and the command exits 1 when a floor is missed.

## kuebiko fix

```
kuebiko fix FINDING_ID [FINDING_ID ...] --repo PATH [--no-pr] [--allow-judged]
                                        [--max-cost USD]
```

Accepts only findings whose confidence is `confirmed` unless `--allow-judged`
is given. Writes the fix on a new branch, runs the checks again, runs every
guard in `docs/gaming-patterns.md`, adds one regression test, and opens a pull
request. It never merges and never pushes to the default branch.

## kuebiko verify

```
kuebiko verify FINDING_ID [--session CODE]
```

Replays the finding's steps and runs its check again. With `--session` it
records a session verification instead. Exits 1 when the barrier is still there.

## kuebiko preview

```
kuebiko preview --app APP --task TEXT [--url URL] [--port PORT]
```

Serves the feedback layer next to a running preview build and prints the link
to hand to a tester. The page it serves follows the accessibility rules in
`AGENTS.md`.

## kuebiko session

```
kuebiko session import FILE [--synthetic]
kuebiko session list
```

Imports a session file into the ledger. A session holds an anonymous code, the
tester's setup, the task and the result. It holds no names and no contact
details, and `import` rejects a file that has any.
