# Contracts

Three files in this directory are the interfaces every workstream builds
against. They exist so that agents working in parallel never have to guess what
another one will emit or expect.

| File | Fixes |
| --- | --- |
| `finding.schema.json` | The finding record: one barrier on one element in one page state |
| `ledger.sql` | The SQLite schema that stores findings and their lifecycle |
| `cli.md` | Command names, options, output files and exit codes |

`examples/` holds finding records that must validate. `tests/fixtures/invalid-findings/`
holds records that must be rejected. `python scripts/validate_contracts.py`
checks both, loads the ledger schema, and checks the corpus files. CI runs it on
every pull request.

## Changing a contract

Contracts are frozen. To change one:

1. Open a pull request that changes only `contracts/`, the matching code in
   `src/kuebiko/contracts.py` and the examples.
2. Title it `contract: ` followed by what changes.
3. Bump `schema_version` when a record that used to validate would stop
   validating, or the reverse.
4. The maintainer approves it. Nothing else merges on top of an unapproved
   contract change.

## The finding record in one paragraph

A finding says where (`target`, `state`, `element`), what (`criterion`, `check`,
`outcome`, `severity`, `summary`), how sure (`confidence`) and why (`evidence`).
It is an observation: once written it never changes. Whether it has been fixed
or verified lives in the ledger.

### Confidence

| Value | Meaning | Who can emit it |
| --- | --- | --- |
| `confirmed` | A deterministic check or a person observed the barrier | `rule`, `tester` |
| `judged` | A model concluded it from the evidence attached | `agent` |
| `needs-person` | Nobody has decided; the outcome is `cantTell` | `agent` |

Reports keep the three apart. They are never merged into one number.

### The id is a fingerprint

The same barrier found twice must get the same id, on any machine. The id is
`fnd_` followed by the first 16 hex characters of the SHA-256 of these six
values joined by newlines:

1. `target.app`
2. the path, query and fragment of `target.url`, without scheme, host or port
   (the fragment stays because single-page apps route with it, as in `/#/cart`)
3. `state.steps` serialised as JSON with sorted keys and no spaces
4. `element.selector`, or the empty string for a page-level finding
5. `criterion.id`
6. `check.id`

`kuebiko.contracts.finding_id` is the reference implementation. Use it; do not
write another one.

Two findings from different checks about the same element get different ids.
Group them by app, page, selector and criterion when you need one row per place.

## The ledger in one paragraph

`runs` records each scan, audit, eval or verification. `findings` holds the
latest record for each id and its status. `fixes` links a finding to a branch
and pull request. `verifications` records every re-test. `sessions` holds
tester sessions with an anonymous code and no contact details. The status rules
are written at the top of `ledger.sql`.
