#!/usr/bin/env python3
"""Synthesize TriKinetics DAM monitor files with planted defects and exact ground truth.

The point: hand-labelling real files is slow, error-prone, and gives you a fixed test
set you will inevitably overfit to. Planting defects gives you exact labels for free and
lets you regenerate a fresh held-out set with one seed change.

Real experiments remain the validation set — synthetic data proves the detector works on
defects you know are there; real data proves it works on the ones you don't.

Usage:
    python generate.py --out corpus/ --n-experiments 20 --seed 42
    python generate.py --out holdout/ --n-experiments 10 --seed 1337
"""

import argparse
import json
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

N_CHANNELS = 32
BIN_MINUTES = 1


@dataclass
class MonitorTruth:
    """Ground truth for one monitor file. Every field is planted, not inferred."""
    filename: str
    start: str
    n_reads: int
    empty_channels: list[int] = field(default_factory=list)
    deaths: dict[str, str] = field(default_factory=dict)      # channel -> last movement
    low_activity_channels: list[int] = field(default_factory=list)
    late_deaths: dict[str, str] = field(default_factory=dict)   # adversarial
    declining_channels: dict[str, str] = field(default_factory=dict)  # adversarial
    light_schedule: str = "LD 12:12"
    zt0_clock: str = "09:00"


@dataclass
class ExperimentTruth:
    experiment: str
    seed: int
    monitors: list[MonitorTruth]
    staggered_start: bool
    common_window: list[str]
    partial_final_bin: bool = True
    n_alive_expected: int = 0


def simulate_channel(n_bins, start_dt, alive_until_bin=None, level="normal"):
    """Beam-break counts for one tube. Crepuscular-ish, higher in light phase."""
    counts = []
    for i in range(n_bins):
        t = start_dt + timedelta(minutes=i * BIN_MINUTES)
        if alive_until_bin is not None and i > alive_until_bin:
            counts.append(0)
            continue
        light = 9 <= t.hour < 21
        if level == "low":
            counts.append(random.choice([0] * 90 + [1]))
        elif light:
            # morning/evening peaks
            peak = t.hour in (9, 10, 19, 20)
            counts.append(random.randint(0, 14 if peak else 7))
        else:
            counts.append(random.randint(0, 2))
    return counts


def write_monitor(path, start_dt, n_bins, channels):
    """Emit a standard 42-column DAM2 monitor file."""
    with open(path, "w") as fh:
        for i in range(n_bins):
            t = start_dt + timedelta(minutes=i * BIN_MINUTES)
            light = 1 if 9 <= t.hour < 21 else 0
            row = [
                str(i + 1),
                t.strftime("%d %b %y"),
                t.strftime("%H:%M:%S"),
                "1",      # status
                "0",      # extras
                "1",      # monitor number
                "0",      # tube (unused in DAM2)
                "1",      # data type
                "0",      # unused
                str(light),
            ] + [str(channels[ch][i]) for ch in range(N_CHANNELS)]
            fh.write("\t".join(row) + "\n")


def simulate_decline(n_bins, start_dt, decline_start_bin):
    """A fly that tapers toward death without ever fully stopping.

    This is the honest hard case. There is no fact of the matter about when this fly
    'died', which means the detector cannot be scored on it — it can only be scored on
    whether it *surfaces the ambiguity* instead of silently picking a side.
    """
    counts = []
    for i in range(n_bins):
        t = start_dt + timedelta(minutes=i * BIN_MINUTES)
        if i < decline_start_bin:
            light = 9 <= t.hour < 21
            counts.append(random.randint(0, 7 if light else 2))
        else:
            frac = 1.0 - min(1.0, (i - decline_start_bin) / (n_bins - decline_start_bin))
            counts.append(1 if random.random() < frac * 0.15 else 0)
    return counts


def make_experiment(outdir, exp_id, seed, n_monitors=2, days=3, adversarial=False):  # noqa
    random.seed(seed)
    base = datetime(2026, 3, 2, 9, 0, 0)
    n_bins_nominal = days * 24 * 60

    monitors, starts, ends = [], [], []
    staggered = random.random() < 0.7

    for m in range(n_monitors):
        offset = random.randint(0, 9) if staggered else 0
        start = base + timedelta(minutes=offset)
        n_bins = n_bins_nominal - random.randint(0, 6)

        # Plant defects. Counts are deliberately modest — a monitor where half the
        # tubes are broken is not a realistic test of a detector.
        pool = list(range(N_CHANNELS))
        random.shuffle(pool)
        n_empty = random.randint(0, 3)
        n_dead = random.randint(0, 3)
        n_low = random.randint(0, 2)
        empty = sorted(pool[:n_empty])
        dead = sorted(pool[n_empty:n_empty + n_dead])
        low = sorted(pool[n_empty + n_dead:n_empty + n_dead + n_low])

        # Adversarial cases: defects the detector was NOT written to catch.
        # These exist to make the eval informative rather than self-congratulatory.
        late_dead, declining = [], []
        cursor = n_empty + n_dead + n_low
        if adversarial:
            late_dead = sorted(pool[cursor:cursor + random.randint(0, 2)])
            cursor += len(late_dead)
            declining = sorted(pool[cursor:cursor + random.randint(0, 2)])

        channels, deaths, late_deaths, declines = {}, {}, {}, {}
        for ch in range(N_CHANNELS):
            if ch in empty:
                channels[ch] = [0] * n_bins
            elif ch in late_dead:
                # Dies inside the final 24 h — below the detector's trailing-zero
                # window, so it is structurally undetectable at death_hours=24.
                death_bin = random.randint(n_bins - 1200, n_bins - 200)
                channels[ch] = simulate_channel(n_bins, start, alive_until_bin=death_bin)
                lm = max(i for i, v in enumerate(channels[ch]) if v > 0)
                late_deaths[str(ch + 1)] = (start + timedelta(minutes=lm)).isoformat()
            elif ch in declining:
                channels[ch] = simulate_decline(n_bins, start,
                                                random.randint(int(n_bins * 0.3),
                                                               int(n_bins * 0.6)))
                declines[str(ch + 1)] = "ambiguous"
            elif ch in dead:
                # Death must leave enough trailing zeros to be callable at 24 h,
                # otherwise the label is a lie the detector can't be blamed for.
                death_bin = random.randint(int(n_bins * 0.25), n_bins - 1500)
                channels[ch] = simulate_channel(n_bins, start, alive_until_bin=death_bin)
                last_move = max(i for i, v in enumerate(channels[ch]) if v > 0)
                deaths[str(ch + 1)] = (start + timedelta(minutes=last_move)).isoformat()
            elif ch in low:
                channels[ch] = simulate_channel(n_bins, start, level="low")
            else:
                channels[ch] = simulate_channel(n_bins, start)

        fname = f"Monitor{m + 1}.txt"
        write_monitor(Path(outdir) / exp_id / fname, start, n_bins, channels)

        monitors.append(MonitorTruth(
            filename=fname,
            start=start.isoformat(),
            n_reads=n_bins,
            empty_channels=[c + 1 for c in empty],
            deaths=deaths,
            low_activity_channels=[c + 1 for c in low],
            late_deaths=late_deaths,
            declining_channels=declines,
        ))
        starts.append(start)
        ends.append(start + timedelta(minutes=n_bins - 1))

    truth = ExperimentTruth(
        experiment=exp_id,
        seed=seed,
        monitors=monitors,
        staggered_start=staggered,
        common_window=[max(starts).isoformat(), min(ends).isoformat()],
        n_alive_expected=sum(
            N_CHANNELS - len(m.empty_channels) - len(m.deaths) - len(m.low_activity_channels)
            for m in monitors
        ),
    )
    (Path(outdir) / exp_id / "ground_truth.json").write_text(
        json.dumps(asdict(truth), indent=2))
    return truth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-experiments", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--monitors", type=int, default=2)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--adversarial", action="store_true",
                    help="Plant defects the detector was not written to catch")
    args = ap.parse_args()

    out = Path(args.out)
    manifest = []
    for i in range(args.n_experiments):
        exp_id = f"exp_{i:03d}"
        (out / exp_id).mkdir(parents=True, exist_ok=True)
        t = make_experiment(out, exp_id, args.seed + i, args.monitors, args.days,
                            adversarial=args.adversarial)
        manifest.append({
            "experiment": exp_id,
            "n_monitors": len(t.monitors),
            "staggered": t.staggered_start,
            "n_empty": sum(len(m.empty_channels) for m in t.monitors),
            "n_dead": sum(len(m.deaths) for m in t.monitors),
            "n_low": sum(len(m.low_activity_channels) for m in t.monitors),
            "n_late_dead": sum(len(m.late_deaths) for m in t.monitors),
            "n_declining": sum(len(m.declining_channels) for m in t.monitors),
        })

    (out / "manifest.json").write_text(json.dumps(
        {"seed": args.seed, "n_experiments": args.n_experiments, "experiments": manifest},
        indent=2))

    def tot(k):
        return sum(m[k] for m in manifest)
    print(f"{args.n_experiments} experiments -> {out}")
    print(f"  planted: {tot('n_empty')} empty, {tot('n_dead')} deaths, "
          f"{tot('n_low')} low-activity, "
          f"{sum(1 for m in manifest if m['staggered'])} staggered starts")
    if args.adversarial:
        print(f"  adversarial: {tot('n_late_dead')} late deaths (< 24 h window), "
              f"{tot('n_declining')} gradual declines (no ground-truth answer)")


if __name__ == "__main__":
    main()
