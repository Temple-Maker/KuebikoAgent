"""The test corpus: repositories pinned in ``corpus/manifest.csv``, cloned into ``.corpus/``.

Nothing cloned here is ever committed. Several corpus apps are AGPL or GPL.
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kuebiko.contracts import contracts_dir

GITHUB = "https://github.com/"  # tests point this at a local directory

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Item:
    """One manifest row. ``repo`` is the corpus key, for example ``go-gitea/gitea``."""

    repo: str
    commit: str
    tier: str


def repo_root() -> Path:
    return contracts_dir().parent


def default_dest() -> Path:
    return repo_root() / ".corpus"


def load_manifest(path: Path | None = None) -> list[Item]:
    """Read the manifest. Rejects rows whose repo or commit could escape a path or URL."""
    path = path or repo_root() / "corpus" / "manifest.csv"
    with open(path, newline="", encoding="utf-8") as handle:
        items = [Item(row["repo"], row["commit"], row["tier"]) for row in csv.DictReader(handle)]
    for item in items:
        if not _REPO.match(item.repo) or any(p in (".", "..") for p in item.repo.split("/")):
            raise ValueError(f"manifest: {item.repo!r} is not owner/name")
        if not _COMMIT.match(item.commit):
            raise ValueError(f"manifest: {item.repo} is not pinned to a full commit hash")
    return items


def checkout_path(item: Item, dest: Path) -> Path:
    return dest / item.repo


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # No credential prompt: a missing repository should fail, not hang.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)


def head_commit(path: Path) -> str | None:
    """The commit checked out at ``path``, or None when there is no clone there."""
    if not (path / ".git").exists():
        return None
    result = _git("rev-parse", "HEAD", cwd=path)
    return result.stdout.strip() if result.returncode == 0 else None


def fetch(item: Item, dest: Path) -> str:
    """Make ``dest/owner/name`` a checkout of the pinned commit. Returns "present" or "cloned".

    Only the one commit is fetched (depth 1). Local changes are never overwritten:
    git refuses the checkout and this raises.
    """
    path = checkout_path(item, dest)
    if head_commit(path) == item.commit:
        return "present"
    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ("init", "-q"),
        ("fetch", "-q", "--depth", "1", f"{GITHUB}{item.repo}.git", item.commit),
        ("-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"),
    ):
        result = _git(*args, cwd=path)
        if result.returncode != 0:
            raise RuntimeError(f"{item.repo}: git {' '.join(args)} failed: {result.stderr.strip()}")
    if head_commit(path) != item.commit:
        raise RuntimeError(f"{item.repo}: checked out {head_commit(path)}, expected {item.commit}")
    return "cloned"


def is_safe_dest(dest: Path) -> bool:
    """True unless ``dest`` lies inside this repository somewhere git does not ignore."""
    root = repo_root().resolve()
    dest = dest.resolve()
    if not dest.is_relative_to(root):
        return True
    # 0 ignored, 1 not ignored, 128 not a git checkout (so nothing is tracked).
    # The trailing slash lets directory-only patterns like ".corpus/" match before it exists.
    return _git("check-ignore", "-q", f"{dest}/", cwd=root).returncode != 1
