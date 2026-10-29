"""Render a session into a Markdown QC + analysis report.

The report is assembled entirely from the session's stored artifacts — QC tallies,
group sizes, metric summaries, contrast results — which are themselves aggregates
produced by tested functions. Nothing here reaches back into raw counts, so the
written report carries the same guarantee as everything the model saw.
"""

from __future__ import annotations

from .sessions import Session


def _n_override_banner(session: Session) -> list[str]:
    """The declared-n override, above everything else in the document.

    Not a footnote and not a row in a table of warnings. This report's numbers rest
    on a refusal that a human suppressed: `assign_groups` found the assigned
    channel count contradicting the experiment's own pre-registered n, refused, and
    was overridden. A reader who scrolls to the contrast table and stops must
    already have passed this. A report that looks clean while resting on a
    suppressed refusal is the failure this system exists to prevent, so the banner
    is placed where skipping it takes deliberate effort.

    Empty list — no banner, no placeholder — in the normal case. A permanent
    "no overrides" line would train the reader to skip the region it lives in."""
    if not session.n_overrides:
        return []
    out = [
        "> ## ⚠ DECLARED-n OVERRIDE IN FORCE",
        ">",
        "> The n below is **not** the n this experiment pre-registered. "
        "`assign_groups` refused this assignment and a human overrode the refusal.",
        ">",
        "> | Group | Declared n | n used | Stated reason |",
        "> |---|---|---|---|",
    ]
    for o in session.n_overrides:
        out.append(
            f"> | {o['group']} | {o['declared_n']} | **{o['computed_n']}** | "
            f"{o.get('reason', '?')} |"
        )
    out += [
        ">",
        "> Every mean, SD, n and test in this report is computed over the **n "
        "used** column. Whether that is the right number is the decision recorded "
        "above; it is not something this report can check.",
        "",
    ]
    return out


def render_manifest(session: Session) -> str:
    """Files, window, groups, exclusions + reasons — the grounding for Q&A."""
    lines = [f"# Manifest — {session.name}", ""]
    lines.append(f"Session: `{session.session_id}`  ·  created {session.created_at}")
    lines.append("")
    lines.extend(_n_override_banner(session))
    lines.append("## Files")
    lines.append("| Monitor | Reads | Channels | Window | Bin (s) |")
    lines.append("|---|---|---|---|---|")
    for m in session.monitors:
        lines.append(
            f"| {m['file']} | {m['n_reads']} | {m['n_channels']} | "
            f"{m['first_ts']} → {m['last_ts']} | {m['bin_seconds']} |"
        )
    lines.append("")
    lines.extend(_machines_section(session))
    lines.append("## Groups")
    if session.groups:
        sizes = _group_sizes(session)
        overridden = {o["group"] for o in session.n_overrides}
        for label in session.group_labels:
            flag = "  ⚠ **n OVERRIDDEN — see above**" if label in overridden else ""
            lines.append(f"- **{label}**: n = {sizes.get(label, 0)}{flag}")
    else:
        lines.append("_No groups assigned._")
    lines.append("")
    lines.append("## Exclusions")
    if session.exclusions:
        for e in session.exclusions:
            lines.append(
                f"- {e['monitor']} ch{e['channel']} — {e['reason']} "
                f"(applied {e.get('at', '?')})"
            )
    else:
        lines.append("_None applied._")
    return "\n".join(lines)


def _machines_section(session: Session) -> list[str]:
    """Which files are the beams of which machine, by role. Nothing on a session
    with no declared topology, so the undeclared manifest is unchanged."""
    if not session.beams:
        return []
    from .config import ROLES
    rows: dict[str, dict[str, str]] = {}
    for b in session.beams:
        rows.setdefault(b["machine"], {})[b["role"]] = b["file"]
    lines = ["## Machines",
             "| Machine | " + " | ".join(ROLES) + " |",
             "|---|" + "---|" * len(ROLES)]
    for machine, roles in rows.items():
        lines.append(f"| {machine} | " + " | ".join(roles.get(r, "—") for r in ROLES)
                     + " |")
    lines += ["",
              "Each machine's beams are summed per tube before QC and analysis, so "
              "every QC call, group size and n below counts tubes — animals — not "
              "beams.", ""]
    return lines


def render_report(session: Session) -> str:
    parts = [render_manifest(session), ""]

    parts.append("## QC")
    if session.qc:
        for death_hours, qc in session.qc.items():
            parts.append(f"### death_hours = {death_hours}")
            for monitor, tally in qc.get("tally", {}).items():
                tally_str = ", ".join(f"{k}: {v}" for k, v in tally.items())
                parts.append(f"- **{monitor}** — {tally_str}")
            decisions = qc.get("decisions_required", [])
            parts.append("")
            parts.append(f"**Decisions required ({len(decisions)}):**")
            if decisions:
                parts.extend(f"{i + 1}. {d}" for i, d in enumerate(decisions))
            else:
                parts.append("_none_")
    else:
        parts.append("_QC not run._")
    parts.append("")

    parts.append("## Metrics")
    if session.metrics:
        for metric, payload in session.metrics.items():
            parts.append(f"### {metric}")
            parts.append(_render_metric(payload))
    else:
        parts.append("_No metrics computed._")
    parts.append("")

    parts.append("## Contrasts")
    if session.contrasts:
        parts.append("| Contrast | Metric | Phase | Groups | Test | p | Effect | n |")
        parts.append("|---|---|---|---|---|---|---|---|")
        for cid, c in session.contrasts.items():
            n_str = ", ".join(f"{k}={v}" for k, v in c["n"].items())
            eff = c["effect_size"]
            parts.append(
                f"| {cid} | {c['metric']} | {c['phase']} | "
                f"{' vs '.join(c['groups'])} | {c['test']} | "
                f"{_fmt(c['p_value'])} | {eff['kind']}={_fmt(eff['value'])} | {n_str} |"
            )
    else:
        parts.append("_No contrasts run._")

    parts.append("")
    parts.append("---")
    parts.append(
        "Every value above traces to a tested analysis function. The model "
        "orchestrated the pipeline; it did not compute any statistic itself."
    )
    return "\n".join(parts)


# Columns that lead a metric table, in this order, when present: identity first,
# then the statistics a reader looks for. An ORDER, not a filter. This used to be
# the filter — six names, everything else dropped — and the report silently lost
# survival's values (`y`), the whole log-rank table, the rhythmic fraction and
# every IQR, while the metrics resource carried all of them.
_LEADING_COLUMNS = ("labels", "x", "group1", "group2", "n",
                    "mean", "sd", "sem", "median", "q1", "q3")


def _render_metric(payload: dict) -> str:
    """Every key in the payload, so the document shows what the metric carries.

    Scalars are the computation's parameters (immobility_minutes, method,
    death_hours) and come first: a number is not interpretable without the
    threshold that defined it. Lists of rows are tables; lists of strings are
    decisions; an empty list says so rather than vanishing, because a missing
    decisions list and an empty one must not read the same."""
    params, blocks = [], []
    for key, value in payload.items():
        if not isinstance(value, list):
            params.append(f"- {key}: {_fmt(value)}")
            continue
        blocks.append(f"**{key}**")
        if not value:
            blocks.append("_none_")
        elif all(isinstance(r, dict) for r in value):
            blocks.extend(_render_table(value))
        else:
            blocks.extend(f"- {item}" for item in value)
        blocks.append("")
    lines = params + ([""] if params and blocks else []) + blocks
    return "\n".join(lines) if lines else "_(empty)_"


def _render_table(rows: list[dict]) -> list[str]:
    """Every column with a value in at least one row. A column null in every row
    (the engine's id / order / channel / monitor) hides nothing by being left out,
    and rendering it would bury the column that does carry the value."""
    seen: list[str] = []
    for r in rows:
        seen.extend(k for k in r if k not in seen)
    present = [k for k in seen if any(r.get(k) is not None for r in rows)]
    if not present:
        return ["_no values_"]
    cols = ([c for c in _LEADING_COLUMNS if c in present]
            + [c for c in present if c not in _LEADING_COLUMNS])
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    out.extend("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |" for r in rows)
    return out


def _group_sizes(session: Session) -> dict[str, int]:
    excluded = session.excluded_set()
    sizes: dict[str, int] = {}
    for g in session.groups:
        if (g["monitor"], int(g["channel"])) in excluded:
            continue
        sizes[g["labels"]] = sizes.get(g["labels"], 0) + 1
    return sizes


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3g}"
    return str(v)
