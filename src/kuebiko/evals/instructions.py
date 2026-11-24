"""The instructions baseline: a general model given only an accessibility instruction file.

The model sees the instruction file and the page's HTML, nothing else: no rule
id, no expected outcome, no tools. Its answers become ``agent`` findings with
confidence ``judged``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from kuebiko import __version__
from kuebiko.contracts import finding_id
from kuebiko.evals.tools import CaseError

INSTRUCTIONS = Path(__file__).resolve().parents[3] / "evals" / "instructions" / "accessibility.md"
DEFAULT_MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TOKENS = 16000
CHECK_ID = "kuebiko:instructions-baseline"

# USD per million tokens: input, output, cache write, cache read.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.10),
}

SC_PATTERN = re.compile(r"^[1-4]\.[0-9]{1,2}\.[0-9]{1,2}$")

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "failures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string", "description": "Success criterion number, e.g. 1.1.1"},
                    "level": {"type": "string", "enum": ["A", "AA"]},
                    "selector": {"type": "string", "description": "CSS selector, or empty for the whole page"},
                    "severity": {"type": "string", "enum": ["blocker", "serious", "moderate", "minor"]},
                    "summary": {"type": "string"},
                },
                "required": ["criterion", "level", "selector", "severity", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["failures"],
    "additionalProperties": False,
}


def make_client():
    try:
        import anthropic
    except ModuleNotFoundError as error:
        raise RuntimeError("the instructions baseline needs the anthropic package: pip install anthropic") from error
    return anthropic.Anthropic()


def cost_of(model: str, usage) -> float:
    price_in, price_out, price_write, price_read = PRICES[model]
    return (
        usage.input_tokens * price_in
        + usage.output_tokens * price_out
        + (usage.cache_creation_input_tokens or 0) * price_write
        + (usage.cache_read_input_tokens or 0) * price_read
    ) / 1_000_000


def ask(client, model: str, html: str) -> tuple[list[dict], float]:
    """Return the model's raw failures for one page and the call's cost in USD."""
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": INSTRUCTIONS.read_text(encoding="utf-8"), "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": "Review this page and report every failure you find.\n\n<page>\n" + html + "\n</page>",
        }],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    cost = cost_of(model, response.usage)
    if response.stop_reason != "end_turn":
        raise CaseError(f"model stopped with {response.stop_reason} (request {response._request_id})", cost)
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["failures"], cost


def to_findings(failures: list[dict], *, run_id: str, url: str, app: str, repo: str, commit: str, model: str) -> list[dict]:
    """Turn the model's answers into finding records. Answers naming no valid criterion are dropped."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    records: dict[str, dict] = {}
    for failure in failures:
        if not SC_PATTERN.match(failure["criterion"]):
            continue
        summary = failure["summary"].strip()[:200]
        if len(summary) < 10:
            summary = f"The model reported a failure of {failure['criterion']}."
        selector = failure["selector"].strip()[:500]
        record = {
            "schema_version": "0.1",
            "run_id": run_id,
            "created_at": now,
            "target": {"app": app, "url": url, "repo": repo, "commit": commit},
            "state": {"steps": [], "viewport": {"width": 1280, "height": 800}},
            "element": {"selector": selector, "html": ""} if selector else None,
            "criterion": {"standard": "WCAG", "version": "2.2", "id": failure["criterion"], "level": failure["level"]},
            "check": {"id": CHECK_ID, "source": "agent", "tool": "kuebiko", "tool_version": __version__, "model": model},
            "outcome": "failed",
            "confidence": "judged",
            "severity": failure["severity"],
            "summary": summary,
            "evidence": [{"kind": "log", "text": failure["summary"][:4000]}],
        }
        record["id"] = finding_id(record)
        records.setdefault(record["id"], record)  # the same place reported twice is one finding
    return list(records.values())
