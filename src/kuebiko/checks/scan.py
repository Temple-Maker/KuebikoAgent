"""Rule checks for one page: axe-core in the page plus Playwright checks.

Everything here emits ``check.source = rule`` findings only. A check reports a
barrier when it measured one; it never reports a pass and never guesses.

| Check | Criterion | What it measures |
| --- | --- | --- |
| ``axe:<rule>`` | the rule's WCAG tags | axe-core violations on load |
| ``kuebiko:keyboard-trap`` | 2.1.2 | Tab and Shift+Tab both leave focus on the same element |
| ``kuebiko:focus-visible`` | 2.4.7 | Focusing an element changes no pixel around it, or it has no size or is off-screen |
| ``kuebiko:contrast-focus`` / ``-hover`` | 1.4.3 | axe color-contrast fails only while the element is focused / hovered |
| ``kuebiko:reflow-320`` | 1.4.10 | At 320 px wide the page scrolls sideways; reports the outermost elements that stick out |
| ``kuebiko:zoom-200`` | 1.4.4 | At 200% zoom a text container clips content it did not clip at 100% |

The tab-order walk drives the keyboard checks and is saved as a keyboard trace.
Guidepup's virtual screen reader walks the page once; its phrase for each axe
element is attached as evidence and the whole log is saved as a transcript.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from kuebiko import __version__
from kuebiko.contracts import SCHEMA_VERSION, finding_id, page_of
from kuebiko.ledger import now

NODE_MODULES = Path(__file__).parent / "js" / "node_modules"
AXE_JS = NODE_MODULES / "axe-core" / "axe.min.js"
VSR_JS = NODE_MODULES / "@guidepup" / "virtual-screen-reader" / "lib" / "esm" / "index.browser.js"
PAGE_JS = Path(__file__).parent / "page.js"

VIEWPORT = {"width": 1280, "height": 800}
REFLOW_VIEWPORT = {"width": 320, "height": 640}
# 200% browser zoom on a 1280x800 window gives a 640x400 CSS viewport.
ZOOM_VIEWPORT = {"width": 640, "height": 400}
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa"]
MAX_TABS = 60
MAX_SR_STEPS = 400
MAX_OVERFLOW = 10

# check id -> (criterion, level, severity, summary)
KUEBIKO_CHECKS = {
    "kuebiko:keyboard-trap": ("2.1.2", "A", "blocker",
        "Keyboard focus cannot leave this element with Tab or Shift+Tab, so keyboard users are stuck."),
    "kuebiko:focus-visible": ("2.4.7", "AA", "serious",
        "Nothing on screen shows when this element has keyboard focus, so keyboard users lose their place."),
    "kuebiko:contrast-focus": ("1.4.3", "AA", "serious",
        "Text in this element drops below the required contrast while it has keyboard focus."),
    "kuebiko:contrast-hover": ("1.4.3", "AA", "serious",
        "Text in this element drops below the required contrast while the pointer is over it."),
    "kuebiko:reflow-320": ("1.4.10", "AA", "serious",
        "At 320 px wide this element sticks out past the screen, so people who zoom must scroll sideways."),
    "kuebiko:zoom-200": ("1.4.4", "AA", "serious",
        "At 200% zoom this element cuts off its own text, so people who enlarge text lose content."),
}
IMPACT_TO_SEVERITY = {"critical": "blocker", "serious": "serious", "moderate": "moderate", "minor": "minor"}
SEVERITY_RANK = {"minor": 0, "moderate": 1, "serious": 2, "blocker": 3}


class PageNotLoaded(Exception):
    """The page answered with an HTTP error, so there is nothing to scan."""


def load_assets() -> dict[str, str]:
    """Read the in-page scripts. Raises FileNotFoundError with the install command."""
    missing = [p for p in (AXE_JS, VSR_JS) if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            f"missing {missing[0]}; run: npm ci --prefix {NODE_MODULES.parent}"
        )
    return {name: path.read_text(encoding="utf-8") for name, path in
            (("axe", AXE_JS), ("vsr", VSR_JS), ("page", PAGE_JS))}


def criteria_of(tags: list[str]) -> list[tuple[str, str]]:
    """WCAG 2.2 criteria and level an axe rule's tags name, e.g. wcag111 + wcag2a."""
    levels = [len(m.group(1)) for t in tags if (m := re.fullmatch(r"wcag2\d?(a{1,3})", t))]
    if not levels:
        return []
    level = "A" * min(levels)
    found = [f"{m[1]}.{m[2]}.{m[3]}" for t in tags if (m := re.fullmatch(r"wcag(\d)(\d)(\d{1,2})", t))]
    return [(c, level) for c in found if c != "4.1.1"]  # 4.1.1 is obsolete in WCAG 2.2


def make_record(*, run_id: str, target: dict[str, Any], url: str, steps: list[dict], viewport: dict,
                element: dict | None, criterion: tuple[str, str], check: dict, severity: str,
                summary: str, evidence: list[dict], hint: str | None = None,
                description: str | None = None) -> dict[str, Any]:
    """Build one rule finding record with its fingerprint id."""
    state: dict[str, Any] = {"steps": steps, "viewport": viewport}
    if description:
        state["description"] = description[:200]
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": "",
        "run_id": run_id,
        "created_at": now(),
        "target": {**target, "url": url},
        "state": state,
        "element": element and {"selector": element["selector"], "html": element["html"][:2000]},
        "criterion": {"standard": "WCAG", "version": "2.2", "id": criterion[0], "level": criterion[1]},
        "check": {**check, "source": "rule"},
        "outcome": "failed",
        "confidence": "confirmed",
        "severity": severity,
        "summary": summary[:200],
        "evidence": evidence,
    }
    if hint:
        record["remediation_hint"] = hint[:1000]
    record["id"] = finding_id(record)
    return record


def axe_records(violations: list[dict], rules: dict[str, dict], sr: dict[str, str], axe_version: str,
                **base) -> list[dict]:
    """One record per violating node and criterion."""
    records = []
    for rule in violations:
        meta = rules.get(rule["id"], {})
        act = next((a for a in meta.get("actIds") or [] if re.fullmatch(r"[0-9a-z]{6}", a)), None)
        check = {"id": f"axe:{rule['id']}", "tool": "axe-core", "tool_version": axe_version}
        if act:
            check["act_rule"] = act
        for node in rule["nodes"]:
            selector = node["selector"]
            evidence = [{"kind": "dom", "text": node["html"][:4000]}]
            if selector in sr:
                evidence.append({"kind": "sr-transcript", "text": sr[selector][:4000]})
            data = next((c.get("data") for c in node.get("any", []) if isinstance(c.get("data"), dict)), None)
            if rule["id"] == "color-contrast" and data:
                evidence.append({"kind": "contrast", "data": data})
            for criterion in criteria_of(rule["tags"]):
                records.append(make_record(
                    **base, element={"selector": selector, "html": node["html"]}, criterion=criterion,
                    check=check, severity=IMPACT_TO_SEVERITY.get(rule.get("impact") or "", "moderate"),
                    summary=rule["help"].rstrip(".") + ".", evidence=evidence, hint=node.get("failureSummary"),
                ))
    return records


def kuebiko_record(check_id: str, element: dict | None, evidence: list[dict], **base) -> dict:
    criterion, level, severity, summary = KUEBIKO_CHECKS[check_id]
    return make_record(**base, element=element, criterion=(criterion, level),
                       check={"id": check_id, "tool": "kuebiko", "tool_version": __version__},
                       severity=severity, summary=summary, evidence=evidence)


def slug(page: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", page).strip("_") or "root"


class Scanner:
    """Scans pages in one browser context and collects rule findings."""

    def __init__(self, context, *, run_id: str, target: dict, steps: list[dict], run_dir: Path):
        self.context = context
        self.run_id = run_id
        self.target = target
        self.steps = steps
        self.run_dir = run_dir
        self.assets = load_assets()
        self.rules: dict[str, list[tuple[str, str]]] = {cid: [v[:2]] for cid, v in KUEBIKO_CHECKS.items()}

    # -- page setup -------------------------------------------------------

    def _load(self, page, url: str) -> None:
        response = page.goto(url, wait_until="load")
        if response is not None and not response.ok:
            raise PageNotLoaded(f"HTTP {response.status}")
        for step in self.steps:
            replay(page, url, step)
        page.add_script_tag(content=self.assets["axe"])
        page.add_script_tag(content=self.assets["page"])

    def _base(self, url: str, viewport: dict, extra_steps: list[dict] = (), description: str | None = None) -> dict:
        steps = [*self.steps, *extra_steps]
        return {"run_id": self.run_id, "target": self.target, "url": url, "steps": steps, "viewport": viewport,
                "description": description or ("Page on load" if not steps else f"Page after {len(steps)} steps")}

    def _artifact(self, url: str, name: str, content: str) -> str:
        rel = f"artifacts/{slug(page_of(url))}/{name}"
        path = self.run_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return rel

    # -- the scan ---------------------------------------------------------

    def scan(self, url: str) -> tuple[list[dict], list[str]]:
        """Scan one page. Returns its findings and the same-origin links on it."""
        page = self.context.new_page()
        page.on("popup", lambda popup: popup.close())
        try:
            page.set_viewport_size(VIEWPORT)
            self._load(page, url)
            links = page.evaluate("[...document.links].map(a => a.href)")
            records = self._axe_and_sr(page, url)
            baseline_clipped = page.evaluate("__kuebiko.clipped()")
            records += self._keyboard(page, url)
            records += self._reflow(page, url)
            records += self._zoom(page, url, baseline_clipped)
        finally:
            page.close()
        origin = urlsplit(url)[:2]
        same = [urldefrag(urljoin(url, h))[0] for h in links]
        return records, [h for h in same if urlsplit(h)[:2] == origin]

    def _axe_and_sr(self, page, url: str) -> list[dict]:
        result = page.evaluate(
            """async (tags) => {
                const r = await axe.run(document, {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']});
                for (const v of r.violations) for (const n of v.nodes)
                    n.selector = __kuebiko.ownSelector(n.target) || n.target.flat().join(' ');
                return {version: axe.version, violations: r.violations, rules: axe.getRules(tags)};
            }""",
            AXE_TAGS,
        )
        rules = {r["ruleId"]: r for r in result["rules"]}
        for rule_id, meta in rules.items():
            if crit := criteria_of(meta["tags"]):
                self.rules[f"axe:{rule_id}"] = crit
        selectors = [n["selector"] for v in result["violations"] for n in v["nodes"]]
        sr = page.evaluate("([src, sels, max]) => __kuebiko.srWalk(src, sels, max)",
                           [self.assets["vsr"], selectors, MAX_SR_STEPS])
        self._artifact(url, "sr-transcript.txt", "\n".join(sr["log"]) + "\n")
        self.default_contrast = {n["selector"] for v in result["violations"] if v["id"] == "color-contrast"
                                 for n in v["nodes"]}
        return axe_records(result["violations"], rules, sr["bySelector"], axe_version=result["version"],
                           **self._base(url, VIEWPORT))

    def _contrast(self, page, selector: str) -> list[dict]:
        return page.evaluate(
            """async (sel) => {
                const el = document.querySelector(sel);
                if (!el) return [];
                const r = await axe.run(el, {runOnly: ['color-contrast'], resultTypes: ['violations']});
                return r.violations.flatMap(v => v.nodes.map(n => ({
                    selector: __kuebiko.ownSelector(n.target) || n.target.flat().join(' '),
                    html: n.html, data: (n.any[0] || {}).data || null})));
            }""",
            selector,
        )

    def _keyboard(self, page, url: str) -> list[dict]:
        """Tab through the page once: keyboard trap, focus visibility, contrast per state."""
        records: list[dict] = []
        page.mouse.move(0, 0)
        count = page.evaluate("__kuebiko.tabbableCount()")
        trace: list[str | None] = []
        seen: set[str] = set()
        prev = None
        trace_path = f"artifacts/{slug(page_of(url))}/keyboard-trace.json"
        trace_evidence = {"kind": "keyboard-trace", "path": trace_path}

        def add(check_id, element, evidence, extra_steps=()):
            records.append(kuebiko_record(check_id, element, evidence, **self._base(url, VIEWPORT, extra_steps)))

        for _ in range(min(count + 2, MAX_TABS)):
            page.keyboard.press("Tab")
            info = page.evaluate("__kuebiko.active()")
            trace.append(info and info["selector"])
            if info is None:
                if seen:
                    break  # focus left the document after the last element
                continue
            sel = info["selector"]
            if sel == prev and info["tag"] != "iframe" and count > 1:
                page.keyboard.press("Shift+Tab")
                back = page.evaluate("__kuebiko.active()")
                trace.append(back and back["selector"])
                if back and back["selector"] == sel:
                    add("kuebiko:keyboard-trap", info, [trace_evidence, {
                        "kind": "log", "text": f"Tab and Shift+Tab both left focus on {sel}; "
                                               f"{count} elements are tabbable."}])
                break
            if sel in seen:
                break  # wrapped round to the start
            seen.add(sel)
            prev = sel
            if info["tag"] == "iframe":
                continue
            focus_step = [{"action": "focus", "selector": sel}]
            for node in self._contrast(page, sel):
                if node["selector"] not in self.default_contrast:
                    add("kuebiko:contrast-focus", node, [{"kind": "contrast", "data": node["data"] or {}},
                                                        {"kind": "dom", "text": node["html"][:4000]}], focus_step)
            reason = self._focus_invisible(page, info)
            if reason:
                add("kuebiko:focus-visible", info, [trace_evidence, {"kind": "log", "text": reason}])
            r = info["rect"]
            if r["width"] >= 1 and r["height"] >= 1:
                page.mouse.move(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
                for node in self._contrast(page, sel):
                    if node["selector"] not in self.default_contrast:
                        add("kuebiko:contrast-hover", node, [{"kind": "contrast", "data": node["data"] or {}},
                                                            {"kind": "dom", "text": node["html"][:4000]}],
                            [{"action": "hover", "selector": sel}])
                page.mouse.move(0, 0)
            page.evaluate("s => document.querySelector(s)?.focus()", sel)
        self._artifact(url, "keyboard-trace.json", json.dumps({"tabbable": count, "focus": trace}, indent=1))
        return records

    def _focus_invisible(self, page, info: dict) -> str | None:
        """Why focus on this element is invisible, or None when focusing it changes the screen."""
        r, vp = info["rect"], info["viewport"]
        if r["width"] < 1 or r["height"] < 1:
            return "The focused element has no size."
        if r["x"] + r["width"] <= 0 or r["y"] + r["height"] <= 0 or r["x"] >= vp["width"] or r["y"] >= vp["height"]:
            return "The focused element is outside the viewport."
        pad = 8  # room for outlines and offsets drawn outside the box
        x0, y0 = max(0, r["x"] - pad), max(0, r["y"] - pad)
        clip = {"x": x0, "y": y0, "width": min(vp["width"], r["x"] + r["width"] + pad) - x0,
                "height": min(vp["height"], r["y"] + r["height"] + pad) - y0}
        focused = page.screenshot(clip=clip, animations="disabled")
        page.evaluate("document.activeElement && document.activeElement.blur()")
        blurred = page.screenshot(clip=clip, animations="disabled")
        if focused == blurred:
            return f"Screenshots of the element and {pad} px around it are identical with and without focus."
        return None

    def _reflow(self, page, url: str) -> list[dict]:
        page.set_viewport_size(REFLOW_VIEWPORT)
        self._load(page, url)
        base = self._base(url, REFLOW_VIEWPORT)
        return [kuebiko_record("kuebiko:reflow-320", el, [
            {"kind": "dom", "text": el["html"][:4000]},
            {"kind": "log", "text": f"Right edge at {el['right']:.0f} px in a {el['viewport']} px wide viewport."},
        ], **base) for el in page.evaluate("n => __kuebiko.overflowing(n)", MAX_OVERFLOW)]

    def _zoom(self, page, url: str, baseline: dict) -> list[dict]:
        page.set_viewport_size(ZOOM_VIEWPORT)
        self._load(page, url)
        base = self._base(url, ZOOM_VIEWPORT, description="Page on load at 200% zoom")
        return [kuebiko_record("kuebiko:zoom-200", {"selector": sel, "html": el["html"]}, [
            {"kind": "dom", "text": el["html"][:4000]},
            {"kind": "log", "text": f"Box {el['client'][0]}x{el['client'][1]} px holds content "
                                    f"{el['scroll'][0]}x{el['scroll'][1]} px with overflow hidden; "
                                    "not clipped at 100%."},
        ], **base) for sel, el in page.evaluate("__kuebiko.clipped()").items() if sel not in baseline]


def replay(page, url: str, step: dict) -> None:
    """Replay one step from the finding schema's step shape."""
    action = step["action"]
    if action == "goto":
        page.goto(urljoin(url, step["url"]), wait_until="load")
    elif action == "click":
        page.click(step["selector"])
    elif action == "fill":
        page.fill(step["selector"], step["value"])
    elif action == "press":
        if "selector" in step:
            page.press(step["selector"], step["key"])
        else:
            page.keyboard.press(step["key"])
    elif action == "hover":
        page.hover(step["selector"])
    elif action == "focus":
        page.focus(step["selector"])
    elif action == "select":
        page.select_option(step["selector"], step["value"])
    elif action == "wait":
        page.wait_for_timeout(step["ms"])
