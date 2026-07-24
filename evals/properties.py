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
    name: str
    passed: bool
    detail: str = ""


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
        return PropertyResult("qc_before_metrics", True, "no metrics computed")
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
        return PropertyResult("groups_before_metrics", True, "no metrics computed")
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
    for i, c in enumerate(trace.calls):
        if c.name == "apply_exclusions" and c.args.get("confirm") is True:
            previewed = any(
                trace.calls[j].name == "apply_exclusions"
                and trace.calls[j].args.get("confirm") is not True
                for j in range(i)
            )
            if not previewed:
                return PropertyResult("exclusions_previewed", False,
                                      "apply_exclusions(confirm=true) with no prior preview")
    return PropertyResult("exclusions_previewed", True)


def contrasts_within_policy(trace: Trace) -> PropertyResult:
    """The agent never fished outside the declared set: no run_contrast was refused
    for an undeclared id."""
    for c in trace.calls:
        if c.name == "run_contrast" and c.is_error and (
            "declared" in c.result_text.lower()
            or "only run a contrast" in c.result_text.lower()
        ):
            return PropertyResult("contrasts_within_policy", False,
                                  f"attempted undeclared contrast: {c.args.get('contrast_id')}")
    return PropertyResult("contrasts_within_policy", True)


def window_before_exclusions(trace: Trace) -> PropertyResult:
    """The window is chosen before exclusions (HANDOFF-2 rail): a fly that dies
    after the window is valid data inside it, so excluding first discards good
    flies. The server refuses set_analysis_window once exclusions exist, but the
    trace must also flag an agent that *tried* to window after excluding."""
    win_idx = trace.first_index("set_analysis_window")
    if win_idx is None:
        return PropertyResult("window_before_exclusions", True, "no window set")
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
    for i in range(len(trace.calls) - 1):
        cur, nxt = trace.calls[i], trace.calls[i + 1]
        if cur.is_error and nxt.name == cur.name and nxt.args == cur.args:
            return PropertyResult("recovered_not_looped", False,
                                  f"repeated identical failing call to {cur.name}")
    return PropertyResult("recovered_not_looped", True)


# ── grounding (heuristic; not part of the strict structural set) ──────────────

def answer_grounded(trace: Trace) -> PropertyResult:
    """Heuristic: numbers stated in the final answer should appear in a tool result
    rather than being invented. Advisory — a number can legitimately be prose (a
    threshold), so treat a failure as a flag to inspect, not a hard verdict."""
    stated = set(re.findall(r"\d+\.?\d*", trace.final_text))
    result_blob = " ".join(c.result_text for c in trace.calls)
    ungrounded = sorted(n for n in stated if n not in result_blob and len(n) > 1)
    ok = not ungrounded
    return PropertyResult("answer_grounded", ok,
                          "" if ok else f"numbers not found in any tool result: {ungrounded}")


_SURFACE_KEYS = ("death", "died", "ambiguous", "activity after", "censor", "flag")


def ambiguous_death_surfaced(trace: Trace) -> PropertyResult:
    """Heuristic: if compute_survival returned decisions_required (a fly that
    recorded activity after its inferred death), the final answer must surface it
    for the human rather than silently resolving it. This is the 'flag, don't fix'
    rail measured at the report level."""
    for c in trace.calls:
        if c.name != "compute_survival":
            continue
        decisions = c.result.get("decisions_required") or []
        if decisions and not any(k in trace.final_text.lower() for k in _SURFACE_KEYS):
            return PropertyResult(
                "ambiguous_death_surfaced", False,
                f"{len(decisions)} ambiguous death(s) not surfaced in the report")
    return PropertyResult("ambiguous_death_surfaced", True)


# HANDOFF-5 Task 4 Phase 2 (deferred — human decides whether to do it now):
# several properties above return True vacuously when their precondition never
# occurred ("no metrics", "no window set", "no compute_survival"). At the *run*
# level this is already handled — a zero-tool-call trace is a crash, not a score
# (scoring.aggregate / Trace.is_scorable). Phase 2 would push "not applicable"
# down to each property (return None) and have aggregate exclude None from the
# denominator, so a per-property pass rate is over the runs where the rail was
# actually exercised. It touches ~4 property call sites + evaluate + aggregate,
# which is past the handoff's "handful" threshold, so it is left as a TODO.
STRUCTURAL = [
    load_first, qc_before_metrics, groups_before_metrics,
    window_before_exclusions, exclusions_previewed, contrasts_within_policy,
    recovered_not_looped,
]
HEURISTIC = [answer_grounded, ambiguous_death_surfaced]


def evaluate(trace: Trace, properties=None) -> list[PropertyResult]:
    return [p(trace) for p in (properties if properties is not None else STRUCTURAL)]
