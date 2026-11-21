# Project-Touhou
[Personal Project] Project-Touhou is inspired by accessibility-centric coding agents created for the sole-purpose of making software more accessible; benchmarked by users and deployed by devs, this project seeks to make all software more accessible and usable for certain underrepresented populations.

## Status

Early build. Nothing here audits a site yet. The repository currently holds the
rules, contracts and test corpus that the build runs against.

| Start here | What it is |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | The rulebook for every contributor, human or agent |
| [`contracts/`](contracts/README.md) | The finding record, the ledger schema and the command-line contract |
| [`corpus/`](corpus/README.md) | The pinned control set every phase runs against |

```bash
python -m pip install -e ".[dev]"
python scripts/validate_contracts.py
pytest -q
```

Project-Touhou reports evidence about accessibility barriers. It never certifies
that a site meets a standard or a law.
