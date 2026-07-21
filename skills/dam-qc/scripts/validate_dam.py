#!/usr/bin/env python3
"""Deterministic QC checks for TriKinetics DAM monitor files.

Implements Steps 1-5 of the dam-qc skill and emits a JSON report.
Flags; does not repair. The only transformation applied is the mechanical
alignment described in Step 2, and it is reported rather than assumed.

Usage:
    python validate_dam.py Monitor1.txt Monitor2.txt --out qc.json
    python validate_dam.py data/*.txt --death-hours 24

Thresholds are CLI flags on purpose: they are lab conventions, not constants.
"""

import argparse
import glob
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

LIGHT_COL = 10         # 1-indexed; DAM2 monitor files are 42 columns
FIRST_CHANNEL_COL = 11
N_CHANNELS = 32

# Gradual-decline surfacing (see the changepoint-frailty research note). A fly that
# tapers toward death without ever producing a clean zero run is invisible to the
# trailing-zero test — it stays `alive` and silently corrupts every downstream
# metric. We do NOT decide when such a fly died (there is no fact of the matter);
# we only surface it as `suspect` for a human, using a trailing-window activity
# *rate* check instead of a strict-zero test.
DECLINE_BASELINE_H = 24.0   # each channel's own first day is its reference
DECLINE_TRAIL_H = 24.0      # compare the final day against that baseline
DECLINE_MIN_RUN_H = 48.0    # baseline and trailing windows must not overlap
DECLINE_RATIO = 0.35        # final-day activity below 35% of own baseline = decline
DECLINE_MIN_EXPECTED = 100  # baseline too quiet to judge -> leave to low-activity check


def parse_monitor(path):
    """Parse one monitor file into a dict of columns. Returns None on unparseable."""
    rows, bad_lines = [], 0
    with open(path) as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < FIRST_CHANNEL_COL + N_CHANNELS - 1:  # expect 42 cols
                bad_lines += 1
                continue
            try:
                ts = datetime.strptime(f"{parts[1]} {parts[2]}", "%d %b %y %H:%M:%S")
                counts = [int(x) for x in
                          parts[FIRST_CHANNEL_COL - 1:FIRST_CHANNEL_COL - 1 + N_CHANNELS]]
                rows.append({
                    "index": int(parts[0]),
                    "ts": ts,
                    "status": int(parts[3]),
                    "light": int(parts[LIGHT_COL - 1]),
                    "counts": counts,
                })
            except (ValueError, IndexError):
                bad_lines += 1
    if not rows:
        return None
    return {"path": str(path), "rows": rows, "bad_lines": bad_lines}


def infer_bin_seconds(rows):
    """Modal gap between consecutive reads. Never assume 60."""
    gaps = {}
    for a, b in zip(rows, rows[1:]):
        g = int((b["ts"] - a["ts"]).total_seconds())
        gaps[g] = gaps.get(g, 0) + 1
    return max(gaps, key=gaps.get) if gaps else None


def inventory(mon):
    rows = mon["rows"]
    bin_s = infer_bin_seconds(rows)
    span = (rows[-1]["ts"] - rows[0]["ts"]).total_seconds()
    expected = int(span / bin_s) + 1 if bin_s else None
    return {
        "file": Path(mon["path"]).name,
        "n_reads": len(rows),
        "expected_reads": expected,
        "missing_reads": (expected - len(rows)) if expected else None,
        "bad_lines": mon["bad_lines"],
        "first_ts": rows[0]["ts"].isoformat(),
        "last_ts": rows[-1]["ts"].isoformat(),
        "bin_seconds": bin_s,
        "bad_status_reads": sum(1 for r in rows if r["status"] != 1),
        "duplicate_timestamps": len(rows) - len({r["ts"] for r in rows}),
    }


def classify_channels(mon, death_bins):
    """empty / died / alive / suspect for each channel. See Step 3 of the skill."""
    rows = mon["rows"]
    out = []
    for ch in range(N_CHANNELS):
        series = [r["counts"][ch] for r in rows]
        total = sum(series)
        if total == 0:
            out.append({"channel": ch + 1, "state": "empty", "total": 0})
            continue
        last_active = max(i for i, v in enumerate(series) if v > 0)
        trailing_zeros = len(series) - 1 - last_active
        if trailing_zeros >= death_bins:
            out.append({
                "channel": ch + 1, "state": "died", "total": total,
                "last_movement": rows[last_active]["ts"].isoformat(),
                "trailing_zero_bins": trailing_zeros,
            })
        else:
            out.append({"channel": ch + 1, "state": "alive", "total": total})
    # Mark low-activity outliers as suspect, judged against this run's own
    # distribution rather than a fixed count that would not transfer between
    # genotypes or ages.
    alive = [c for c in out if c["state"] == "alive"]
    if len(alive) >= 4:
        totals = sorted(c["total"] for c in alive)
        q1 = totals[len(totals) // 4]
        for c in alive:
            if c["total"] < q1 * 0.1:
                c["state"] = "suspect"
                c["reason"] = "activity < 10% of first-quartile channel"
    # Gradual decline: a channel still called `alive` whose final day has collapsed
    # against its own phase-matched baseline. Surface it; do not decide it died.
    for c in out:
        if c["state"] != "alive":
            continue
        ratio = decline_ratio(rows, c["channel"] - 1)
        if ratio is not None and ratio < DECLINE_RATIO:
            c["state"] = "suspect"
            c["decline_ratio"] = round(ratio, 3)
            c["reason"] = (
                f"final-day activity fell to {ratio:.0%} of this channel's own "
                "phase-matched baseline without a clean zero run — possible gradual "
                "decline/frailty, not a clean death"
            )
    return out


def decline_ratio(rows, ch):
    """Phase-normalised trailing activity ratio for one channel, or None.

    Compares the final DECLINE_TRAIL_H hours against the channel's OWN first-day
    profile at matched clock-hour: observed / expected, where expected sums that
    channel's baseline rate for each trailing bin's hour. Self-referenced so
    genotype and baseline vigour cancel; phase-matched so the light-dark cycle
    cancels. Without the phase match, every channel shows a huge break at the
    lights-off transition and healthy flies flag as declining (the research
    prototype measured 8/8 such false positives before normalisation).

    Returns None when the run is too short to separate baseline from trailing, or
    the baseline is too quiet to judge (that is a low-activity channel, handled
    above, not a decline).
    """
    ts0, ts1 = rows[0]["ts"], rows[-1]["ts"]
    if (ts1 - ts0).total_seconds() / 3600.0 < DECLINE_MIN_RUN_H:
        return None
    base_end = ts0 + timedelta(hours=DECLINE_BASELINE_H)
    trail_start = ts1 - timedelta(hours=DECLINE_TRAIL_H)

    base_sum, base_cnt = {}, {}
    for r in rows:
        if r["ts"] < base_end:
            h = r["ts"].hour
            base_sum[h] = base_sum.get(h, 0) + r["counts"][ch]
            base_cnt[h] = base_cnt.get(h, 0) + 1
    if not base_cnt:
        return None
    base_rate = {h: base_sum[h] / base_cnt[h] for h in base_cnt}

    observed = expected = 0.0
    for r in rows:
        if r["ts"] >= trail_start:
            observed += r["counts"][ch]
            expected += base_rate.get(r["ts"].hour, 0.0)
    if expected < DECLINE_MIN_EXPECTED:
        return None
    return observed / expected


def light_schedule(mon):
    rows = mon["rows"]
    transitions = [
        {"ts": b["ts"].isoformat(), "to": "on" if b["light"] == 1 else "off"}
        for a, b in zip(rows, rows[1:]) if a["light"] != b["light"]
    ]
    times = {t["ts"][11:16] for t in transitions}
    return {
        "n_transitions": len(transitions),
        "transition_clock_times": sorted(times),
        "consistent": len(times) <= 2,
        "transitions": transitions[:10],
    }


def align(monitors):
    """Step 2: trim to common window on wall clock; drop the partial final bin."""
    starts = [m["rows"][0]["ts"] for m in monitors]
    ends = [m["rows"][-1]["ts"] for m in monitors]
    common_start, common_end = max(starts), min(ends)
    report = []
    for m in monitors:
        before = len(m["rows"])
        m["rows"] = [r for r in m["rows"]
                     if common_start <= r["ts"] <= common_end][:-1]  # drop final bin
        report.append({
            "file": Path(m["path"]).name,
            "reads_before": before,
            "reads_after": len(m["rows"]),
            "trimmed": before - len(m["rows"]),
        })
    return {
        "common_start": common_start.isoformat(),
        "common_end": common_end.isoformat(),
        "final_bin_dropped": True,
        "per_monitor": report,
    }


def _parse_iso(value, label):
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        print(f"--{label} '{value}' is not an ISO datetime "
              "(e.g. 2026-03-02T09:00:00).", file=sys.stderr)
        raise SystemExit(2) from None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--death-hours", type=float, default=24.0,
                    help="Trailing zero window to call a channel dead (lab convention)")
    ap.add_argument("--start", default=None,
                    help="Restrict analysis to reads at or after this ISO datetime")
    ap.add_argument("--end", default=None,
                    help="Restrict analysis to reads at or before this ISO datetime")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    win_start = _parse_iso(args.start, "start") if args.start else None
    win_end = _parse_iso(args.end, "end") if args.end else None

    paths = [p for f in args.files for p in glob.glob(f)] or args.files
    monitors, failed = [], []
    for p in paths:
        m = parse_monitor(p)
        (monitors if m else failed).append(m if m else p)

    # Analysis window: applied before classification so that death is judged WITHIN
    # the window. A fly that dies after the window end is valid data inside it — which
    # is why the window is chosen before any exclusion, not after.
    if win_start or win_end:
        kept = []
        for m in monitors:
            m["rows"] = [r for r in m["rows"]
                         if (win_start is None or r["ts"] >= win_start)
                         and (win_end is None or r["ts"] <= win_end)]
            (kept if len(m["rows"]) >= 2 else failed).append(
                m if len(m["rows"]) >= 2 else m["path"])
        monitors = kept

    if not monitors:
        print("No parseable monitor files in the analysis window.", file=sys.stderr)
        return 1

    pre = [inventory(m) for m in monitors]
    alignment = align(monitors)

    report = {"unparseable_files": failed, "inventory": pre, "alignment": alignment,
              "analysis_window": {
                  "start": win_start.isoformat() if win_start else None,
                  "end": win_end.isoformat() if win_end else None},
              "monitors": [], "decisions_required": []}

    for m in monitors:
        bin_s = infer_bin_seconds(m["rows"]) or 60
        death_bins = int(args.death_hours * 3600 / bin_s)
        chans = classify_channels(m, death_bins)
        tally = {}
        for c in chans:
            tally[c["state"]] = tally.get(c["state"], 0) + 1
        report["monitors"].append({
            "file": Path(m["path"]).name,
            "tally": tally,
            "light_schedule": light_schedule(m),
            "channels": [c for c in chans if c["state"] != "alive"],
        })
        for c in chans:
            if c["state"] == "died":
                report["decisions_required"].append(
                    f"{Path(m['path']).name} ch{c['channel']}: censor at "
                    f"{c['last_movement']} or exclude?")
            if c["state"] == "suspect":
                report["decisions_required"].append(
                    f"{Path(m['path']).name} ch{c['channel']}: "
                    f"{c.get('reason', 'suspect — review')} — include, exclude, "
                    "or censor?")
        if not light_schedule(m)["consistent"]:
            report["decisions_required"].append(
                f"{Path(m['path']).name}: light transitions at inconsistent clock times")

    out = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(out)
        print(f"Wrote {args.out}: {len(report['decisions_required'])} decisions required")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
