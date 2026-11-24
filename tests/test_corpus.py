"""kuebiko corpus: manifest parsing and pinned fetches, against a local git repository (no network)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kuebiko import cli, corpus


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[corpus.Item, str]:
    """A local "GitHub" holding acme/app with two commits; the item pins the first."""
    repo = tmp_path / "github" / "acme" / "app.git"
    repo.mkdir(parents=True)
    git("init", "-q", cwd=repo)
    for text in ("one", "two"):
        (repo / "file.txt").write_text(text)
        git("add", ".", cwd=repo)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", text, cwd=repo)
    first, latest = git("rev-parse", "HEAD~1", cwd=repo), git("rev-parse", "HEAD", cwd=repo)
    monkeypatch.setattr(corpus, "GITHUB", (tmp_path / "github").as_uri() + "/")
    return corpus.Item("acme/app", first, "D-core"), latest


def test_manifest_has_six_core_apps() -> None:
    assert len([i for i in corpus.load_manifest() if i.tier == "D-core"]) == 6


@pytest.mark.parametrize(
    "row", ["../x,%s,D-core" % ("a" * 40), "a/..,%s,D-core" % ("a" * 40), "a/b c,%s,D-core" % ("a" * 40), "a/b,main,D-core"]
)
def test_manifest_rejects_unsafe_rows(tmp_path: Path, row: str) -> None:
    path = tmp_path / "manifest.csv"
    path.write_text("repo,commit,tier\n" + row + "\n")
    with pytest.raises(ValueError):
        corpus.load_manifest(path)


def test_fetch_checks_out_the_pinned_commit_not_the_tip(tmp_path: Path, remote) -> None:
    item, latest = remote
    dest = tmp_path / "dest"
    assert corpus.fetch(item, dest) == "cloned"
    path = dest / "acme" / "app"
    assert corpus.head_commit(path) == item.commit != latest
    assert (path / "file.txt").read_text() == "one"
    assert corpus.fetch(item, dest) == "present"


def test_fetch_never_overwrites_local_changes(tmp_path: Path, remote) -> None:
    item, latest = remote
    dest = tmp_path / "dest"
    corpus.fetch(item, dest)
    path = dest / "acme" / "app"
    corpus.fetch(corpus.Item(item.repo, latest, item.tier), dest)  # move to another commit
    (path / "file.txt").write_text("my edit")
    with pytest.raises(RuntimeError):
        corpus.fetch(item, dest)
    assert (path / "file.txt").read_text() == "my edit"


def test_fetch_of_missing_commit_fails(tmp_path: Path, remote) -> None:
    item, _ = remote
    with pytest.raises(RuntimeError):
        corpus.fetch(corpus.Item(item.repo, "f" * 40, item.tier), tmp_path / "dest")


def test_dest_must_be_outside_the_repo_or_ignored() -> None:
    root = corpus.repo_root()
    assert corpus.is_safe_dest(root / ".corpus")
    assert not corpus.is_safe_dest(root / "src" / "clones")


def test_cli_fetch_and_list(tmp_path: Path, remote, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    item, _ = remote
    monkeypatch.setattr(corpus, "load_manifest", lambda: [item, corpus.Item("acme/other", "b" * 40, "D-wide")])
    dest = str(tmp_path / "dest")

    assert cli.main(["corpus", "fetch", "--tier", "D-core", "--dest", dest, "--json"]) == 0
    assert [(r["repo"], r["status"]) for r in json.loads(capsys.readouterr().out)] == [("acme/app", "cloned")]

    assert cli.main(["corpus", "list", "--dest", dest, "--json"]) == 0
    assert {r["repo"]: r["fetched"] for r in json.loads(capsys.readouterr().out)} == {"acme/app": True, "acme/other": False}

    assert cli.main(["corpus", "fetch", "--tier", "D-wide", "--dest", dest, "--quiet"]) == cli.EXIT_RUNTIME
    assert cli.main(["corpus", "fetch", "--tier", "nope", "--dest", dest]) == cli.EXIT_USAGE


def test_every_core_app_has_a_local_only_boot_recipe() -> None:
    from urllib.parse import urlsplit

    from kuebiko.corpus import boot

    recipes = boot.recipes()
    assert set(recipes) == {i.repo for i in corpus.load_manifest() if i.tier == "D-core"}
    ports = []
    for recipe in recipes.values():
        compose = corpus.repo_root() / "corpus" / recipe["compose"]
        url = urlsplit(recipe["url"])
        ports.append(url.port)
        assert url.hostname == "127.0.0.1"
        assert f'"127.0.0.1:{url.port}:' in compose.read_text()
    assert len(set(ports)) == len(ports)


def test_wait_for_counts_any_answer_below_500(tmp_path: Path) -> None:
    import functools
    import http.server
    import socket
    import threading

    from kuebiko.corpus import boot

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert boot.wait_for(f"http://127.0.0.1:{server.server_port}/missing", 5) == 404
    finally:
        server.shutdown()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    with pytest.raises(TimeoutError):
        boot.wait_for(f"http://127.0.0.1:{closed}/", 0)
