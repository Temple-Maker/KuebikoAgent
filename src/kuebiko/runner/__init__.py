"""Load a target in Chromium and replay a finding's ``state``.

``state`` is the object ``contracts/finding.schema.json`` defines: ``steps`` to
replay after loading the URL and the ``viewport``. Selectors are Playwright
selectors (CSS unless prefixed, for example ``role=button[name="Save"]``).

    with open_page("http://localhost:3000/", record["state"]) as page:
        ...  # the page is now in the state where the barrier appears

Playwright is imported only when a page is opened, so importing this module
needs no browser.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from kuebiko.contracts import finding_schema

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}


class StepError(RuntimeError):
    """A step could not be replayed. ``index`` is its position in ``steps``."""

    def __init__(self, index: int, step: dict[str, Any], cause: Exception) -> None:
        super().__init__(f"step {index} {step!r} failed: {cause}")
        self.index, self.step = index, step


@lru_cache(maxsize=1)
def _steps_validator() -> Draft202012Validator:
    schema = finding_schema()
    return Draft202012Validator({"type": "array", "items": {"$ref": "#/$defs/step"}, "$defs": schema["$defs"]})


def steps_errors(steps: Any) -> list[str]:
    """Every reason ``steps`` is not a valid ``state.steps`` array. Empty means valid."""
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(steps)'}: {e.message}"
        for e in _steps_validator().iter_errors(steps)
    ]


def resolve(base_url: str, path: str) -> str:
    """The URL a ``goto`` step loads: ``path`` on the origin of ``base_url``."""
    if not path.startswith("/") or path[1:2] in ("/", "\\"):
        raise ValueError(f"goto url must be a path on the target's origin, got {path!r}")
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}{path}"


def replay(page: Any, steps: Sequence[dict[str, Any]], base_url: str) -> None:
    """Run each step on a Playwright ``page``. Raises ``StepError`` at the first failure."""
    for index, step in enumerate(steps):
        action, selector = step["action"], step.get("selector")
        try:
            if action == "goto":
                page.goto(resolve(base_url, step["url"]))
            elif action == "click":
                page.click(selector)
            elif action == "fill":
                page.fill(selector, step["value"])
            elif action == "press":
                # Without a selector the key goes to whatever has focus, as a keyboard user's would.
                page.press(selector, step["key"]) if selector else page.keyboard.press(step["key"])
            elif action == "hover":
                page.hover(selector)
            elif action == "focus":
                page.focus(selector)
            elif action == "select":
                page.select_option(selector, step["value"])
            elif action == "wait":
                page.wait_for_timeout(step["ms"])
            else:
                raise ValueError(f"unknown action {action!r}")
        except Exception as error:
            raise StepError(index, step, error) from error


@contextmanager
def open_page(
    url: str, state: dict[str, Any] | None = None, *, headless: bool = True, timeout_ms: int = 15_000
) -> Iterator[Any]:
    """Open ``url`` in Chromium at ``state.viewport``, replay ``state.steps`` and yield the page."""
    state = state or {}
    steps = state.get("steps", [])
    errors = steps_errors(steps)
    if errors:
        raise ValueError("invalid steps:\n  " + "\n  ".join(errors))
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Playwright is not installed: pip install playwright && playwright install chromium"
        ) from error
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            context = browser.new_context(viewport=state.get("viewport", DEFAULT_VIEWPORT))
            context.set_default_timeout(timeout_ms)
            page = context.new_page()
            page.goto(url)
            replay(page, steps, url)
            yield page
        finally:
            browser.close()
