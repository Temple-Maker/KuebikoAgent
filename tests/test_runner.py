"""The runner replays state.steps. The browser test serves a page on 127.0.0.1 and skips without Playwright."""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from kuebiko import runner


class FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.keyboard = self

    def __getattr__(self, name: str):
        return lambda *args: self.calls.append((name, *args))


def test_replay_maps_every_action() -> None:
    steps = [
        {"action": "goto", "url": "/cart?x=1#top"},
        {"action": "click", "selector": "#a"},
        {"action": "fill", "selector": "#b", "value": "v"},
        {"action": "press", "key": "Tab"},
        {"action": "press", "selector": "#c", "key": "Enter"},
        {"action": "hover", "selector": "#d"},
        {"action": "focus", "selector": "#e"},
        {"action": "select", "selector": "#f", "value": "2"},
        {"action": "wait", "ms": 5},
    ]
    assert runner.steps_errors(steps) == []
    page = FakePage()
    runner.replay(page, steps, "http://localhost:8080/app/start")
    assert page.calls == [
        ("goto", "http://localhost:8080/cart?x=1#top"),
        ("click", "#a"),
        ("fill", "#b", "v"),
        ("press", "Tab"),
        ("press", "#c", "Enter"),
        ("hover", "#d"),
        ("focus", "#e"),
        ("select_option", "#f", "2"),
        ("wait_for_timeout", 5),
    ]


@pytest.mark.parametrize("path", ["//evil.example/", "/\\evil.example", "http://evil.example/", "cart"])
def test_goto_stays_on_the_target_origin(path: str) -> None:
    with pytest.raises(ValueError):
        runner.resolve("http://localhost:8080/", path)


@pytest.mark.parametrize(
    "steps", [[{"action": "click"}], [{"action": "goto", "url": "//x"}], [{"action": "scroll"}], {"action": "wait", "ms": 1}]
)
def test_invalid_steps_are_rejected(steps) -> None:
    assert runner.steps_errors(steps)
    with pytest.raises(ValueError):
        with runner.open_page("http://localhost/", {"steps": steps, "viewport": {"width": 1, "height": 1}}):
            pass


def test_failed_step_says_which() -> None:
    class Broken(FakePage):
        def click(self, selector):
            raise TimeoutError("no such element")

    with pytest.raises(runner.StepError) as info:
        runner.replay(Broken(), [{"action": "wait", "ms": 0}, {"action": "click", "selector": "#x"}], "http://h/")
    assert info.value.index == 1


PAGES = {
    "index.html": """<!doctype html><html lang="en"><title>Home</title><main>
<label for="q">Search</label><input id="q">
<button id="go" onclick="document.getElementById('out').textContent = 'searched ' + q.value">Go</button>
<p id="out" role="status"></p><a href="/second.html">Next</a></main></html>""",
    "second.html": """<!doctype html><html lang="en"><title>Second</title><main><h1>Second</h1></main></html>""",
}


@pytest.fixture
def site(tmp_path: Path):
    for name, html in PAGES.items():
        (tmp_path / name).write_text(html)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/index.html"
    server.shutdown()


def test_open_page_in_chromium(site: str) -> None:
    pytest.importorskip("playwright.sync_api")
    state = {
        "viewport": {"width": 320, "height": 640},
        "steps": [
            {"action": "fill", "selector": "#q", "value": "ramen"},
            {"action": "focus", "selector": "#go"},
            {"action": "press", "key": "Enter"},
        ],
    }
    try:
        with runner.open_page(site, state) as page:
            assert page.text_content("#out") == "searched ramen"
            assert page.viewport_size == {"width": 320, "height": 640}
            runner.replay(page, [{"action": "goto", "url": "/second.html"}], site)
            assert page.title() == "Second"
    except Exception as error:
        if "Executable doesn't exist" in str(error):
            pytest.skip("Chromium not installed: playwright install chromium")
        raise
