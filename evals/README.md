# Evals

`kuebiko eval SUITE [--baseline scan|instructions]` scores a tool on a labelled
suite and writes `eval.json` (shape in `contracts/cli.md`) into
`runs/RUN_ID/`. Only `act-subset` is implemented so far.

| Tool | What runs on each page |
| --- | --- |
| `--baseline scan` | `kuebiko scan URL`, through the command-line contract |
| `--baseline instructions` | A general model (default `claude-opus-5-5`, effort `medium`) given only `instructions/accessibility.md` and the page's HTML |
| no `--baseline` | `kuebiko audit URL` |

## Running it

```bash
kuebiko corpus fetch --tier A-act-test-cases   # or clone w3c/wcag-act-rules at the pinned commit into .corpus/w3c/wcag-act-rules
kuebiko eval act-subset --baseline scan
kuebiko eval act-subset --baseline instructions    # needs Anthropic API credentials and `pip install anthropic`
pytest -q evals/tests
```

`KUEBIKO_ACT_DIR` points at a checkout somewhere else. The command checks the
checkout is at the commit pinned in `corpus/manifest.csv`. Pages are served
from `127.0.0.1`, so nothing goes over the network except model calls.

## Scoring

As `AGENTS.md` and `corpus/README.md` define it. A case is flagged when the tool
emits at least one `failed` finding on its page for a success criterion its
rule maps to. Choices made here:

- "Maps to" means every `wcagNN:X.Y.Z` key in the case's
  `ruleAccessibilityRequirements`, secondary criteria included. Technique and
  ARIA keys are not criteria. `cantTell` findings never flag a case.
- The eight `46ca7f` cases in the subset map to no criterion, so no tool can
  flag them: three failed cases are a recall ceiling of 147/150.
- `per_criterion` lists only criteria with at least 20 cases, because the
  subset does not support finer claims.
- A case that cannot be run (tool crash, model refusal) counts as not flagged,
  is listed in `cases.jsonl`, and makes the command exit 3.

Each run directory also holds `cases.jsonl` (one line per case: expected,
criteria, reported, flagged, cost, error), `findings.jsonl` and `run.json`.

## Cost

`--max-cost` (default 15 USD) stops a run before spend would pass it. The first
full instructions run on `claude-opus-5-5` cost 1.53 USD and took 120 s
(24 November 2026). The monthly budget for model spend is 100 to 150 USD.

## Floors

`thresholds.toml`. A missed floor exits 1. Runs with `--limit` are not checked.
