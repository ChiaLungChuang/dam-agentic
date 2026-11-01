"""Score the declared-n checksum against a generated corpus (HANDOFF-7 H13-2).

`generate.py --declarations` plants, per experiment, a declaration whose group
sizes either match the mapping in ground_truth.json (assign_groups must PROCEED)
or overstate one group (assign_groups must REFUSE, naming the group and both
numbers). Until this script the corpus could express that defect class and
nothing ran it. This drives the real server over stdio — one server per
experiment, pointed at that experiment's declaration — and grades each outcome
against the plant.

The class is `declared_n_mismatch`, scored like score.py's channel states: a
refusal is a positive call.

  tp  planted mismatch, refused
  fp  matching declaration, refused   (a checksum that refuses everything)
  fn  planted mismatch, proceeded     (the defect this exists to catch)

A refusal of a planted mismatch that does not name the group and both numbers
counts as a tp for the class but is reported separately: the error string is
read by a model deciding what to do next, and a refusal it cannot act on is
half a refusal.

Usage:
    python damsim/generate.py --out /tmp/c --n-experiments 6 --declarations
    python damsim/score_declarations.py --corpus /tmp/c
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from damsim.score import prf  # noqa: E402
from evals.harness import MCPHarness  # noqa: E402


def _names_the_mismatch(text: str, decl: dict) -> bool:
    return all(g in text and str(decl["declared_n"][g]) in text
               and str(decl["computed_n"][g]) in text
               for g in decl["mismatched_groups"])


async def score_experiment(exp_dir: Path, decl: dict) -> dict:
    """Run load_experiment then assign_groups(mapping) against this experiment's
    declaration, and grade the outcome against the plant."""
    result = {"experiment": exp_dir.name, "planted_mismatch": decl["mismatch"]}
    paths = sorted(str(p) for p in exp_dir.glob("Monitor*.txt"))
    saved = os.environ.get("DAM_PREREG_PATH")
    os.environ["DAM_PREREG_PATH"] = str(exp_dir / decl["path"])
    try:
        with tempfile.TemporaryDirectory() as state:
            async with MCPHarness(state) as h:
                r = await h.call("load_experiment", paths=paths, name=exp_dir.name)
                if r.is_error:
                    return {**result, "error": f"load_experiment refused: {r.text}"}
                a = await h.call("assign_groups", session_id=r.data["session_id"],
                                 mapping=decl["mapping"])
    finally:
        if saved is None:
            os.environ.pop("DAM_PREREG_PATH", None)
        else:
            os.environ["DAM_PREREG_PATH"] = saved
    result["refused"] = a.is_error
    if a.is_error:
        result["refusal"] = a.text
        result["names_group_and_numbers"] = _names_the_mismatch(a.text, decl)
    return result


def aggregate(per_exp: list[dict]) -> dict:
    scored = [e for e in per_exp if "refused" in e]
    tp = sum(e["planted_mismatch"] and e["refused"] for e in scored)
    fp = sum(not e["planted_mismatch"] and e["refused"] for e in scored)
    fn = sum(e["planted_mismatch"] and not e["refused"] for e in scored)
    return {
        "declared_n_mismatch": prf(tp, fp, fn),
        "refusals_naming_group_and_numbers": sum(
            1 for e in scored if e["planted_mismatch"] and e.get("names_group_and_numbers")),
        "n_planted": sum(e["planted_mismatch"] for e in scored),
        "n_matching": sum(not e["planted_mismatch"] for e in scored),
    }


async def score_corpus(corpus: Path) -> dict:
    per_exp, skipped = [], []
    for exp_dir in sorted(d for d in corpus.iterdir() if d.is_dir()):
        truth = json.loads((exp_dir / "ground_truth.json").read_text())
        decl = truth.get("declaration")
        if not decl:
            skipped.append(exp_dir.name)
            continue
        per_exp.append(await score_experiment(exp_dir, decl))
    return {"n_scored": len(per_exp), "n_without_declaration": len(skipped),
            "n_errored": sum("error" in e for e in per_exp),
            "aggregate": aggregate(per_exp), "per_experiment": per_exp}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    report = asyncio.run(score_corpus(Path(args.corpus)))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))

    if not report["n_scored"]:
        print("NO DATA: no experiment in this corpus carries a declaration. "
              "Generate it with `damsim/generate.py --declarations`.")
        return 1
    agg = report["aggregate"]
    r = agg["declared_n_mismatch"]

    def fmt(v):
        return f"{v:7.3f}" if v is not None else f"{'--':>7}"
    print(f"Scored {report['n_scored']} declarations ({report['n_errored']} errored; "
          f"{agg['n_planted']} planted mismatches, {agg['n_matching']} matching)\n")
    print(f"{'defect':<20} {'prec':>7} {'recall':>7} {'f1':>7}   {'tp':>4} {'fp':>4} {'fn':>4}")
    print("-" * 65)
    print(f"{'declared_n_mismatch':<20} {fmt(r['precision'])} {fmt(r['recall'])} "
          f"{fmt(r['f1'])}   {r['tp']:>4} {r['fp']:>4} {r['fn']:>4}")
    print(f"\nrefusals naming the group and both numbers: "
          f"{agg['refusals_naming_group_and_numbers']}/{r['tp']}")
    for e in report["per_experiment"]:
        if "error" in e:
            print(f"  ERROR {e['experiment']}: {e['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
