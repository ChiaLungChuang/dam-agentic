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
class DeclarationTruth:
    """Ground truth for an emitted pre-registration declaration (H13-2).

    The generator emitted monitor files and nothing else, so a generated corpus
    could not state a declared n at all — which meant the declared-n defect class
    was not scored badly, it was invisible. This is the planted truth for it.

    `mapping` is carried because a scorer cannot drive `assign_groups` without one:
    the declaration states how many animals a group has, the mapping states which
    channels carry them, and the checksum compares the two. Handing over only the
    declaration would leave the scorer to invent the other half of the comparison
    it is scoring."""
    path: str                              # relative to the experiment directory
    experiment: str                        # the declaration's experiment: value
    declared_n: dict[str, int]             # group -> declared animal count
    computed_n: dict[str, int]             # group -> channels the mapping assigns
    mapping: dict                          # the assign_groups argument
    mismatch: bool                         # True => assign_groups must REFUSE
    mismatched_groups: list[str] = field(default_factory=list)


@dataclass
class ExperimentTruth:
    experiment: str
    seed: int
    monitors: list[MonitorTruth]
    staggered_start: bool
    common_window: list[str]
    partial_final_bin: bool = True
    n_alive_expected: int = 0
    # None unless --declarations was passed, and OMITTED from the serialised truth
    # entirely in that case (see make_experiment). A `"declaration": null` key
    # would be more explicit, but it would also change every ground_truth.json in
    # a corpus generated without the flag, and the existing corpus is a baseline
    # people compare eval numbers against. Byte-identical wins over explicit here;
    # a reader uses truth.get("declaration"), which handles both.
    declaration: dict | None = None


#: Group labels the emitted declarations use. Deliberately generic: a synthetic
#: corpus that names genotypes invites someone to read biology into it.
DECL_GROUPS = ("grp_a", "grp_b")

#: What a mismatched declaration overstates its first group's n by. A near-miss
#: would be indistinguishable from an off-by-one in the channel expansion when it
#: fails; this size can only be the checksum.
DECL_MISMATCH_DELTA = 7


def write_declaration(outdir, exp_id, n_monitors, *, mismatch):
    """Emit a pre-registration declaration next to the monitor files.

    Two shapes, and both are needed or the class is only half-scorable: a matching
    declaration is the negative control (assign_groups must PROCEED — without it, a
    checksum that refused everything would score perfectly), and a mismatched one
    is the planted defect (assign_groups must REFUSE, naming the group and both
    numbers).

    The filename satisfies the loader's containment rule — `contrasts-<exp_id>.yaml`
    contains the `experiment:` value `<exp_id>` — because a declaration whose stem
    does not name its experiment is refused at load, and a corpus that emits files
    the loader rejects tests nothing.

    Groups split each monitor's channels in half, so every monitor contributes to
    both arms: a split by monitor would trip the confound warning on every
    experiment and bury the signal being scored.

    Consumes no randomness, deliberately. Drawing even one number here would shift
    every subsequent draw and silently move the existing corpus — the 20
    experiments / 46 late deaths / 41 gradual declines baseline is a number people
    compare against.
    """
    half = N_CHANNELS // 2
    computed = {DECL_GROUPS[0]: half * n_monitors,
                DECL_GROUPS[1]: half * n_monitors}
    declared = dict(computed)
    mismatched: list[str] = []
    if mismatch:
        declared[DECL_GROUPS[0]] = computed[DECL_GROUPS[0]] + DECL_MISMATCH_DELTA
        mismatched = [DECL_GROUPS[0]]

    mapping = {
        DECL_GROUPS[0]: {f"Monitor{m + 1}.txt": [1, half]
                         for m in range(n_monitors)},
        DECL_GROUPS[1]: {f"Monitor{m + 1}.txt": [half + 1, N_CHANNELS]
                         for m in range(n_monitors)},
    }

    name = f"contrasts-{exp_id}.yaml"
    body = [
        "# GENERATED by damsim/generate.py --declarations. Not a real",
        "# pre-registration: a real one is written by a human before the data is",
        "# seen, and its commit is the timestamp. This exists so a generated corpus",
        "# can express a declared n at all (HANDOFF-7 H13-2).",
        "#",
        f"# Planted: {'MISMATCH — assign_groups must REFUSE' if mismatch else 'MATCH — assign_groups must PROCEED'}.",
        "#",
        "# Point the server at this file; DAM_PREREG_PATH has no default and the",
        "# server refuses every grouping and contrast tool without it:",
        "#",
        f"#     DAM_PREREG_PATH=<corpus>/{exp_id}/{name} python -m dam_mcp.server",
        "#",
        "# The path is also in this experiment's ground_truth.json under",
        "# declaration.path, and in the corpus manifest.json, so a runner can",
        "# resolve it without guessing the naming convention.",
        "",
        f"experiment: {exp_id}",
        "groups:",
    ]
    body += [f"  {label}: {declared[label]}" for label in DECL_GROUPS]
    (Path(outdir) / exp_id / name).write_text("\n".join(body) + "\n")

    return DeclarationTruth(
        path=name, experiment=exp_id, declared_n=declared, computed_n=computed,
        mapping=mapping, mismatch=mismatch, mismatched_groups=mismatched,
    )


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


def make_experiment(outdir, exp_id, seed, n_monitors=2, days=3, adversarial=False,  # noqa
                    declaration=None):
    """`declaration` is None (emit none), True (emit a mismatched one) or False
    (emit a matching one). It is resolved by the caller rather than drawn here, so
    the emitter stays out of the random stream — see write_declaration."""
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
                # Dies inside the final <12 h — below the detector's trailing-zero
                # window at the production default (death_hours=12), so it is
                # structurally undetectable there. Kept short enough that the ~24 h
                # decline check still sees mostly activity and does not flag it.
                death_bin = random.randint(n_bins - 700, n_bins - 100)
                channels[ch] = simulate_channel(n_bins, start, alive_until_bin=death_bin)
                lm = max(i for i, v in enumerate(channels[ch]) if v > 0)
                late_deaths[str(ch + 1)] = (start + timedelta(minutes=lm)).isoformat()
            elif ch in declining:
                channels[ch] = simulate_decline(n_bins, start,
                                                random.randint(int(n_bins * 0.3),
                                                               int(n_bins * 0.6)))
                declines[str(ch + 1)] = "ambiguous"
            elif ch in dead:
                # Death must leave enough trailing zeros to be callable at the death
                # window (>=12 h here), else the label is a lie the detector can't be
                # blamed for. n_bins-1500 (~25 h) clears both 12 h and 24 h windows.
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
    # After the monitor files and after every random draw, so enabling it cannot
    # move the corpus that already exists.
    if declaration is not None:
        truth.declaration = asdict(
            write_declaration(outdir, exp_id, n_monitors, mismatch=declaration))

    payload = asdict(truth)
    if truth.declaration is None:
        payload.pop("declaration")          # byte-identical without --declarations
    (Path(outdir) / exp_id / "ground_truth.json").write_text(
        json.dumps(payload, indent=2))
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
    ap.add_argument("--declarations", action="store_true",
                    help="Also emit a pre-registration declaration per experiment "
                         "(contrasts-<exp_id>.yaml), alternating a matching one "
                         "(assign_groups must proceed) with a mismatched one "
                         "(assign_groups must refuse). Opt-in: without it the "
                         "corpus is byte-identical to before this flag existed.")
    args = ap.parse_args()

    out = Path(args.out)
    manifest = []
    for i in range(args.n_experiments):
        exp_id = f"exp_{i:03d}"
        (out / exp_id).mkdir(parents=True, exist_ok=True)
        # Alternating by index, not by a draw: both classes are needed and the
        # split must be reproducible from the command line alone. Odd experiments
        # carry the planted mismatch, even ones the negative control.
        decl = (i % 2 == 1) if args.declarations else None
        t = make_experiment(out, exp_id, args.seed + i, args.monitors, args.days,
                            adversarial=args.adversarial, declaration=decl)
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
        # Added only when there is one, so a corpus generated without
        # --declarations produces the manifest it always produced.
        if t.declaration:
            manifest[-1]["declaration"] = t.declaration["path"]
            manifest[-1]["declaration_mismatch"] = t.declaration["mismatch"]

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
    if args.declarations:
        n_mismatch = sum(1 for m in manifest if m.get("declaration_mismatch"))
        n_match = sum(1 for m in manifest
                      if m.get("declaration_mismatch") is False)
        print(f"  declarations: {n_mismatch} planted declared-n mismatches "
              f"(assign_groups must refuse), {n_match} matching "
              "(negative control — it must proceed)")
        print("  point the server at one per experiment: "
              "DAM_PREREG_PATH=<corpus>/exp_000/contrasts-exp_000.yaml")


if __name__ == "__main__":
    main()
