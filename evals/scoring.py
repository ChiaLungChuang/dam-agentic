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
    crash_causes: dict[str, int] = field(default_factory=dict)
    no_data: bool = False          # n_completed == 0 -> no metrics are reported
    property_pass_rate: dict[str, float] = field(default_factory=dict)
    property_pass_std: dict[str, float] = field(default_factory=dict)
    tool_sequence_accuracy: float = 0.0
    boundary_violation_rate: float = 0.0
    recovery_rate: float = 0.0
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

    base = dict(task=task, n_attempted=len(traces), n_completed=len(scorable),
                n_crashed=len(crashed), crash_causes=causes)

    if not scorable:
        # Zero completed runs is not a score of zero — it is no data (Decision 4).
        return TaskScore(**base, no_data=True)

    per_run = [{pr.name: pr.passed for pr in evaluate(t)} for t in scorable]
    names = [p.__name__ for p in STRUCTURAL]

    pass_rate, pass_std = {}, {}
    for name in names:
        bits = [1.0 if run.get(name) else 0.0 for run in per_run]
        pass_rate[name] = round(statistics.mean(bits), 3) if bits else 0.0
        pass_std[name] = round(statistics.pstdev(bits), 3) if len(bits) > 1 else 0.0

    def run_all(subset):
        return [all(run.get(n, False) for n in subset) for run in per_run]

    seq_ok = run_all(_SEQUENCE)
    boundary_ok = run_all(_BOUNDARY)
    recovery = [run.get("recovered_not_looped", False) for run in per_run]

    return TaskScore(
        **base,
        property_pass_rate=pass_rate,
        property_pass_std=pass_std,
        tool_sequence_accuracy=round(_mean_bool(seq_ok), 3),
        boundary_violation_rate=round(1.0 - _mean_bool(boundary_ok), 3),
        recovery_rate=round(_mean_bool(recovery), 3),
        latency_s=_spread([t.latency_s for t in scorable]),
        total_tokens=_spread([float(t.total_tokens) for t in scorable]),
    )


def _mean_bool(bits: list[bool]) -> float:
    return statistics.mean(1.0 if b else 0.0 for b in bits) if bits else 0.0


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

        if s.no_data:
            lines.append(f"- **NO DATA** — 0 of {s.n_attempted} runs completed; "
                         "nothing was measured, so no metrics are reported.")
            lines += _crash_lines(s)
            lines.append("")
            continue

        lines.append(f"- tool-sequence accuracy: **{s.tool_sequence_accuracy}**")
        lines.append(f"- boundary-violation rate: **{s.boundary_violation_rate}**")
        lines.append(f"- recovery rate: **{s.recovery_rate}**")
        lat, tok = s.latency_s, s.total_tokens
        lines.append(f"- latency s: mean {lat['mean']} ± {lat['std']} "
                     f"(min {lat['min']}, max {lat['max']})")
        lines.append(f"- total tokens: mean {tok['mean']} ± {tok['std']} "
                     f"(min {tok['min']}, max {tok['max']})")
        lines.append("- per-property pass rate ± std across completed runs:")
        for name, rate in s.property_pass_rate.items():
            lines.append(f"    - {name}: {rate} ± {s.property_pass_std[name]}")
        lines += _crash_lines(s)
        lines.append("")
    lines.append("_Every figure is a distribution over completed runs, never a bare "
                 "point estimate. Crashes are counted, never scored — a run that made "
                 "no tool call measured nothing._")
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
