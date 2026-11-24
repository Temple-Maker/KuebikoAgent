"""Score labelled cases against the findings a tool emitted.

A case is flagged when the tool emits at least one ``failed`` finding on the
case's page for a success criterion the case's rule maps to. A positive is a
case whose expected outcome is ``failed``. See AGENTS.md and corpus/README.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from kuebiko.evals.act import Case

# Per-criterion rates are only reported for criteria with at least this many cases;
# corpus/README.md says the subset does not support finer claims.
MIN_CASES_PER_CRITERION = 20


def failed_criteria(findings: Iterable[Mapping]) -> set[str]:
    return {f["criterion"]["id"] for f in findings if f.get("outcome") == "failed"}


def _rates(cases: list[Case], flagged: Mapping[str, bool]) -> dict:
    positives = sum(c.positive for c in cases)
    tp = sum(c.positive and flagged[c.key] for c in cases)
    fp = sum(not c.positive and flagged[c.key] for c in cases)
    n_flagged = tp + fp
    return {
        "cases": len(cases),
        "positives": positives,
        "flagged": n_flagged,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": positives - tp,
        "recall": round(tp / positives, 4) if positives else None,
        "false_discovery_rate": round(fp / n_flagged, 4) if n_flagged else None,
    }


def score(cases: list[Case], found: Mapping[str, set[str]]) -> dict:
    """``found`` maps a case key to the criteria the tool reported as failed on its page."""
    flagged = {c.key: bool(c.criteria & found.get(c.key, set())) for c in cases}
    result = _rates(cases, flagged)
    per_criterion = {}
    for criterion in sorted({sc for c in cases for sc in c.criteria}):
        subset = [c for c in cases if criterion in c.criteria]
        if len(subset) >= MIN_CASES_PER_CRITERION:
            # Here a case counts as flagged only by a finding for this criterion.
            per_criterion[criterion] = _rates(
                subset, {c.key: criterion in found.get(c.key, set()) for c in subset}
            )
    result["per_criterion"] = per_criterion
    return result
