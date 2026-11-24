# Test corpus

The control set every phase runs against. Everything is pinned to a commit so
that two runs a week apart see identical inputs.

Nothing from the corpus is stored in this repository. `kuebiko corpus fetch`
clones each item into `.corpus/`, which is git-ignored. Twelve of the real
applications are AGPL or GPL, so keep their code out of this tree.

## Files

| File | Content |
| --- | --- |
| `manifest.csv` | `repo,commit,tier` for every item pinned from GitHub |
| `act-subset.csv` | The fixed 300-case tuning subset of the W3C ACT test cases |
| `boot.toml`, `boot/` | How to start each `D-core` app from its checkout with Docker Compose |

## Tiers

| Tier | What it is | How it is labelled |
| --- | --- | --- |
| `A-act-test-cases` | W3C ACT test cases: small pages with an expected outcome per rule | By hand. 558 of the 1,211 cases belong to rules W3C has approved; the rest belong to proposed rules |
| `A-known-good-widgets` | W3C ARIA Authoring Practices examples: 76 pages over 28 widget patterns | Reference implementations. Use as negative controls and as hosts for seeded defects |
| `A-planted-barriers` | The UK government's accessibility tool audit: 142 test pages, each named for the barrier it plants | By hand. Static pages, last updated in 2019 |
| `A-negative-control` | GOV.UK Frontend and the US Web Design System | Built to be accessible, taken on reputation. Use to measure false positives |
| `C-one-app-many-frameworks` | TodoMVC: 45 implementations of one to-do app | Unlabelled until defects are injected |
| `D-core` | Six real applications to boot for the first release | Unlabelled |
| `D-wide` | Twenty-eight more real applications | Unlabelled |

Two more sets are not on GitHub and so are not in the manifest:

- **A11YBench** (tier B): 60 real websites with 8,886 violations found by IBM's
  rule checker. Each row of the dataset has a clone URL, a commit and an install
  command. Lite is the first 10 rows.
  <https://huggingface.co/datasets/LLM4APR/A11YBench>
- **W3C Before and After Demo**: an inaccessible site, its repaired version and
  an evaluation report for each page. From 2012, so it under-tests dynamic
  widgets. <https://www.w3.org/WAI/demos/bad/>

## The tuning subset

`act-subset.csv` is 150 cases expected to fail and 150 expected to pass, all
from approved rules, spread across 37 rules. `scripts/make_act_subset.py`
rebuilds it from `testcases.json` at the pinned commit and always writes the
same rows.

A case is one row: a rule and a page. The same page can appear under two rules,
so the key is `rule_id` plus `testcase_id`. `relative_path` is the page's
location under `content-assets/wcag-act-rules/` in the pinned checkout, so
evals can run offline.

Default scoring, which workstream 5 may refine through a pull request: a case
counts as flagged when the tool emits at least one `failed` finding on that
page for a success criterion its rule maps to. The mapping is the
`ruleAccessibilityRequirements` field of each case in `testcases.json`.

Tune on this subset only. Report numbers on the full set once, at the end of a
phase.

## What has and has not been checked

- Every repository in the manifest existed and was read at the pinned commit on
  21 November 2026.
- All six `D-core` applications booted from their pinned checkouts on
  24 November 2026 (see "Booting the core apps" below). No `D-wide`
  application has been booted.
- The real applications are a convenience sample of well-known, active
  projects. They probably have better accessibility than the average site, so
  results on them may understate real-world difficulty.
- The sizes above support overall recall and false-discovery rates and
  comparisons between versions down to about five points. They do not support
  claims per rule or per framework.

## Booting the core apps

`python -m kuebiko.corpus.boot [REPO ...] [--keep]` builds each app from its
checkout in `.corpus/` with Docker Compose, waits until it answers on
127.0.0.1, and then tears it down with its volumes, so every boot starts from
the same empty state. Code uses `kuebiko.corpus.boot.boot(repo)`, a context
manager that yields the URL. The app is always built from the pinned source,
never from a published image of the app. Base images (node, php, alpine and
Uptime Kuma's `base3`) do come from Docker Hub.

Checked on 24 November 2026 on macOS with Docker 28.3.3 (8 GB, 12 CPUs). The
cold build includes building the image; the warm boot reuses it. "First page"
is where Chromium landed after loading the URL and replaying two Tab presses
with `kuebiko.runner`.

| App | Port | Boots | Cold build | Warm boot | First page |
| --- | --- | --- | --- | --- | --- |
| actualbudget/actual | 8101 | yes | 130 s | 6 s | `/bootstrap`, "Welcome to Actual!" |
| excalidraw/excalidraw | 8102 | yes | 84 s | 7 s | The whiteboard |
| louislam/uptime-kuma | 8103 | yes, after one fix | 78 s | | `/setup-database`, first-run setup |
| BookStackApp/BookStack | 8104 | yes | 100 s | 20 s | `/login`, "Log In" |
| kanboard/kanboard | 8105 | yes | 14 s | 7 s | `/login` |
| go-gitea/gitea | 8106 | yes | 101 s | 7 s | The installer |

Notes:

- **Uptime Kuma** failed on the first try: its Dockerfile expects `dist/` to be
  built on the host first (`extra/release/lib.mjs` runs `npm run build` before
  `docker build`). The recipe now builds `dist/` in a one-shot `node:26`
  container and mounts it into the app.
- **BookStack** follows the repository's own development compose file without
  mailhog and xdebug. It uses `npm ci` rather than `npm install`, which would
  rewrite the pinned `package-lock.json`. Its build writes `vendor/`,
  `node_modules/` and `public/dist/` into the checkout; all are ignored by that
  repository.
- **Actual** uses `sync-server.Dockerfile`, the production server that also
  serves the web client.
- Four apps start on a first-run screen (an installer, a setup page or a login
  page). Reaching pages behind them needs seeded accounts, which is not done yet.
