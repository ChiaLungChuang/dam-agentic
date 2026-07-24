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
    n_runs: int
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
    per_run = [{pr.name: pr.passed for pr in evaluate(t)} for t in traces]
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
        task=task,
        n_runs=len(traces),
        property_pass_rate=pass_rate,
        property_pass_std=pass_std,
        tool_sequence_accuracy=round(_mean_bool(seq_ok), 3),
        boundary_violation_rate=round(1.0 - _mean_bool(boundary_ok), 3),
        recovery_rate=round(_mean_bool(recovery), 3),
        latency_s=_spread([t.latency_s for t in traces]),
        total_tokens=_spread([float(t.total_tokens) for t in traces]),
    )


def _mean_bool(bits: list[bool]) -> float:
    return statistics.mean(1.0 if b else 0.0 for b in bits) if bits else 0.0


def format_report(scores: list[TaskScore], model_id: str | None = None) -> str:
    lines = ["# Agentic eval report", ""]
    if model_id:
        # A property-violation rate without a model identifier is not a result.
        lines += [f"**Model:** `{model_id}`", ""]
    for s in scores:
        lines.append(f"## {s.task}  (n={s.n_runs} runs)")
        lines.append(f"- tool-sequence accuracy: **{s.tool_sequence_accuracy}**")
        lines.append(f"- boundary-violation rate: **{s.boundary_violation_rate}**")
        lines.append(f"- recovery rate: **{s.recovery_rate}**")
        lat, tok = s.latency_s, s.total_tokens
        lines.append(f"- latency s: mean {lat['mean']} ± {lat['std']} "
                     f"(min {lat['min']}, max {lat['max']})")
        lines.append(f"- total tokens: mean {tok['mean']} ± {tok['std']} "
                     f"(min {tok['min']}, max {tok['max']})")
        lines.append("- per-property pass rate ± std across runs:")
        for name, rate in s.property_pass_rate.items():
            lines.append(f"    - {name}: {rate} ± {s.property_pass_std[name]}")
        lines.append("")
    lines.append("_Every figure is a distribution over runs, never a bare point "
                 "estimate — the agent is stochastic and the spread is the finding._")
    return "\n".join(lines)
