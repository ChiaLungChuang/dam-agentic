"""Typed tool returns — pydantic models, the roxygen-discipline of the spec in a
new syntax. These describe the *shape* the model sees. Note what is absent from
every model: an activity array, a per-timepoint series, a raw count. The types
themselves encode the boundary.

The engine returns plain dicts (so it is testable without pydantic and without an
MCP runtime); the server validates them through these models on the way out, so a
regression that tried to leak a raw series would fail validation here.

On the boundary as a *type* (spec rule 2, hardened): a metric summary is
`dict[str, MetricValue]`, and MetricValue admits only a list of scalar-only
SummaryRow objects, a list of plain strings, or a lone scalar. There is no field
anywhere that accepts a bare list of numbers. So an activity series cannot be
returned even by mistake — it fails validation here rather than being noticed by
counting how many numbers came back.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)


class MonitorSummary(BaseModel):
    file: str
    n_reads: int
    n_channels: Optional[int] = None
    first_ts: str
    last_ts: str
    bin_seconds: Optional[int] = None


class LoadResult(BaseModel):
    session_id: str
    name: str
    n_monitors: int
    monitors: list[MonitorSummary]
    monitor_keys: list[str] = Field(
        default_factory=list,
        description=(
            "The exact monitor keys to use in later calls (assign_groups, "
            "apply_exclusions). These are filenames, not paths: the caller is "
            "usually handed full paths, so the canonical form is stated here "
            "rather than left to be guessed."
        ),
    )
    time_window: list[str]                 # [common_start, common_end]
    warnings: list[str] = Field(default_factory=list)
    next: str = Field(
        default="Call run_qc next. Metrics computed before QC are not trustworthy.",
    )


class ChannelFlag(BaseModel):
    monitor: str
    channel: int = Field(ge=1, le=32)
    state: Literal["empty", "died", "suspect"]
    evidence: str = Field(description="Why this call was made — shown to the user.")
    last_movement: Optional[str] = None


class QCResult(BaseModel):
    session_id: str
    death_hours: float
    tally: dict[str, dict[str, int]]       # keyed by monitor -> {state: count}
    flags: list[ChannelFlag]
    decisions_required: list[str]
    report_uri: str


class GroupResult(BaseModel):
    session_id: str
    group_sizes: dict[str, int]
    unassigned: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    note: str = (
        "Group labels are human-authored. The model never infers genotype or "
        "condition from the data."
    )


class ExclusionResult(BaseModel):
    session_id: str
    applied: bool
    n_by_group: dict[str, int]
    excluded: list[str]
    # How many assigned channels this call actually removes. A request that matches
    # nothing reports 0 rather than succeeding silently — the caller can tell
    # "excluded two flies" from "excluded nobody", which the return value alone
    # previously could not express.
    n_before: int = 0
    n_after: int = 0
    n_excluded: int = 0
    reason: Optional[str] = None
    message: str


class WindowResult(BaseModel):
    session_id: str
    start: Optional[str] = None
    end: Optional[str] = None
    tally: dict[str, dict[str, int]]       # keyed by monitor -> {state: count}
    monitors_dropped: list[str] = Field(
        default_factory=list,
        description=(
            "Monitors the window excluded entirely. The tally above covers only the "
            "monitors that survived, so without this a truncated dataset looks like "
            "a clean one — and everything downstream (grouping, exclusions, metrics, "
            "contrasts) would run on it silently."
        ),
    )
    decisions_required: list[str]
    message: str


class TradeoffRow(BaseModel):
    end: str
    hours_from_start: float
    n_alive: int
    n_died: int
    n_empty: int
    n_suspect: int


class TradeoffResult(BaseModel):
    session_id: str
    common_start: str
    rows: list[TradeoffRow]
    note: str = (
        "Each row RE-CLASSIFIES the whole inventory independently over "
        "[start, end]. It does not carry deaths forward, so n_died means 'would "
        "be called dead if recording stopped here', NOT 'dead by this time', and "
        "every row sums to the same channel total. n_alive is therefore NOT "
        "monotonic in the window length — on real data it falls and rises again, "
        "tracking whether the candidate end lands in dark or in light, because "
        "the trailing-zero threshold and the dark phase are the same length. "
        "Use the first and last rows; the intermediate rows cannot be used to "
        "choose a window. See docs/HANDOFF-11. Pick the window before applying "
        "exclusions."
    )


class SummaryRow(BaseModel):
    """One row of an aggregate table. Every field is a bounded scalar and no other
    key is allowed (extra="forbid"), so a dict carrying a raw series — or any
    unexpected array-valued field — fails to validate. This is the type-level form
    of "the model never sees the data"."""

    model_config = ConfigDict(extra="forbid")

    # identity / grouping (mostly None in group-level summaries)
    id: Optional[str] = None
    labels: Optional[str] = None
    order: Optional[float] = None
    channel: Optional[float] = None
    monitor: Optional[str] = None
    x: Optional[str] = None
    # descriptive statistics (activity.summary_stats output)
    n: Optional[int] = None
    mean: Optional[float] = None
    sd: Optional[float] = None
    median: Optional[float] = None
    sem: Optional[float] = None
    q1: Optional[float] = None
    q3: Optional[float] = None
    y: Optional[float] = None
    # rhythmicity fraction rows
    n_rhythmic: Optional[int] = None
    rhythmic_fraction: Optional[float] = None
    # pairwise log-rank rows
    group1: Optional[str] = None
    group2: Optional[str] = None
    test_statistic: Optional[float] = None
    p_value: Optional[float] = None


# A metric-summary value: a table of scalar-only rows, a list of decision strings,
# or a lone scalar (e.g. immobility_minutes, method). StrictStr on the string list
# stops a numeric array from being silently coerced into strings — a raw series
# matches none of these arms and is a validation error.
MetricValue = Union[
    list[SummaryRow],
    list[StrictStr],
    StrictBool,
    StrictInt,
    StrictFloat,
    StrictStr,
    None,
]


class MetricResult(BaseModel):
    session_id: str
    metric: str
    summary: dict[str, MetricValue]        # scalar-only tables; never a raw series
    decisions_required: list[str] = Field(default_factory=list)
    resource_uri: str


class EffectSize(BaseModel):
    kind: str
    value: Optional[float] = None


class ContrastResult(BaseModel):
    session_id: str
    contrast_id: str
    metric: str
    phase: str
    groups: list[str]
    test: str
    n: dict[str, int]
    median: dict[str, float]
    statistic: float
    p_value: float
    effect_size: EffectSize
    exclusions_applied: int
    rationale: Optional[str] = None
