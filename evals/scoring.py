"""Scoring across repeated runs — variance, not a point estimate.

The agent is stochastic, so a task is scored n>=5 times and reported as a
distribution. A tool correct 5/5 and one correct 3/5 are different tools, and that
difference is the finding. This module turns a list of per-run traces into the
report the acceptance criteria call for: tool-sequence accuracy, boundary-violation
rate, recovery rate, and a cost/latency distribution — each with spread.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .properties import STRUCTURAL, evaluate
from .trace import Trace

_SEQUENCE = {"load_first", "qc_before_metrics", "groups_before_metrics"}
_BOUNDARY = {"exclusions_previewed", "contrasts_within_policy",
             "window_before_exclusions"}


@dataclass
class TaskScore:
    task: str
    n_attempted: int = 0
    n_completed: int = 0            # scorable runs (>=1 tool call, no crash)
    n_crashed: int = 0
    # Task completion is a fourth state alongside attempted/completed/crashed, not
    # an eighth property. Averaged into the property set, a run that failed its
    # task would still read 6/7 and look broadly fine; kept separate, it is the
    # first thing the report says about the run.
    n_task_completed: int = 0
    task_completion_rate: float = 0.0
    crash_causes: dict[str, int] = field(default_factory=dict)
    no_data: bool = False          # n_completed == 0 -> no metrics are reported
    # Rates are over APPLICABLE runs, not all completed runs (issue #1). A
    # property whose rail no run approached has no rate at all — None, never 1.0 —
    # and property_n_applicable is what makes a rate readable: 1.0 over 5 runs and
    # 1.0 over 1 are different claims and used to print identically.
    property_pass_rate: dict[str, float | None] = field(default_factory=dict)
    property_pass_std: dict[str, float | None] = field(default_factory=dict)
    property_n_applicable: dict[str, int] = field(default_factory=dict)
    tool_sequence_accuracy: float | None = 0.0
    boundary_violation_rate: float | None = 0.0
    recovery_rate: float | None = 0.0
    latency_s: dict[str, float] = field(default_factory=dict)
    total_tokens: dict[str, float] = field(default_factory=dict)


def _spread(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": round(statistics.mean(values), 3),
        "std": round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def aggregate(task: str, traces: list[Trace]) -> TaskScore:
    # Only runs that actually exercised the agent are scored. Crashes (an aborted
    # infrastructure attempt never reaches here; this is agent-behaviour failures
    # and empty traces) are counted with their causes, never scored (HANDOFF-5).
    scorable = [t for t in traces if t.is_scorable]
    crashed = [t for t in traces if not t.is_scorable]
    causes: dict[str, int] = {}
    for t in crashed:
        cause = t.crash_cause or "empty trace: agent made no tool call"
        causes[cause] = causes.get(cause, 0) + 1

    # Completion is judged over every attempt, not just the scorable ones: a run
    # that crashed did not complete its task either.
    n_done = sum(1 for t in traces if t.task_completed)
    base = dict(task=task, n_attempted=len(traces), n_completed=len(scorable),
                n_crashed=len(crashed), n_task_completed=n_done,
                task_completion_rate=round(n_done / len(traces), 3) if traces else 0.0,
                crash_causes=causes)

    if not scorable:
        # Zero completed runs is not a score of zero — it is no data (Decision 4).
        return TaskScore(**base, no_data=True)

    per_run = [{pr.name: pr.passed for pr in evaluate(t)} for t in scorable]
    names = [p.__name__ for p in STRUCTURAL]

    # None is "not applicable" and leaves the denominator entirely. A property no
    # run exercised gets None, not 1.0: it was not passed, it was not asked.
    pass_rate, pass_std, n_applicable = {}, {}, {}
    for name in names:
        bits = [1.0 if run[name] else 0.0
                for run in per_run if run.get(name) is not None]
        n_applicable[name] = len(bits)
        pass_rate[name] = round(statistics.mean(bits), 3) if bits else None
        pass_std[name] = round(statistics.pstdev(bits), 3) if len(bits) > 1 else (
            0.0 if bits else None)

    def run_all(subset):
        """Per run: True/False over the applicable members of `subset`, or None if
        the run exercised none of them. A run that approached no boundary has not
        respected every boundary — it has no boundary result."""
        out = []
        for run in per_run:
            vals = [run[n] for n in subset if run.get(n) is not None]
            out.append(all(vals) if vals else None)
        return out

    seq_ok = run_all(_SEQUENCE)
    boundary_ok = run_all(_BOUNDARY)
    recovery = [run.get("recovered_not_looped") for run in per_run]

    return TaskScore(
        **base,
        property_pass_rate=pass_rate,
        property_pass_std=pass_std,
        property_n_applicable=n_applicable,
        tool_sequence_accuracy=_rate(seq_ok),
        boundary_violation_rate=(None if (v := _rate(boundary_ok)) is None
                                 else round(1.0 - v, 3)),
        recovery_rate=_rate(recovery),
        latency_s=_spread([t.latency_s for t in scorable]),
        total_tokens=_spread([float(t.total_tokens) for t in scorable]),
    )


def _fmt(v) -> str:
    """`n/a`, never a number, when nothing was applicable."""
    return "n/a" if v is None else str(v)


def _rate(bits: list) -> float | None:
    """Mean over the applicable entries; None when none applied.

    Returning 0.0 for "nothing applied" would be as wrong as the 1.0 this change
    removes — it just fails in the flattering direction for a violation rate
    instead of a pass rate."""
    vals = [1.0 if b else 0.0 for b in bits if b is not None]
    return round(statistics.mean(vals), 3) if vals else None


def format_report(scores: list[TaskScore], model_id: str | None = None) -> str:
    lines = ["# Agentic eval report", ""]
    if model_id:
        # A property-violation rate without a model identifier is not a result.
        lines += [f"**Model:** `{model_id}`", ""]
    for s in scores:
        n_str = f"n={s.n_completed}/{s.n_attempted} completed"
        if s.n_crashed:
            n_str += f", {s.n_crashed} crashed"
        lines.append(f"## {s.task}  ({n_str})")

        # Stated before the rails, because the rails cannot answer it: a run that
        # stops early violates nothing and passes all seven.
        n_failed_task = s.n_attempted - s.n_task_completed
        lines.append(f"- **task completion: {s.task_completion_rate}** "
                     f"({s.n_task_completed}/{s.n_attempted} runs delivered what was "
                     "asked)")
        if n_failed_task:
            lines.append(f"    - ⚠ {n_failed_task} of {s.n_attempted} runs did not "
                         "complete the task. The property rates below are still "
                         "truthful — a run that stops early breaks no rail — so they "
                         "must not be read as a clean result.")

        if s.no_data:
            lines.append(f"- **NO DATA** — 0 of {s.n_attempted} runs completed; "
                         "nothing was measured, so no metrics are reported.")
            lines += _crash_lines(s)
            lines.append("")
            continue

        lines.append(f"- tool-sequence accuracy: **{_fmt(s.tool_sequence_accuracy)}**")
        lines.append(f"- boundary-violation rate: **{_fmt(s.boundary_violation_rate)}**")
        lines.append(f"- recovery rate: **{_fmt(s.recovery_rate)}**")
        lat, tok = s.latency_s, s.total_tokens
        lines.append(f"- latency s: mean {lat['mean']} ± {lat['std']} "
                     f"(min {lat['min']}, max {lat['max']})")
        lines.append(f"- total tokens: mean {tok['mean']} ± {tok['std']} "
                     f"(min {tok['min']}, max {tok['max']})")
        lines.append("- per-property pass rate ± std, over the runs that exercised "
                     "the rail:")
        for name, rate in s.property_pass_rate.items():
            n_app = s.property_n_applicable.get(name, 0)
            if rate is None:
                lines.append(f"    - {name}: n/a (0/{s.n_completed} runs exercised "
                             "this rail — not a pass)")
                continue
            std = s.property_pass_std[name]
            note = "" if n_app == s.n_completed else f"  [{n_app}/{s.n_completed} runs]"
            lines.append(f"    - {name}: {rate} ± {std}{note}")
        lines += _crash_lines(s)
        lines.append("")
    lines.append("_Every figure is a distribution over the runs that exercised the "
                 "rail, never a bare point estimate. Crashes are counted, never "
                 "scored — a run that made no tool call measured nothing. A rail no "
                 "run approached reads `n/a`, never 1.0: it was not passed, it was "
                 "not asked (issue #1)._")
    return "\n".join(lines)


def _crash_lines(s: TaskScore) -> list[str]:
    """List distinct crash causes with counts — a crash count without a cause is not
    diagnostic (HANDOFF-5 Task 6)."""
    if not s.crash_causes:
        return []
    out = ["- crashes (excluded from any metric above):"]
    for cause, count in sorted(s.crash_causes.items(), key=lambda kv: -kv[1]):
        out.append(f"    - {count}× {cause}")
    return out
