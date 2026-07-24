#!/usr/bin/env python3
"""Score a QC run against planted ground truth.

Reports per-defect-class precision/recall rather than one aggregate number, because
"87% accurate" hides which failure you have — and the failure mode is the thing you
actually need to know. Missing a dead fly and flagging a healthy one are different
errors with different costs, and an aggregate score averages that information away.

Usage:
    python score.py --corpus corpus/ --qc-cmd "python ../dam-qc/scripts/validate_dam.py"
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else None
    r = tp / (tp + fn) if (tp + fn) else None
    f = 2 * p * r / (p + r) if p and r else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f}


def score_experiment(exp_dir, qc_cmd, death_hours):
    truth = json.loads((exp_dir / "ground_truth.json").read_text())
    files = sorted(str(p) for p in exp_dir.glob("Monitor*.txt"))

    out_path = exp_dir / "qc_output.json"
    cmd = qc_cmd.split() + files + ["--out", str(out_path), "--death-hours", str(death_hours)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"experiment": truth["experiment"], "error": proc.stderr.strip()[:200]}
    qc = json.loads(out_path.read_text())

    # --- Channel state classification -------------------------------------------
    pred = {}   # (file, channel) -> state
    for m in qc["monitors"]:
        for c in m["channels"]:
            pred[(m["file"], c["channel"])] = c["state"]

    gt = {}
    for m in truth["monitors"]:
        for ch in m["empty_channels"]:
            gt[(m["filename"], ch)] = "empty"
        for ch in m["deaths"]:
            gt[(m["filename"], int(ch))] = "died"
        for ch in m["low_activity_channels"]:
            gt[(m["filename"], ch)] = "suspect"

    # Adversarial: late deaths are structurally undetectable at a 24 h window.
    # Score them separately and label them a KNOWN LIMITATION rather than folding
    # them into recall, which would blame the detector for a stated design bound.
    late_missed, late_found = 0, 0
    for m in truth["monitors"]:
        for ch in m.get("late_deaths", {}):
            got = pred.get((m["filename"], int(ch)))
            if got == "died":
                late_found += 1
            else:
                late_missed += 1

    # Gradual declines have no correct answer. The only thing worth scoring is
    # whether the detector surfaced them for a human instead of silently deciding.
    decline_surfaced, decline_silent = 0, 0
    for m in truth["monitors"]:
        for ch in m.get("declining_channels", {}):
            got = pred.get((m["filename"], int(ch)))
            if got in ("suspect", "died"):
                decline_surfaced += 1
            else:
                decline_silent += 1

    # Declining and late-death channels are graded in the dedicated blocks above and
    # were never ground-truthed into empty/died/suspect. Exclude them from the
    # standard PRF so that correctly surfacing a decline as `suspect` is not then
    # miscounted as a suspect false positive — a class the channel never belonged to.
    adversarial = set()
    for m in truth["monitors"]:
        for ch in m.get("declining_channels", {}):
            adversarial.add((m["filename"], int(ch)))
        for ch in m.get("late_deaths", {}):
            adversarial.add((m["filename"], int(ch)))

    results = {}
    for state in ("empty", "died", "suspect"):
        g = {k for k, v in gt.items() if v == state}
        p = {k for k, v in pred.items() if v == state and k not in adversarial}
        results[state] = prf(len(g & p), len(p - g), len(g - p))

    # --- Alignment ---------------------------------------------------------------
    align = qc["alignment"]
    common_start_ok = align["common_start"] == truth["common_window"][0]
    # The detector drops the partial final bin, so its common_end is one bin earlier
    # than the raw common window. Score against that expectation, not the raw truth.
    gt_end = datetime.fromisoformat(truth["common_window"][1])
    pred_end = datetime.fromisoformat(align["common_end"])
    common_end_ok = abs((gt_end - pred_end).total_seconds()) <= 60

    # --- Death timing ------------------------------------------------------------
    timing_errors = []
    for m in qc["monitors"]:
        gm = next(x for x in truth["monitors"] if x["filename"] == m["file"])
        for c in m["channels"]:
            if c["state"] == "died" and str(c["channel"]) in gm["deaths"]:
                t_true = datetime.fromisoformat(gm["deaths"][str(c["channel"])])
                t_pred = datetime.fromisoformat(c["last_movement"])
                timing_errors.append(abs((t_true - t_pred).total_seconds()) / 60)

    return {
        "experiment": truth["experiment"],
        "channel_states": results,
        "alignment": {
            "common_start_correct": common_start_ok,
            "common_end_within_1_bin": common_end_ok,
            "final_bin_dropped": align["final_bin_dropped"],
        },
        "death_timing_error_minutes": {
            "n": len(timing_errors),
            "max": max(timing_errors) if timing_errors else None,
        },
        "n_decisions_surfaced": len(qc["decisions_required"]),
        "adversarial": {
            "late_deaths_found": late_found,
            "late_deaths_missed": late_missed,
            "declines_surfaced": decline_surfaced,
            "declines_silently_passed": decline_silent,
        },
    }


def aggregate(per_exp):
    agg = {}
    for state in ("empty", "died", "suspect"):
        tp = sum(e["channel_states"][state]["tp"] for e in per_exp if "channel_states" in e)
        fp = sum(e["channel_states"][state]["fp"] for e in per_exp if "channel_states" in e)
        fn = sum(e["channel_states"][state]["fn"] for e in per_exp if "channel_states" in e)
        agg[state] = prf(tp, fp, fn)
    adv = [e for e in per_exp if "adversarial" in e]
    if adv:
        agg["adversarial"] = {
            k: sum(e["adversarial"][k] for e in adv)
            for k in ("late_deaths_found", "late_deaths_missed",
                      "declines_surfaced", "declines_silently_passed")
        }
    ok = [e for e in per_exp if "alignment" in e]
    agg["alignment"] = {
        "common_start_correct": sum(e["alignment"]["common_start_correct"] for e in ok),
        "n": len(ok),
    }
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--qc-cmd", required=True)
    ap.add_argument("--death-hours", type=float, default=12.0,
                    help="Match production's DEFAULT_DEATH_HOURS so the eval scores "
                         "the detector in the configuration it actually runs in.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    corpus = Path(args.corpus)
    exps = sorted(d for d in corpus.iterdir() if d.is_dir())
    per_exp = [score_experiment(d, args.qc_cmd, args.death_hours) for d in exps]

    failed = [e for e in per_exp if "error" in e]
    report = {"n_experiments": len(exps), "n_errored": len(failed),
              "aggregate": aggregate(per_exp), "per_experiment": per_exp}

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))

    print(f"Scored {len(exps)} experiments ({len(failed)} errored)\n")
    print(f"{'defect':<10} {'prec':>7} {'recall':>7} {'f1':>7}   {'tp':>4} {'fp':>4} {'fn':>4}")
    print("-" * 55)
    def fmt(v):
        return f"{v:7.3f}" if v is not None else f"{'--':>7}"
    for state in ("empty", "died", "suspect"):
        r = report["aggregate"][state]
        print(f"{state:<10} {fmt(r['precision'])} {fmt(r['recall'])} {fmt(r['f1'])}   "
              f"{r['tp']:>4} {r['fp']:>4} {r['fn']:>4}")
    a = report["aggregate"]["alignment"]
    print(f"\nalignment: common start correct in {a['common_start_correct']}/{a['n']}")
    adv = report["aggregate"].get("adversarial")
    if adv and (adv["late_deaths_found"] + adv["late_deaths_missed"]):
        lt = adv["late_deaths_found"] + adv["late_deaths_missed"]
        dc = adv["declines_surfaced"] + adv["declines_silently_passed"]
        print("\nADVERSARIAL (defects the detector was not written to catch)")
        print(f"  late deaths (in final death-window):  found "
              f"{adv['late_deaths_found']}/{lt}  -> KNOWN LIMITATION, not a bug")
        print(f"  gradual declines:    surfaced {adv['declines_surfaced']}/{dc}, "
              f"silently passed {adv['declines_silently_passed']}")
        print("  (declines have no ground-truth answer; the only failure is deciding silently)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
