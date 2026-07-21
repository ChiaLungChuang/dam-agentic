"""Render a session into a Markdown QC + analysis report.

The report is assembled entirely from the session's stored artifacts — QC tallies,
group sizes, metric summaries, contrast results — which are themselves aggregates
produced by tested functions. Nothing here reaches back into raw counts, so the
written report carries the same guarantee as everything the model saw.
"""

from __future__ import annotations

from .sessions import Session


def render_manifest(session: Session) -> str:
    """Files, window, groups, exclusions + reasons — the grounding for Q&A."""
    lines = [f"# Manifest — {session.name}", ""]
    lines.append(f"Session: `{session.session_id}`  ·  created {session.created_at}")
    lines.append("")
    lines.append("## Files")
    lines.append("| Monitor | Reads | Channels | Window | Bin (s) |")
    lines.append("|---|---|---|---|---|")
    for m in session.monitors:
        lines.append(
            f"| {m['file']} | {m['n_reads']} | {m['n_channels']} | "
            f"{m['first_ts']} → {m['last_ts']} | {m['bin_seconds']} |"
        )
    lines.append("")
    lines.append("## Groups")
    if session.groups:
        sizes = _group_sizes(session)
        for label in session.group_labels:
            lines.append(f"- **{label}**: n = {sizes.get(label, 0)}")
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


def _render_metric(payload: dict) -> str:
    lines = []
    for key, rows in payload.items():
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            continue
        lines.append(f"**{key}**")
        cols = [c for c in ("labels", "x", "n", "mean", "sd", "median") if c in rows[0]]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["---"] * len(cols)) + "|")
        for r in rows:
            lines.append("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |")
        lines.append("")
    return "\n".join(lines) if lines else "_(summary)_"


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
