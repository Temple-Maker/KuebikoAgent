"""Boot a corpus app from its pinned checkout with Docker Compose.

Recipes live in ``corpus/boot.toml``. Each app is built from the checkout in
``.corpus/`` (never from a published image of the app), bound to 127.0.0.1 only,
and torn down with its volumes afterwards so every boot starts from the same state.

    with boot("kanboard/kanboard") as url:
        with open_page(url) as page: ...

    python -m kuebiko.corpus.boot [REPO ...] [--keep]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kuebiko import corpus


def recipes() -> dict[str, dict[str, str]]:
    with open(corpus.repo_root() / "corpus" / "boot.toml", "rb") as handle:
        return tomllib.load(handle)


def _compose(repo: str, dest: Path) -> list[str]:
    item = next((i for i in corpus.load_manifest() if i.repo == repo), None)
    recipe = recipes().get(repo)
    if item is None or recipe is None:
        raise KeyError(f"no boot recipe for {repo!r}; see corpus/boot.toml")
    path = corpus.checkout_path(item, dest)
    if corpus.head_commit(path) != item.commit:
        raise RuntimeError(f"{repo} is not checked out at its pinned commit; run kuebiko corpus fetch")
    project = "kuebiko-" + repo.replace("/", "-").lower()
    compose_file = corpus.repo_root() / "corpus" / recipe["compose"]
    return ["docker", "compose", "-p", project, "-f", str(compose_file), "--project-directory", str(path)]


def wait_for(url: str, timeout_s: float) -> int:
    """Poll ``url`` until it answers below 500. Returns the status; raises TimeoutError."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            if error.code < 500:
                return error.code
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"{url} did not answer within {timeout_s:.0f}s")
        time.sleep(2)


def up(repo: str, dest: Path | None = None, timeout_s: float = 300) -> str:
    """Build and start ``repo``, wait until it answers, and return its URL."""
    command = _compose(repo, dest or corpus.default_dest())
    subprocess.run([*command, "up", "-d", "--build"], check=True)
    url = recipes()[repo]["url"]
    try:
        wait_for(url, timeout_s)
    except TimeoutError:
        subprocess.run([*command, "logs", "--tail", "40"], check=False)  # the why, before down() deletes it
        raise
    return url


def down(repo: str, dest: Path | None = None) -> None:
    subprocess.run([*_compose(repo, dest or corpus.default_dest()), "down", "-v"], check=False)


@contextmanager
def boot(repo: str, dest: Path | None = None, timeout_s: float = 300) -> Iterator[str]:
    """Start ``repo``, yield its URL, then stop it and delete its volumes."""
    try:
        yield up(repo, dest, timeout_s)
    finally:
        down(repo, dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kuebiko.corpus.boot", description=__doc__.split("\n")[0])
    parser.add_argument("repos", nargs="*", metavar="REPO", help="default: every app in corpus/boot.toml")
    parser.add_argument("--keep", action="store_true", help="leave the apps running")
    parser.add_argument("--timeout", type=float, default=300, help="seconds to wait for HTTP after start")
    args = parser.parse_args(argv)
    results = {}
    for repo in args.repos or list(recipes()):
        started = time.monotonic()
        try:
            url = up(repo, timeout_s=args.timeout)
            results[repo] = f"boots   {url} ({time.monotonic() - started:.0f}s)"
        except Exception as error:  # report every app, then carry on with the next
            results[repo] = f"FAILED  {error}"
        finally:
            if not args.keep:
                down(repo)
        print(f"{repo}: {results[repo]}", flush=True)
    print("\n".join(f"{repo:<26} {result}" for repo, result in results.items()))
    return 0 if all(r.startswith("boots") for r in results.values()) else 3


if __name__ == "__main__":
    sys.exit(main())
