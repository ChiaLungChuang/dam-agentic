"""The compute layer — the only place raw counts are ever touched.

Every public function here takes a Session (or a built DataStore) and returns
*aggregate summaries*: per-group n, mean, SD, median, effect sizes, tallies. It
never returns an activity series, a per-timepoint array, or a dataframe of counts.
That is the architectural guarantee (spec rule 2): the model cannot compute or
invent a statistic because the numbers are produced here, in tested Rtivity-Python
functions, and only their summaries cross the boundary.

The analysis maths is not implemented here. It is imported from Rtivity-Python
(Silva et al., Sci Rep 2022; this repo's contribution is the Python rewrite + the
agentic layer, not the methods). This module is the wiring: build the analysis
table the way DataStore expects, call the tested function, summarise, hand back.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .errors import ToolError, needs_groups
from .sessions import Session, SessionStore

# validate_dam.py — the tested QC detector. run_qc / describe wrap it as a
# subprocess (same contract score.py uses) so there is a single source of truth
# for classification, not a reimplementation that can drift.
_QC_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills" / "dam-qc" / "scripts" / "validate_dam.py"
)

_LIGHT_COL = 10          # 1-indexed; DAM2 monitor files are 42 columns
_N_EXPECTED_COLS = 42


# ── Rtivity-Python import check ───────────────────────────────────────────────
#
# The analysis engine is the installed `rtivity-python` package (editable, or from
# git — see pyproject and docs/running.md). Import it normally. Packaging is the
# contract now; there is no sys.path manipulation and no path env var standing in
# for it. If the package is missing, say exactly how to install it.

def _ensure_rtivity() -> None:
    try:
        import modules.data.data_store  # noqa: F401
    except ModuleNotFoundError as exc:
        raise ToolError(
            "The Rtivity-Python analysis engine is not installed. Install it with "
            "`pip install -e /path/to/Rtivity-Python` (or "
            "`pip install 'rtivity-python @ "
            "git+https://github.com/ChiaLungChuang/Rtivity-Python'`), then retry. "
            "Structural tools (load_experiment, describe_experiment, run_qc) work "
            "without it; only the compute_* and run_contrast tools need it."
        ) from exc


# ── structural scan (load_experiment) ─────────────────────────────────────────

def scan_structural(paths: list[str]) -> tuple[list[dict], list[str]]:
    """Light per-file scan for load_experiment: shape only, no counts surfaced.

    Returns (monitor summaries, warnings). Raises ToolError with an actionable
    message if a file cannot be parsed as a DAM monitor file at all.
    """
    monitors: list[dict] = []
    warnings: list[str] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            raise ToolError(
                f"Monitor file not found: {path}. Check the path — load_experiment "
                "needs the .txt files themselves, not the folder."
            )
        summary, file_warnings = _scan_file(path)
        monitors.append(summary)
        warnings.extend(file_warnings)

    # Cross-monitor discrepancy is the single most informative early signal
    # (SKILL Step 1). Surface a divergent bin width or read count.
    bins = {m["bin_seconds"] for m in monitors if m["bin_seconds"]}
    if len(bins) > 1:
        warnings.append(
            f"Monitors disagree on bin width ({sorted(bins)} s). A file parsed at "
            "the wrong bin width yields sleep metrics off by that ratio. Confirm the "
            "run interval before computing anything."
        )
    return monitors, warnings


def _scan_file(path: Path) -> tuple[dict, list[str]]:
    first_ts = last_ts = prev_ts = None
    n_reads = 0
    n_cols_seen: int | None = None
    gaps: dict[int, int] = {}
    with open(path) as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < _N_EXPECTED_COLS:
                continue
            try:
                ts = datetime.strptime(f"{parts[1]} {parts[2]}", "%d %b %y %H:%M:%S")
            except (ValueError, IndexError):
                continue
            if n_cols_seen is None:
                n_cols_seen = len(parts)
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            n_reads += 1
            if prev_ts is not None:
                gaps[int((ts - prev_ts).total_seconds())] = (
                    gaps.get(int((ts - prev_ts).total_seconds()), 0) + 1
                )
            prev_ts = ts

    if n_reads == 0:
        raise ToolError(
            f"{path.name}: no rows parse as DAM2 data (expected {_N_EXPECTED_COLS} "
            "tab-separated columns with 'DD MMM YY' + 'HH:MM:SS' in columns 2-3). "
            "This may be a different DAMSystem version or a non-monitor file — ask "
            "the user to confirm the format."
        )

    bin_seconds = max(gaps, key=gaps.get) if gaps else None
    n_channels = (n_cols_seen - (_LIGHT_COL)) if n_cols_seen else None  # cols 11..42

    warnings: list[str] = []
    if bin_seconds is not None and bin_seconds != 60:
        warnings.append(
            f"{path.name}: bin width is {bin_seconds}s, not the usual 60s. Sleep "
            "metrics scale with bin width — confirm this is intended."
        )
    if n_cols_seen not in (None, _N_EXPECTED_COLS):
        warnings.append(
            f"{path.name}: found {n_cols_seen} columns, expected {_N_EXPECTED_COLS}. "
            "Channel mapping may be off; confirm the DAMSystem version."
        )

    return (
        {
            "file": path.name,
            "path": str(path.resolve()),
            "n_reads": n_reads,
            "n_channels": n_channels,
            "first_ts": first_ts.isoformat(),
            "last_ts": last_ts.isoformat(),
            "bin_seconds": bin_seconds,
        },
        warnings,
    )


# ── QC (describe_experiment / run_qc) ─────────────────────────────────────────

def run_validate(paths: list[str], death_hours: float, out_path: Path,
                 window: dict | None = None) -> dict:
    """Run the tested validate_dam.py detector as a subprocess and return its JSON.

    `window` is an optional {"start": iso, "end": iso}; when set, QC is computed
    within that window (death is judged inside it), so the same session windowed
    differently yields a different, correct QC.
    """
    cmd = [sys.executable, str(_QC_SCRIPT), *paths,
           "--out", str(out_path), "--death-hours", str(death_hours)]
    if window and window.get("start"):
        cmd += ["--start", window["start"]]
    if window and window.get("end"):
        cmd += ["--end", window["end"]]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ToolError(
            "QC detector could not process these files: "
            + (proc.stderr.strip() or "unknown error")
            + ". Fix the file(s) or drop the unparseable one from load_experiment."
        )
    return json.loads(out_path.read_text())


def window_tradeoff(paths: list[str], death_hours: float = 24.0,
                    n_points: int = 6) -> dict:
    """n-alive as a function of where the analysis window ends.

    Longer window -> more flies have died by the cutoff -> fewer alive. The point is
    to turn an eyeball decision into an informed one: compute the curve, let the
    human pick. Reuses the tested classifier in-process (one parse, many cutoffs)
    rather than shelling out per candidate.
    """
    vd = _validate_module()
    monitors = [m for m in (vd.parse_monitor(p) for p in paths) if m]
    if not monitors:
        raise ToolError(
            "No parseable monitor files to compute a window tradeoff for."
        )
    common_start = max(m["rows"][0]["ts"] for m in monitors)
    common_end = min(m["rows"][-1]["ts"] for m in monitors)
    span_h = (common_end - common_start).total_seconds() / 3600.0
    if span_h <= 0:
        raise ToolError("Monitors share no common time window.")

    # Candidate ends from ~1/n_points of the run out to the full run.
    rows_out = []
    for k in range(1, n_points + 1):
        cut = common_start + (common_end - common_start) * (k / n_points)
        tally = {"alive": 0, "died": 0, "empty": 0, "suspect": 0}
        for m in monitors:
            windowed = {"path": m["path"],
                        "rows": [r for r in m["rows"] if r["ts"] <= cut]}
            if len(windowed["rows"]) < 2:
                continue
            bin_s = vd.infer_bin_seconds(windowed["rows"]) or 60
            death_bins = int(death_hours * 3600 / bin_s)
            for c in vd.classify_channels(windowed, death_bins):
                tally[c["state"]] = tally.get(c["state"], 0) + 1
        rows_out.append({
            "end": cut.isoformat(),
            "hours_from_start": round((cut - common_start).total_seconds() / 3600, 1),
            "n_alive": tally["alive"],
            "n_died": tally["died"],
            "n_empty": tally["empty"],
            "n_suspect": tally["suspect"],
        })
    return {"common_start": common_start.isoformat(), "rows": rows_out}


_VD_MODULE = None


def _validate_module():
    """Import validate_dam.py as a module (once) to reuse its tested classifier."""
    global _VD_MODULE
    if _VD_MODULE is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("validate_dam", _QC_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _VD_MODULE = module
    return _VD_MODULE


def qc_tally_and_decisions(qc: dict) -> dict:
    """Reshape validate_dam.py output into the QCResult summary shape."""
    tally: dict[str, dict[str, int]] = {}
    flags: list[dict] = []
    for mon in qc["monitors"]:
        tally[mon["file"]] = mon["tally"]
        for ch in mon["channels"]:            # only non-alive channels are listed
            flag = {
                "monitor": mon["file"],
                "channel": ch["channel"],
                "state": ch["state"],
                "evidence": _evidence(ch),
            }
            if "last_movement" in ch:
                flag["last_movement"] = ch["last_movement"]
            flags.append(flag)
    return {
        "tally": tally,
        "flags": flags,
        "decisions_required": qc.get("decisions_required", []),
        "inventory": qc.get("inventory", []),
        "alignment": qc.get("alignment", {}),
        "unparseable_files": qc.get("unparseable_files", []),
    }


def _evidence(ch: dict) -> str:
    state = ch["state"]
    if state == "empty":
        return "Zero counts for the entire run — an empty tube, never an n."
    if state == "died":
        return (
            f"Activity then {ch.get('trailing_zero_bins', '?')} trailing zero bins "
            f"to end of run; last movement {ch.get('last_movement', '?')}. Censor at "
            "death, do not score as sleep."
        )
    if state == "suspect":
        return ch.get("reason", "Activity implausibly low against this run's own "
                                "distribution — flag for human review.")
    return ""


# ── analysis-table construction ───────────────────────────────────────────────

def build_conditions(session: Session) -> pd.DataFrame:
    """Turn the session's human-assigned groups (minus exclusions) into the
    conditions table DataStore consumes. One row per included channel."""
    if not session.groups:
        raise needs_groups()
    # Each file's own range, clamped to the analysis window if one is set, so the
    # metrics are computed over exactly the window QC was run on.
    win = session.window or {}
    per_file = {}
    for m in session.monitors:
        start = max(m["first_ts"], win["start"]) if win.get("start") else m["first_ts"]
        stop = min(m["last_ts"], win["end"]) if win.get("end") else m["last_ts"]
        per_file[m["file"]] = (start, stop)
    excluded = session.excluded_set()
    rows = []
    for g in session.groups:
        key = (g["monitor"], int(g["channel"]))
        if key in excluded:
            continue
        start, stop = per_file.get(g["monitor"], (None, None))
        rows.append({
            "file": g["monitor"],
            "start_datetime": start,
            "stop_datetime": stop,
            "region_id": int(g["channel"]),
            "labels": g["labels"],
            "order": int(g.get("order", 1)),
        })
    if not rows:
        raise ToolError(
            "Every assigned channel is excluded — there is nothing left to analyse. "
            "Review the exclusions before computing metrics."
        )
    return pd.DataFrame(rows)


def build_store(session: Session, store: SessionStore):
    """Build a Rtivity DataStore for the session's included channels.

    Monitor files are symlinked into a per-session working directory (keeping the
    tested DataStore path intact and side effects out of the user's data folder),
    then loaded via the conditions table.
    """
    _ensure_rtivity()
    from modules.data.data_store import DataStore, Settings

    work = store.session_dir(session.session_id) / "work"
    work.mkdir(parents=True, exist_ok=True)
    for p in session.paths:
        src = Path(p)
        dest = work / src.name
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        dest.symlink_to(src)

    conditions = build_conditions(session)
    settings = Settings()
    ds = DataStore(dam_dir=work, settings=settings)
    ds.load_from_dataframe(conditions, dam_dir=work)
    return ds


# ── metric computation ────────────────────────────────────────────────────────

def compute_sleep(ds, immobility_minutes: float = 5.0, ld_period_h: float = 24.0) -> dict:
    from modules.analysis import activity as A
    from modules.analysis import sleep as S

    df = S.annotate_sleep(ds.data, threshold_min=immobility_minutes)
    df = S.detect_sleep_bouts(df, ld_period_h=ld_period_h)
    return {
        "immobility_minutes": immobility_minutes,
        "total_sleep_time_h": _table(A.summary_stats(S.tst_mean(df))),
        "sleep_bout_duration_min_by_phase":
            _table(A.summary_stats(S.sleep_bout_duration_by_phase(df))),
        "sleep_bout_count_by_phase":
            _table(A.summary_stats(S.sleep_bout_count_by_phase(df))),
        "sleep_latency_min": _table(A.summary_stats(S.sleep_latency_mean(df))),
        "waso_h": _table(A.summary_stats(S.waso_mean(df))),
    }


def compute_activity(ds, bout_threshold_minutes: float = 5.0) -> dict:
    from modules.analysis import activity as A

    adf = A.detect_activity_bouts(ds.data, threshold_min=bout_threshold_minutes)
    return {
        "total_activity_by_phase": _table(A.summary_stats(A.activity_by_phase(ds.data))),
        "counts_per_waking_minute_by_phase":
            _table(A.summary_stats(_cpwm_by_phase(ds.data))),
        "bout_duration_s_by_phase": _table(A.summary_stats(A.bout_duration_by_phase(adf))),
        "activity_per_bout_by_phase":
            _table(A.summary_stats(A.bout_activity_by_phase(adf))),
    }


def compute_rhythmicity(ds, method: str = "chi_sq") -> dict:
    from modules.analysis import activity as A
    from modules.analysis import rhythm as R

    dp = R.dominant_period_by_animal(ds.data, method=method)
    result = {
        "method": method,
        "dominant_period_h": _table(A.summary_stats(dp)),
    }
    if method != "lomb_scargle":
        pg = R.periodogram_by_animal(ds.data, method="chi_sq")
        result["rhythmic_fraction"] = _rhythmic_fraction(pg)
    return result


def compute_survival(ds, death_hours: float = 24.0) -> dict:
    from modules.analysis import survival as surv

    sd = surv.build_survival_data(ds.data, dead_window_h=death_hours)
    summary = _table(surv.survival_summary(sd))
    logrank = _table(surv.logrank_test(sd))
    ambiguous = surv.flag_ambiguous_deaths(ds.data, dead_window_h=death_hours)

    decisions = []
    for _, row in ambiguous.iterrows():
        decisions.append(
            f"{row['id']}: recorded {int(row['post_death_counts'])} counts after "
            f"inferred death on day {int(row['death_day'])} (last active day "
            f"{int(row['last_active_day'])}). Monitor glitch, dislodged fly, or "
            "threshold too short? Confirm before trusting the survival curve."
        )
    return {
        "death_hours": death_hours,
        "summary": summary,
        "logrank_pairwise": logrank,
        "decisions_required": decisions,
    }


# ── contrasts ─────────────────────────────────────────────────────────────────

def run_contrast(ds, contrast: dict, n_exclusions: int) -> dict:
    """Run one pre-declared contrast. The comparison (metric, phase, groups, test)
    comes entirely from the contrast dict, which originates in config — never from
    the model. This function only executes it."""
    from scipy import stats

    metric = contrast["metric"]
    phase = contrast["phase"]
    groups = contrast["groups"]
    test = contrast.get("test", "wilcoxon")
    if len(groups) != 2:
        raise ToolError(
            f"Contrast '{contrast.get('id')}' names {len(groups)} groups; a contrast "
            "compares exactly two. Fix config/contrasts.yaml."
        )

    pam = _per_animal_metric(ds, metric, phase)
    a = pam[pam["labels"] == groups[0]]["y"].dropna().to_numpy()
    b = pam[pam["labels"] == groups[1]]["y"].dropna().to_numpy()
    for label, arr in ((groups[0], a), (groups[1], b)):
        if len(arr) == 0:
            raise ToolError(
                f"Group '{label}' has no animals with a {metric} value in the "
                f"{phase} phase. Check that assign_groups used this exact label and "
                "that the group survived exclusions."
            )

    if test == "wilcoxon":
        res = stats.mannwhitneyu(a, b, alternative="two-sided")
        statistic = float(res.statistic)
        p_value = float(res.pvalue)
        effect = 1.0 - (2.0 * statistic) / (len(a) * len(b))  # rank-biserial
        stat_name = "mann_whitney_u"
        effect_name = "rank_biserial"
    elif test == "t":
        res = stats.ttest_ind(a, b, equal_var=False)
        statistic = float(res.statistic)
        p_value = float(res.pvalue)
        pooled = math.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2) or math.nan
        effect = float((a.mean() - b.mean()) / pooled)  # Cohen's d
        stat_name = "welch_t"
        effect_name = "cohens_d"
    else:
        raise ToolError(
            f"Contrast '{contrast.get('id')}' asks for test '{test}', which is not "
            "supported. Use 'wilcoxon' (rank-sum) or 't' (Welch) in contrasts.yaml."
        )

    return _json_safe({
        "contrast_id": contrast.get("id"),
        "metric": metric,
        "phase": phase,
        "groups": groups,
        "test": stat_name,
        "n": {groups[0]: int(len(a)), groups[1]: int(len(b))},
        "median": {groups[0]: float(np.median(a)), groups[1]: float(np.median(b))},
        "statistic": statistic,
        "p_value": p_value,
        "effect_size": {"kind": effect_name, "value": effect},
        "exclusions_applied": n_exclusions,
        "rationale": contrast.get("rationale"),
    })


def _per_animal_metric(ds, metric: str, phase: str) -> pd.DataFrame:
    """Per-animal value of a declared metric, filtered to one phase. Returns
    columns id, labels, y in interpretable units: total_sleep in hours per day,
    mean_bout_duration in minutes, counts_per_waking_minute in counts/min. (The
    Wilcoxon test only sees ranks, but the reported median should be in real
    units, not a fraction hiding behind a metric named "total_sleep".)"""
    from modules.analysis import sleep as S

    phase_cap = "Light" if str(phase).lower() in ("light", "l", "day") else "Dark"
    df = ds.data

    if metric == "total_sleep":
        s = S.annotate_sleep(df, threshold_min=5.0)
        sub = s[s["phase"] == phase_cap]
        bin_min = _bin_minutes(df)
        n_days = max(int(df["experiment_day"].nunique()), 1)
        g = (sub.groupby(["id", "labels"], observed=True)["asleep"]
             .sum().reset_index().rename(columns={"asleep": "y"}))
        # asleep bins -> mean hours of sleep per day in this phase
        g["y"] = g["y"] * bin_min / 60.0 / n_days
        return g
    if metric == "mean_bout_duration":
        s = S.annotate_sleep(df, threshold_min=5.0)
        s = S.detect_sleep_bouts(s)
        t = S.sleep_bout_duration_by_phase(s)
        return t[t["x"] == phase_cap][["id", "labels", "y"]]
    if metric == "counts_per_waking_minute":
        cp = _cpwm_by_phase(df)
        return cp[cp["x"] == phase_cap][["id", "labels", "y"]]

    raise ToolError(
        f"Contrast metric '{metric}' is not one this server can compute. Supported: "
        "total_sleep, mean_bout_duration, counts_per_waking_minute. Add a mapping "
        "in the engine before declaring it in contrasts.yaml."
    )


# ── small analysis helpers ────────────────────────────────────────────────────

def _bin_minutes(df: pd.DataFrame) -> float:
    """Bin width in minutes, from the data's own cadence — never assumed."""
    td = df["time_diff"].dropna()
    td = td[td > 0]
    return float(td.median()) / 60.0 if len(td) else 1.0


def _cpwm_by_phase(df: pd.DataFrame) -> pd.DataFrame:
    """Counts per waking minute per animal per phase (total activity / active bins)."""
    grp = df.groupby(["id", "labels", "order", "channel", "monitor", "phase"],
                     observed=True)
    agg = grp.agg(total=("activity", "sum"), waking=("moving", "sum")).reset_index()
    agg["y"] = agg["total"] / agg["waking"].replace(0, np.nan)
    agg = agg.rename(columns={"phase": "x"})
    agg["y"] = agg["y"].fillna(0)
    cols = ["id", "labels", "order", "channel", "monitor", "x", "y"]
    return agg[cols].reset_index(drop=True)


def _rhythmic_fraction(pg: pd.DataFrame) -> list[dict]:
    """Fraction of animals per group with a significant chi-square periodogram peak."""
    rows = []
    for (label,), grp in pg.groupby(["labels"], observed=True):
        per_animal = grp.groupby("id")["p_value"].min()
        rhythmic = (per_animal < 0.05).sum()
        rows.append({
            "labels": label,
            "n": int(per_animal.shape[0]),
            "n_rhythmic": int(rhythmic),
            "rhythmic_fraction": float(rhythmic / per_animal.shape[0])
            if per_animal.shape[0] else None,
        })
    return _json_safe(rows)


# ── serialisation guards ──────────────────────────────────────────────────────

def _table(df: pd.DataFrame) -> list[dict]:
    """Summary DataFrame -> list of row dicts, JSON-safe. Aggregate rows only."""
    return _json_safe(df.to_dict(orient="records"))


def _json_safe(obj):
    """Recursively convert numpy scalars to native types and non-finite floats to
    None, so tool returns and resources always serialise."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        obj = float(obj)
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj
