"""``kuebiko corpus fetch [--tier TIER] [--dest DIR]`` and ``kuebiko corpus list``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kuebiko import corpus

EXIT_OK, EXIT_USAGE, EXIT_RUNTIME = 0, 2, 3


def add_arguments(parser: argparse.ArgumentParser) -> None:
    # A positional, not subparsers, so the common options work after the action too.
    parser.add_argument("action", choices=["fetch", "list"])
    parser.add_argument("--tier", metavar="TIER", help="only rows with this tier, for example D-core")
    parser.add_argument("--dest", type=Path, metavar="DIR", help="where to clone (default .corpus/ in the checkout)")


def run(args: argparse.Namespace) -> int:
    try:
        items = corpus.load_manifest()
    except (OSError, ValueError) as error:
        print(f"kuebiko corpus: {error}", file=sys.stderr)
        return EXIT_RUNTIME
    if args.tier:
        items = [item for item in items if item.tier == args.tier]
        if not items:
            print(f"kuebiko corpus: no manifest rows have tier {args.tier!r}", file=sys.stderr)
            return EXIT_USAGE
    dest = args.dest or corpus.default_dest()
    if not corpus.is_safe_dest(dest):
        print(f"kuebiko corpus: {dest} is inside the repository and not git-ignored; use .corpus/", file=sys.stderr)
        return EXIT_USAGE

    rows, failed = [], False
    for item in items:
        path = corpus.checkout_path(item, dest)
        row = {"repo": item.repo, "commit": item.commit, "tier": item.tier, "path": str(path)}
        if args.action == "list":
            row["fetched"] = corpus.head_commit(path) == item.commit
            line = f"{item.tier:<26} {item.repo:<40} {item.commit[:12]}  {'fetched' if row['fetched'] else '-'}"
        else:
            try:
                row["status"] = corpus.fetch(item, dest)
                line = f"{row['status']:<8} {item.repo} at {item.commit[:12]} -> {path}"
            except RuntimeError as error:
                row["status"], row["error"], failed = "failed", str(error), True
                print(f"kuebiko corpus: {error}", file=sys.stderr)
                line = None
        rows.append(row)
        if line and not args.quiet and not args.json:
            print(line, flush=True)
    if args.json:
        print(json.dumps(rows, indent=2))
    return EXIT_RUNTIME if failed else EXIT_OK
