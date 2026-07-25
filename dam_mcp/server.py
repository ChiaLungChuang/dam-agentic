"""MCP server over the DAM analysis stack.

Ten-ish task-shaped tools and four readable resources. Every tool returns a
summary plus the `session_id` handle; the data stays server-side. The docstrings
are dispatch logic — they tell the model when to use a tool, when not to, and what
comes back — so read them as the API, not as documentation.

Transport is stdio (spec: skip OAuth; that is for remote servers). Run it with the
analysis engine installed (see docs/running.md):

    python -m dam_mcp.server

then point an MCP client (MCP Inspector, Claude Desktop) at that command.

Two protocol-level conventions worth stating:

* Errors are prompts (spec rule 4). Every failure raises ToolError with a message
  written for the model — what happened, why it matters, what to do. The server
  never lets a raw traceback reach the client.
* isError is consistent. A ToolError surfaces as a protocol error (isError=true),
  the same as an unexpected exception would. A refusal is "this call cannot be
  fulfilled as made", so a programmatic client that gates on isError sees it as a
  failure — never a green success carrying an {"error": ...} body. The only
  non-error "stop" is a confirm=false preview, which is a genuine successful
  result the model is meant to read and act on.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import config, engine, report
from .defaults import DEFAULT_DEATH_HOURS
from .errors import ToolError, needs_groups, needs_qc, unknown_session
from .schemas import (
    ChannelFlag,
    ContrastResult,
    ExclusionResult,
    GroupResult,
    LoadResult,
    MetricResult,
    MonitorSummary,
    QCResult,
    TradeoffResult,
    TradeoffRow,
    WindowResult,
)
from .sessions import SessionStore

mcp = FastMCP("dam-tools")
STORE = SessionStore()

# Annotations make the human-in-the-loop boundary visible at the protocol layer,
# where clients actually gate. Pure derivations advertise read-only; the tools
# that change state or write files declare it. (readOnly here means "does not
# change the experiment's data or the scientific decisions" — caching a derived
# summary is still safe and idempotent.)
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _require(session_id: str):
    session = STORE.get(session_id)
    if session is None:
        raise unknown_session(session_id, STORE.list_ids())
    return session


# ── tools ─────────────────────────────────────────────────────────────────────

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False,
    openWorldHint=False))
def load_experiment(paths: list[str], name: str) -> dict:
    """Load DAM monitor files into a new analysis session.

    Use this first, always. Takes the paths to the Monitor*.txt files (not the
    folder) and a human name for the experiment. Returns a session_id used by every
    later call, plus a structural summary: monitor count, reads and channels per
    file, the common time window, inferred bin width, and any warnings.

    Also returns `monitor_keys`: the exact strings to use as the monitor key in
    assign_groups and apply_exclusions. They are filenames, not paths — use them
    verbatim rather than the paths passed in here.

    Does NOT return activity counts — those stay server-side for the whole session.
    Call run_qc next; metrics computed before QC are not trustworthy.
    """
    if not isinstance(paths, list) or not paths:
        raise ToolError(
            "load_experiment needs a non-empty list of monitor file paths. Pass the "
            "Monitor*.txt files themselves, e.g. [\"/data/exp/Monitor1.txt\", ...]."
        )
    monitors, warnings = engine.scan_structural([str(p) for p in paths])
    session = STORE.create(name=name, paths=[str(p) for p in paths])
    session.monitors = monitors
    session.warnings = warnings
    STORE.save(session)

    starts = [m["first_ts"] for m in monitors]
    ends = [m["last_ts"] for m in monitors]
    return LoadResult(
        session_id=session.session_id,
        name=name,
        n_monitors=len(monitors),
        monitors=[MonitorSummary(**{k: m[k] for k in
                  ("file", "n_reads", "n_channels", "first_ts", "last_ts",
                   "bin_seconds")}) for m in monitors],
        monitor_keys=[m["file"] for m in monitors],
        time_window=[max(starts), min(ends)],
        warnings=warnings,
    ).model_dump()


@mcp.tool(annotations=_READ_ONLY)
def describe_experiment(session_id: str) -> dict:
    """Inventory the loaded files: reads vs. expected, missing reads, bad-status
    reads, duplicate timestamps, and the alignment window, per monitor.

    This is Step 1 of the QC skill — the shape of the data before anything touches
    it. Discrepancies between monitors here (one has 4,312 reads, its neighbour
    4,305) are the most informative early signal. Read-only; changes nothing.
    """
    session = _require(session_id)
    out = STORE.session_dir(session_id) / "describe.json"
    qc = engine.run_validate(session.paths, death_hours=DEFAULT_DEATH_HOURS, out_path=out,
                             window=session.window)
    return {
        "session_id": session_id,
        "inventory": qc.get("inventory", []),
        "alignment": qc.get("alignment", {}),
        "unparseable_files": qc.get("unparseable_files", []),
    }


@mcp.tool(annotations=_READ_ONLY)
def run_qc(session_id: str, death_hours: float = DEFAULT_DEATH_HOURS) -> dict:
    """Run quality control and classify every channel as alive/empty/died/suspect.

    Wraps the tested validate_dam.py detector. `death_hours` is the trailing-zero
    window used to call a channel dead — a lab convention, not a constant (this
    lab's default is 12 h); a window that long structurally cannot see a fly that
    died within its final window (stated limitation, not a bug). A shorter window
    sees more late deaths at the cost of calling deep quiescence death — a real
    tradeoff, which window_tradeoff surfaces.

    Returns a per-monitor tally, the flagged channels with the evidence for each
    call, and decisions_required — the list of exclusions a human must rule on. It
    does NOT apply any exclusion; QC flags, it never fixes. Run this after
    load_experiment and before any compute_* tool.
    """
    session = _require(session_id)
    if death_hours <= 0:
        raise ToolError(
            f"death_hours must be positive; got {death_hours}. It is the hours of "
            "continuous silence used to call a channel dead (this lab's default is 12)."
        )
    out = STORE.session_dir(session_id) / f"qc_{death_hours}.json"
    raw = engine.run_validate(session.paths, death_hours=death_hours, out_path=out,
                              window=session.window)
    shaped = engine.qc_tally_and_decisions(raw)

    session.qc[str(death_hours)] = shaped
    STORE.save(session)

    report_uri = f"dam://session/{session_id}/qc-report"
    flags = [ChannelFlag(**{k: f[k] for k in
             ("monitor", "channel", "state", "evidence")},
             last_movement=f.get("last_movement")) for f in shaped["flags"]]
    return QCResult(
        session_id=session_id,
        death_hours=death_hours,
        tally=shaped["tally"],
        flags=flags,
        decisions_required=shaped["decisions_required"],
        report_uri=report_uri,
    ).model_dump()


@mcp.tool(annotations=_READ_ONLY)
def window_tradeoff(session_id: str, death_hours: float = DEFAULT_DEATH_HOURS) -> dict:
    """Show how n-alive trades off against where the analysis window ends.

    Longer window means more flies have died by the cutoff, so fewer are alive to
    analyse; a shorter window keeps more flies but sees less of each. This returns
    that curve — candidate window-end, hours from start, and the alive/died/empty/
    suspect counts at each — so the choice is informed rather than eyeballed. Use it
    before set_analysis_window. Read-only; changes nothing.
    """
    session = _require(session_id)
    if death_hours <= 0:
        raise ToolError(
            f"death_hours must be positive; got {death_hours}."
        )
    result = engine.window_tradeoff(session.paths, death_hours=death_hours)
    return TradeoffResult(
        session_id=session_id,
        common_start=result["common_start"],
        rows=[TradeoffRow(**r) for r in result["rows"]],
    ).model_dump()


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def set_analysis_window(
    session_id: str,
    start: str | None = None,
    end: str | None = None,
    death_hours: float = DEFAULT_DEATH_HOURS,
) -> dict:
    """Restrict the analysis to a time window and re-run QC within it.

    `start` and `end` are ISO datetimes (e.g. "2026-03-02T09:00:00"); either may be
    omitted to leave that edge at the data's own bound. Death is then judged INSIDE
    the window, so a fly that dies after `end` counts as valid data — which is why
    the window is chosen before exclusions, not after.

    Set the window BEFORE applying exclusions. If exclusions are already applied
    this refuses, because they were justified against a different window and must be
    reconsidered. Returns the window, the re-run QC tally, and decisions_required.
    """
    session = _require(session_id)
    if start is None and end is None:
        raise ToolError(
            "Give at least one of start / end (ISO datetimes). To analyse the whole "
            "run, you do not need a window at all."
        )
    if session.exclusions:
        raise ToolError(
            f"{len(session.exclusions)} exclusion(s) are already applied. They were "
            "justified against the current window; changing the window can change "
            "which channels are dead or empty, so those calls must be reconsidered. "
            "Window first, then exclusions — remove them (or start a fresh session) "
            "before setting a new window."
        )
    if death_hours <= 0:
        raise ToolError(f"death_hours must be positive; got {death_hours}.")

    window = {"start": start, "end": end}
    out = STORE.session_dir(session_id) / "qc_windowed.json"
    raw = engine.run_validate(session.paths, death_hours=death_hours, out_path=out,
                              window=window)
    shaped = engine.qc_tally_and_decisions(raw)

    session.window = window
    session.qc[str(death_hours)] = shaped
    STORE.save(session)
    dropped = shaped.get("window_dropped", [])
    drop_note = (
        "" if not dropped else
        f" WARNING: this window excluded {len(dropped)} monitor(s) entirely "
        f"({', '.join(dropped)}). The tally below covers only what survived — every "
        "later step runs on this reduced dataset. Widen the window if that was not "
        "intended."
    )
    return WindowResult(
        session_id=session_id,
        start=start,
        end=end,
        tally=shaped["tally"],
        monitors_dropped=dropped,
        decisions_required=shaped["decisions_required"],
        message="Window set and QC re-run within it. Now review decisions_required "
                "and apply exclusions." + drop_note,
    ).model_dump()


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    openWorldHint=False))
def assign_groups(session_id: str, mapping: dict) -> dict:
    """Assign channels to experimental groups. HUMANS ONLY — the model must never
    infer genotype or condition from the data; it only records what the human
    provides.

    `mapping` is {group_label: {monitor_key: channels}}.

    The monitor key is a filename as returned in load_experiment's `monitor_keys`
    (e.g. "Monitor1.txt"). A full path is also accepted and reduced to its
    filename, so paths handed in by a caller do not have to be converted first.

    `channels` accepts exactly three forms:
      * [low, high]        — a two-element INCLUSIVE range, e.g. [1, 16]
      * [c1, c2, c3, ...]  — an explicit list of channel numbers (length != 2)
      * "1-16" / "1,3,5-8" — a spec string
    Channel numbers are 1-32. Anything else is refused rather than guessed at.

    Refuses if the labels do not cover the groups the declared contrasts compare
    (a legal contrast id must never point at a group that does not exist). Warns —
    but does not refuse — if a group's channels all come from a single monitor,
    because that confounds treatment with monitor. Returns the resulting group
    sizes, any unassigned channels, and any such warnings. Overwrites any previous
    assignment for the session.
    """
    session = _require(session_id)
    if not isinstance(mapping, dict) or not mapping:
        raise ToolError(
            "assign_groups needs a non-empty mapping of "
            "{group_label: {monitor_filename: channels}}. Humans provide this; the "
            "model must not infer groups from the data."
        )
    known_files = {m["file"] for m in session.monitors}
    groups: list[dict] = []
    order_of: dict[str, int] = {}
    seen: dict[str, set[int]] = {}

    for label, per_file in mapping.items():
        if not isinstance(per_file, dict) or not per_file:
            raise ToolError(
                f"Group '{label}' must map to {{monitor_filename: channels}}; got "
                f"{per_file!r}."
            )
        if label not in order_of:
            order_of[label] = len(order_of) + 1
        for raw_monitor, spec in per_file.items():
            # Callers are routinely handed full paths (a task prompt names the
            # files by path) while the session keys on filenames. Normalise rather
            # than refuse: both readings of the contract are reasonable, so the
            # tool accepts either instead of making the caller guess.
            monitor = os.path.basename(str(raw_monitor))
            if monitor not in known_files:
                raise ToolError(
                    f"Monitor '{raw_monitor}' is not in this session. Loaded files "
                    f"are: {sorted(known_files)}. Use one of those keys (they are "
                    "also returned by load_experiment as monitor_keys)."
                )
            for ch in _expand_channels(spec):
                clash = seen.setdefault(monitor, set())
                if ch in clash:
                    raise ToolError(
                        f"{monitor} ch{ch} is assigned to more than one group. A "
                        "channel belongs to exactly one condition — fix the mapping."
                    )
                clash.add(ch)
                groups.append({
                    "monitor": monitor, "channel": ch,
                    "labels": label, "order": order_of[label],
                })

    sizes: dict[str, int] = {}
    for g in groups:
        sizes[g["labels"]] = sizes.get(g["labels"], 0) + 1

    _check_contrast_labels(set(sizes))          # #9 — refuses on mismatch

    session.groups = groups
    STORE.save(session)

    assigned = {(g["monitor"], g["channel"]) for g in groups}
    unassigned = [
        f"{m['file']} ch{ch}"
        for m in session.monitors
        for ch in range(1, (m["n_channels"] or 32) + 1)
        if (m["file"], ch) not in assigned
    ]
    warnings = [w for w in (_confound_warning(groups),) if w]   # #13 — warns only
    return GroupResult(
        session_id=session_id, group_sizes=sizes, unassigned=unassigned,
        warnings=warnings,
    ).model_dump()


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False,
    openWorldHint=False))
def apply_exclusions(
    session_id: str,
    exclusions: list[str],
    reason: str,
    confirm: bool = False,
) -> dict:
    """Exclude channels from the analysis. This is the human-in-the-loop gate:
    every exclusion changes n and the interpretation, so it is deliberate.

    `exclusions` is a list of "MonitorFile:channel" (e.g. "Monitor1.txt:5"). The
    monitor key is one of load_experiment's `monitor_keys`; a full path is accepted
    and reduced to its filename. `reason` is recorded with each and shown in the
    report.

    A key that matches no loaded monitor is refused — that is always a caller
    mistake (a typo, an unloaded monitor, a stale key from another session), never
    a silent no-op. A key that *does* resolve but matches no assigned channel is
    legitimate and returns n_excluded=0 instead of an error.

    Returns n_before, n_after and n_excluded alongside n per group, so "excluded
    two flies" and "excluded nobody" are distinguishable in the result itself.

    Call once with confirm=false to preview the effect (which channels, and the
    new n per group); the exclusion is NOT applied. Then, after the human agrees,
    call again with confirm=true to apply it. Nothing is excluded automatically.
    """
    session = _require(session_id)
    if not isinstance(exclusions, list) or not exclusions:
        raise ToolError(
            "apply_exclusions needs a non-empty list like [\"Monitor1.txt:5\"]. To "
            "change nothing, do not call it."
        )
    if not reason or not str(reason).strip():
        raise ToolError(
            "Every exclusion needs a reason — it is recorded and shown in the "
            "report, because an exclusion is a scientific decision, not a cleanup."
        )
    parsed = [_parse_exclusion(e) for e in exclusions]

    # An unresolvable monitor key is always a caller mistake, so it fails loudly
    # here rather than being recorded as an exclusion that matches nothing. This
    # is the same refusal assign_groups makes; the two tools disagreeing about an
    # identical bad key was the defect.
    known_files = {m["file"] for m in session.monitors}
    unknown = sorted({m for m, _ in parsed if m not in known_files})
    if unknown:
        names = ", ".join(f"'{u}'" for u in unknown)
        noun, verb = ("Monitor", "is") if len(unknown) == 1 else ("Monitors", "are")
        raise ToolError(
            f"{noun} {names} {verb} not in this session. Loaded files are: "
            f"{sorted(known_files)}. Use one of those keys (they are also returned "
            "by load_experiment as monitor_keys). Nothing was excluded."
        )

    def sizes_with(extra: set[tuple[str, int]]) -> dict[str, int]:
        excl = session.excluded_set() | extra
        out: dict[str, int] = {}
        for g in session.groups:
            if (g["monitor"], int(g["channel"])) in excl:
                continue
            out[g["labels"]] = out.get(g["labels"], 0) + 1
        return out

    # Counts, so a request that matches no assigned channel reports a zero instead
    # of an indistinguishable success. Legitimate: the key resolves, but nothing
    # met the criterion.
    before = sizes_with(set())
    after = sizes_with(set(parsed))
    n_before, n_after = sum(before.values()), sum(after.values())
    n_excluded = n_before - n_after
    zero_note = (
        "" if n_excluded else
        " NOTE: this matched no assigned channel — n is unchanged. The key is "
        "valid, but nothing met the criterion (already excluded, or not assigned "
        "to any group)."
    )

    if not confirm:
        return ExclusionResult(
            session_id=session_id, applied=False,
            n_by_group=after,
            excluded=[f"{m}:{c}" for m, c in parsed],
            n_before=n_before, n_after=n_after, n_excluded=n_excluded,
            reason=reason,
            message=(
                f"Preview only — nothing excluded yet. This would exclude "
                f"{n_excluded} channel(s), n {n_before} -> {n_after}. Confirm with "
                "the user, then call again with confirm=true to apply." + zero_note
            ),
        ).model_dump()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for monitor, channel in parsed:
        session.exclusions.append(
            {"monitor": monitor, "channel": channel, "reason": reason, "at": now}
        )
    STORE.save(session)
    return ExclusionResult(
        session_id=session_id, applied=True,
        n_by_group=sizes_with(set()),
        excluded=[f"{m}:{c}" for m, c in parsed],
        n_before=n_before, n_after=n_after, n_excluded=n_excluded,
        reason=reason,
        message=(f"Excluded {n_excluded} channel(s); n {n_before} -> {n_after}. "
                 "Recorded with the reason given." + zero_note),
    ).model_dump()


@mcp.tool(annotations=_READ_ONLY)
def list_contrasts(session_id: str) -> dict:
    """List the pre-declared contrasts the agent is allowed to run.

    The set lives in config/contrasts.yaml, declared before the data was seen. The
    model may run any contrast here and cannot invent one — this is pre-registration
    enforced by the tool boundary. Returns each contrast's id, metric, phase,
    groups, test, and rationale. Choose one and pass its id to run_contrast.
    """
    _require(session_id)
    return {"session_id": session_id, "contrasts": config.list_contrasts()}


@mcp.tool(annotations=_READ_ONLY)
def compute_sleep(
    session_id: str,
    immobility_minutes: float = 5.0,
    ld_period_h: float = 24.0,
) -> dict:
    """Sleep metrics by group × phase: total sleep time, bout duration, bout count,
    latency, WASO — each as mean ± SD with n. Summaries only; no per-fly series.

    Sleep is >= `immobility_minutes` of continuous inactivity (default 5 min, the
    Hendricks/Shaw convention). Requires run_qc first and assign_groups; results
    computed before QC or without groups are not interpretable. Read the full
    tables from the metrics resource.
    """
    session = _require(session_id)
    _guard_ready(session)
    if immobility_minutes <= 0:
        raise ToolError("immobility_minutes must be positive (default 5).")
    ds = engine.build_store(session, STORE)
    summary = engine.compute_sleep(ds, immobility_minutes, ld_period_h)
    return _store_metric(session, "sleep", summary)


@mcp.tool(annotations=_READ_ONLY)
def compute_activity(session_id: str, bout_threshold_minutes: float = 5.0) -> dict:
    """Locomotor activity by group × phase: total activity, counts per waking
    minute, bout duration, activity per bout — mean ± SD with n. Summaries only.

    Requires run_qc and assign_groups first. Use counts-per-waking-minute to
    separate a true activity change from an artefact of more waking time.
    """
    session = _require(session_id)
    _guard_ready(session)
    if bout_threshold_minutes <= 0:
        raise ToolError("bout_threshold_minutes must be positive (default 5).")
    ds = engine.build_store(session, STORE)
    summary = engine.compute_activity(ds, bout_threshold_minutes)
    return _store_metric(session, "activity", summary)


@mcp.tool(annotations=_READ_ONLY)
def compute_rhythmicity(session_id: str, method: str = "chi_sq") -> dict:
    """Circadian rhythm metrics per group: dominant period (mean ± SD) and, for
    the chi-square method, the fraction of animals with a significant periodogram
    peak. `method` is "chi_sq" (default) or "lomb_scargle".

    Requires run_qc and assign_groups first. Summaries only.
    """
    session = _require(session_id)
    _guard_ready(session)
    if method not in ("chi_sq", "lomb_scargle"):
        raise ToolError(
            f"Unknown rhythmicity method '{method}'. Use 'chi_sq' (default) or "
            "'lomb_scargle'."
        )
    ds = engine.build_store(session, STORE)
    summary = engine.compute_rhythmicity(ds, method)
    return _store_metric(session, "rhythmicity", summary)


@mcp.tool(annotations=_READ_ONLY)
def compute_survival(session_id: str, death_hours: float = DEFAULT_DEATH_HOURS) -> dict:
    """Survival by group: n, events, median survival, and pairwise log-rank tests,
    plus a decisions_required list for animals that recorded activity after their
    inferred death (a glitch, a dislodged fly, or too short a threshold).

    Uses the same death definition as QC. Requires run_qc and assign_groups first.
    Summaries only — no per-animal lifespans crossing the boundary.
    """
    session = _require(session_id)
    _guard_ready(session)
    if death_hours <= 0:
        raise ToolError(f"death_hours must be positive; got {death_hours}.")
    ds = engine.build_store(session, STORE)
    summary = engine.compute_survival(ds, death_hours)
    return _store_metric(session, "survival", summary,
                         decisions=summary.get("decisions_required", []))


@mcp.tool(annotations=_READ_ONLY)
def run_contrast(session_id: str, contrast_id: str) -> dict:
    """Run ONE pre-declared contrast and report whether it is significant.

    `contrast_id` must be one returned by list_contrasts. The metric, phase, groups,
    and test all come from config — this tool executes the declared comparison, it
    does not let the model define one. Returns effect size, n per arm, test
    statistic, p-value, group medians, and how many exclusions were in effect.

    Requires run_qc and assign_groups. Run each declared contrast and report which
    passed; do not fish for significance outside the declared set.
    """
    session = _require(session_id)
    _guard_ready(session)
    contrast = config.get_contrast(contrast_id)
    ds = engine.build_store(session, STORE)
    result = engine.run_contrast(ds, contrast, n_exclusions=len(session.exclusions))

    session.contrasts[contrast_id] = result
    STORE.save(session)
    return ContrastResult(session_id=session_id, **result).model_dump()


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True,
    openWorldHint=False))
def render_report(session_id: str, path: str, confirm: bool = False) -> dict:
    """Write the full QC + analysis report for the session to a Markdown file.

    This writes to disk, so call once with confirm=false to preview where it will
    go and what it contains, then again with confirm=true to write it. The report
    is assembled from stored summaries; it invents nothing.
    """
    session = _require(session_id)
    if not path or not str(path).strip():
        raise ToolError("render_report needs a destination path for the .md file.")
    text = report.render_report(session)
    if not confirm:
        return {
            "session_id": session_id,
            "written": False,
            "would_write_to": str(Path(path).resolve()),
            "preview": text[:1500],
            "message": "Preview only. Call again with confirm=true to write the file.",
        }
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    return {"session_id": session_id, "written": True, "path": str(dest.resolve())}


# ── resources ─────────────────────────────────────────────────────────────────
#
# Resources are readable artifacts, not actions. The Q&A layer reads these —
# never raw data — so "why is n=27?" is answered from the manifest, grounded and
# checkable, with no inference.

@mcp.resource("dam://session/{session_id}/manifest")
def manifest(session_id: str) -> str:
    """Files, time window, group assignments, exclusions and their reasons."""
    session = STORE.get(session_id)
    if session is None:
        return f"No session '{session_id}'."
    return report.render_manifest(session)


@mcp.resource("dam://session/{session_id}/qc-report")
def qc_report(session_id: str) -> str:
    import json
    session = STORE.get(session_id)
    if session is None:
        return f"No session '{session_id}'."
    return json.dumps(session.qc, indent=2)


@mcp.resource("dam://session/{session_id}/metrics")
def metrics(session_id: str) -> str:
    import json
    session = STORE.get(session_id)
    if session is None:
        return f"No session '{session_id}'."
    return json.dumps(session.metrics, indent=2)


@mcp.resource("dam://session/{session_id}/contrasts")
def contrasts(session_id: str) -> str:
    import json
    session = STORE.get(session_id)
    if session is None:
        return f"No session '{session_id}'."
    return json.dumps(session.contrasts, indent=2)


# ── internal ──────────────────────────────────────────────────────────────────

def _guard_ready(session) -> None:
    if not session.qc:
        raise needs_qc()
    if not session.groups:
        raise needs_groups()


def _store_metric(session, name: str, summary: dict, decisions: list | None = None) -> dict:
    session.metrics[name] = summary
    STORE.save(session)
    return MetricResult(
        session_id=session.session_id,
        metric=name,
        summary=summary,
        decisions_required=decisions or [],
        resource_uri=f"dam://session/{session.session_id}/metrics",
    ).model_dump()


def _check_contrast_labels(assigned: set[str]) -> None:
    """#9: refuse if the declared contrasts reference groups this assignment does
    not define. Best-effort — a config that cannot be read never blocks assignment,
    it just skips the check."""
    try:
        required = config.contrast_group_labels()
    except ToolError:
        return
    missing = required - assigned
    if missing:
        raise ToolError(
            f"The declared contrasts compare groups {sorted(required)}, but this "
            f"assignment defines {sorted(assigned)} — {sorted(missing)} would have "
            "no animals, so those contrasts could never run. Use the declared "
            "labels, or update config/contrasts.yaml first (a human, "
            "pre-registration step)."
        )


def _confound_warning(groups: list[dict]) -> str | None:
    """#13: flag when treatment is confounded with monitor — every group's channels
    come from a single monitor and no monitor is shared between groups. Structural,
    no biology; the human still decides whether it is intentional."""
    from collections import defaultdict
    group_monitors: dict[str, set[str]] = defaultdict(set)
    monitor_groups: dict[str, set[str]] = defaultdict(set)
    for g in groups:
        group_monitors[g["labels"]].add(g["monitor"])
        monitor_groups[g["monitor"]].add(g["labels"])
    if len(group_monitors) < 2:
        return None
    if not all(len(ms) == 1 for ms in group_monitors.values()):
        return None
    if not all(len(gs) == 1 for gs in monitor_groups.values()):
        return None
    pairs = "; ".join(f"'{lab}' ⟵ {sorted(ms)[0]}"
                      for lab, ms in group_monitors.items())
    return (
        "Treatment is confounded with monitor: " + pairs + ". A monitor effect (IR "
        "calibration, incubator position, thermal gradient, food batch) is "
        "indistinguishable from a treatment effect, and more n does not help. "
        "Proceed only if this is intentional; for future runs alternate conditions "
        "within each monitor."
    )


def _expand_channels(spec) -> list[int]:
    """A [low, high] pair -> inclusive range; a longer list -> those channels; a
    string -> "1-16" / "1,3,5-8". Validates 1..32; every parse guarded."""
    channels: set[int] = set()
    if isinstance(spec, str):
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                bounds = part.split("-", 1)
                lo, hi = _to_int(bounds[0], "channel"), _to_int(bounds[1], "channel")
                channels.update(range(min(lo, hi), max(lo, hi) + 1))
            else:
                channels.add(_to_int(part, "channel"))
    elif isinstance(spec, (list, tuple)):
        nums = [_to_int(x, "channel") for x in spec]
        if len(nums) == 2:
            lo, hi = nums
            channels.update(range(min(lo, hi), max(lo, hi) + 1))
        else:
            channels.update(nums)
    else:
        raise ToolError(
            f"Cannot read channel spec {spec!r}. Use [low, high], a list of "
            "channel numbers, or a string like '1-16'."
        )
    if any(not (1 <= c <= 32) for c in channels):
        raise ToolError(f"Channel numbers must be 1..32; got {sorted(channels)}.")
    if not channels:
        raise ToolError("Empty channel spec.")
    return sorted(channels)


def _parse_exclusion(e) -> tuple[str, int]:
    if isinstance(e, dict):
        if "monitor" not in e or "channel" not in e:
            raise ToolError(
                f"Exclusion {e!r} needs both 'monitor' and 'channel'. Or use the "
                "'MonitorFile:channel' string form."
            )
        return os.path.basename(str(e["monitor"])), _to_int(e["channel"], "channel")
    text = str(e)
    if ":" not in text:
        raise ToolError(
            f"Exclusion '{e}' is not in 'MonitorFile:channel' form (e.g. "
            "'Monitor1.txt:5')."
        )
    monitor, ch = text.rsplit(":", 1)
    if not monitor:
        raise ToolError(f"Exclusion '{e}' is missing the monitor filename.")
    # Same normalisation as assign_groups: exclusions are matched against group
    # rows keyed by filename, so an unnormalised path would match nothing and
    # silently exclude no one — worse than refusing.
    return os.path.basename(monitor), _to_int(ch, "channel")


def _to_int(value, what: str) -> int:
    """Parse an int with an errors-as-prompts message instead of a raw ValueError."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ToolError(
            f"Expected a whole-number {what}, got {value!r}. Check the spec format "
            "(e.g. a channel is '5' or a range '1-16')."
        ) from None


if __name__ == "__main__":
    mcp.run()
