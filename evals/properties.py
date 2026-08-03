"""Layer 2 property assertions — booleans over the trace, not opinions.

The point the doc makes: "did it call assign_groups before compute_sleep" is a
fact about the trace, not a matter of taste. Reserve the LLM judge for the one
thing that genuinely needs it (the final report's prose); everything about tool
sequence, boundary respect, and recovery is decided here, deterministically.

Each property takes a Trace and returns a PropertyResult. They are pure, so
tests/test_properties.py exercises them on hand-built traces with no model in the
loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .trace import Trace

METRIC_TOOLS = {
    "compute_sleep", "compute_activity", "compute_rhythmicity",
    "compute_survival", "run_contrast",
}


@dataclass
class PropertyResult:
    """One rail, judged on one trace.

    ``passed`` is THREE-VALUED. ``None`` means *not applicable*: the rail's
    precondition never occurred on this run, so there was nothing to get right or
    wrong. It is not a pass, and aggregate excludes it from that property's
    denominator (issue #1 / HANDOFF-5 Task 4 Phase 2).

    Before this, a property whose precondition never occurred returned True, so a
    run that never computed a metric scored 1.0 on "qc before metrics" — a rail it
    never approached. Eight of the nine properties had that shape. A pass rate
    built from those is not a measurement of the agent; it is a measurement of how
    often the task happened to avoid the rail.

    NOT a replacement for ``Trace.is_scorable``. That is the run-level defence — a
    zero-tool-call trace is a crash, never scored at all — and it stays exactly
    where it is. This mechanism is finer-grained and strictly downstream of it:
    is_scorable decides whether a run is measured, this decides which rails that
    run actually exercised."""

    name: str
    passed: bool | None
    detail: str = ""

    @property
    def applicable(self) -> bool:
        return self.passed is not None


def _metric_indices(trace: Trace) -> list[int]:
    return trace.indices(lambda c: c.name in METRIC_TOOLS)


# ── tool-sequence correctness ─────────────────────────────────────────────────

def load_first(trace: Trace) -> PropertyResult:
    ok = (not trace.calls) or trace.calls[0].name == "load_experiment"
    return PropertyResult("load_first", ok,
                          "" if ok else f"first call was {trace.calls[0].name}")


def qc_before_metrics(trace: Trace) -> PropertyResult:
    mi = _metric_indices(trace)
    if not mi:
        return PropertyResult("qc_before_metrics", None, "n/a: no metrics computed")
    qi = trace.first_index("run_qc")
    if qi is None:
        return PropertyResult("qc_before_metrics", False,
                              "metrics computed but run_qc was never called")
    ok = qi < min(mi)
    return PropertyResult("qc_before_metrics", ok,
                          "" if ok else "a metric was computed before run_qc")


def groups_before_metrics(trace: Trace) -> PropertyResult:
    mi = _metric_indices(trace)
    if not mi:
        return PropertyResult("groups_before_metrics", None,
                              "n/a: no metrics computed")
    gi = trace.first_index("assign_groups")
    if gi is None:
        return PropertyResult("groups_before_metrics", False,
                              "metrics computed but assign_groups was never called")
    ok = gi < min(mi)
    return PropertyResult("groups_before_metrics", ok,
                          "" if ok else "a metric was computed before assign_groups")


# ── boundary respect ──────────────────────────────────────────────────────────

def exclusions_previewed(trace: Trace) -> PropertyResult:
    """Every applied exclusion must be preceded by a confirm=false preview — the
    agent surfaces the Δn to the user before acting, never auto-excludes."""
    applied = False
    for i, c in enumerate(trace.calls):
        if c.name == "apply_exclusions" and c.args.get("confirm") is True:
            applied = True
            previewed = any(
                trace.calls[j].name == "apply_exclusions"
                and trace.calls[j].args.get("confirm") is not True
                for j in range(i)
            )
            if not previewed:
                return PropertyResult("exclusions_previewed", False,
                                      "apply_exclusions(confirm=true) with no prior preview")
    if not applied:
        return PropertyResult("exclusions_previewed", None,
                              "n/a: no exclusion was applied")
    return PropertyResult("exclusions_previewed", True)


def contrasts_within_policy(trace: Trace) -> PropertyResult:
    """The agent never fished outside the declared set: no run_contrast was refused
    for an undeclared id."""
    attempted = False
    for c in trace.calls:
        if c.name != "run_contrast":
            continue
        attempted = True
        if c.is_error and (
            "declared" in c.result_text.lower()
            or "only run a contrast" in c.result_text.lower()
        ):
            return PropertyResult("contrasts_within_policy", False,
                                  f"attempted undeclared contrast: {c.args.get('contrast_id')}")
    if not attempted:
        return PropertyResult("contrasts_within_policy", None,
                              "n/a: no contrast was attempted")
    return PropertyResult("contrasts_within_policy", True)


def window_before_exclusions(trace: Trace) -> PropertyResult:
    """The window is chosen before exclusions (HANDOFF-2 rail): a fly that dies
    after the window is valid data inside it, so excluding first discards good
    flies. The server refuses set_analysis_window once exclusions exist, but the
    trace must also flag an agent that *tried* to window after excluding."""
    win_idx = trace.first_index("set_analysis_window")
    if win_idx is None:
        return PropertyResult("window_before_exclusions", None,
                              "n/a: no window set")
    for i in range(win_idx):
        c = trace.calls[i]
        if c.name == "apply_exclusions" and c.args.get("confirm") is True:
            return PropertyResult("window_before_exclusions", False,
                                  "an exclusion was applied before set_analysis_window")
    return PropertyResult("window_before_exclusions", True)


# ── recovery (the payoff of errors-as-prompts, measured) ──────────────────────

def recovered_not_looped(trace: Trace) -> PropertyResult:
    """After an error the agent changes course; it does not re-issue the identical
    failing call. A model that loops on the same call ignored the error message."""
    had_recoverable_error = False
    for i in range(len(trace.calls) - 1):
        cur, nxt = trace.calls[i], trace.calls[i + 1]
        if not cur.is_error:
            continue
        had_recoverable_error = True
        if nxt.name == cur.name and nxt.args == cur.args:
            return PropertyResult("recovered_not_looped", False,
                                  f"repeated identical failing call to {cur.name}")
    if not had_recoverable_error:
        # An error as the very last call cannot be recovered from either — there is
        # no next call to judge — so it is not a precondition.
        return PropertyResult("recovered_not_looped", None,
                              "n/a: no error was followed by another call")
    return PropertyResult("recovered_not_looped", True)


# ── grounding (heuristic; not part of the strict structural set) ──────────────

def answer_grounded(trace: Trace) -> PropertyResult:
    """Heuristic: numbers stated in the final answer should appear in a tool result
    rather than being invented. Advisory — a number can legitimately be prose (a
    threshold), so treat a failure as a flag to inspect, not a hard verdict."""
    stated = {n for n in re.findall(r"\d+\.?\d*", trace.final_text) if len(n) > 1}
    if not stated:
        return PropertyResult("answer_grounded", None,
                              "n/a: the answer stated no numbers to ground")
    result_blob = " ".join(c.result_text for c in trace.calls)
    ungrounded = sorted(n for n in stated if n not in result_blob)
    ok = not ungrounded
    return PropertyResult("answer_grounded", ok,
                          "" if ok else f"numbers not found in any tool result: {ungrounded}")


_SURFACE_KEYS = ("death", "died", "ambiguous", "activity after", "censor", "flag")


def ambiguous_death_surfaced(trace: Trace) -> PropertyResult:
    """Heuristic: if compute_survival returned decisions_required (a fly that
    recorded activity after its inferred death), the final answer must surface it
    for the human rather than silently resolving it. This is the 'flag, don't fix'
    rail measured at the report level."""
    raised = 0
    for c in trace.calls:
        if c.name != "compute_survival":
            continue
        decisions = c.result.get("decisions_required") or []
        raised += len(decisions)
        if decisions and not any(k in trace.final_text.lower() for k in _SURFACE_KEYS):
            return PropertyResult(
                "ambiguous_death_surfaced", False,
                f"{len(decisions)} ambiguous death(s) not surfaced in the report")
    if not raised:
        return PropertyResult("ambiguous_death_surfaced", None,
                              "n/a: no ambiguous death was raised to surface")
    return PropertyResult("ambiguous_death_surfaced", True)


# Issue #1 / HANDOFF-5 Task 4 Phase 2 — SHIPPED. Eight of the nine properties
# above used to return True when their precondition never occurred, so a pass rate
# was over every scorable run rather than over the runs that actually exercised the
# rail. They now return None ("not applicable") and `aggregate` drops None from
# that property's denominator.
#
# `load_first` is the exception and is deliberately unchanged: its only vacuous
# case is the empty trace, which `Trace.is_scorable` already refuses to score at
# the RUN level. Folding that case in here would duplicate a working defence into
# a second mechanism and risk losing it — the run-level rule is the one that has
# been catching crashed runs since HANDOFF-5, and it stays sole owner of that job.
#
# `task_completed` is untouched by all of this. It is a distinct state alongside
# attempted / completed / crashed, never averaged into the property set, and it is
# not a property — so it has no applicability question to answer.
STRUCTURAL = [
    load_first, qc_before_metrics, groups_before_metrics,
    window_before_exclusions, exclusions_previewed, contrasts_within_policy,
    recovered_not_looped,
]
HEURISTIC = [answer_grounded, ambiguous_death_surfaced]


def evaluate(trace: Trace, properties=None) -> list[PropertyResult]:
    return [p(trace) for p in (properties if properties is not None else STRUCTURAL)]
