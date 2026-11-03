"""The declared-n scorer (H13-2): it must be able to pass AND to fail.

`damsim/score_declarations.py` drives the real server per experiment and grades
assign_groups' refuse/proceed against what `generate.py --declarations` planted.
A scorer that only ever reports 1.000 measures the generator, not the system
(CLAUDE.md, Evaluation), so each direction has its own test.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from damsim.score_declarations import gate, score_corpus

ROOT = Path(__file__).resolve().parent.parent


def _corpus(out: Path, *flags: str) -> Path:
    subprocess.run(
        [sys.executable, str(ROOT / "damsim" / "generate.py"), "--out", str(out),
         "--n-experiments", "4", "--seed", "11", "--monitors", "1", "--days", "2",
         *flags],
        check=True, capture_output=True)
    return out


def test_every_planted_mismatch_is_refused_and_every_match_proceeds(tmp_path):
    report = asyncio.run(score_corpus(_corpus(tmp_path / "c", "--declarations")))
    assert report["n_scored"] == 4 and report["n_errored"] == 0
    agg = report["aggregate"]
    assert (agg["n_planted"], agg["n_matching"]) == (2, 2)
    r = agg["declared_n_mismatch"]
    assert (r["tp"], r["fp"], r["fn"]) == (2, 0, 0)
    assert agg["refusals_naming_group_and_numbers"] == 2


def test_the_scorer_fails_when_the_outcomes_contradict_the_plant(tmp_path):
    """Invert every plant in ground_truth.json and leave the declarations alone:
    the server still refuses the same two experiments, which are now graded as
    false positives, and the two it lets through are now misses. A scorer that
    still reported 1.000 here would be grading nothing."""
    corpus = _corpus(tmp_path / "c", "--declarations")
    for gt in corpus.glob("exp_*/ground_truth.json"):
        truth = json.loads(gt.read_text())
        truth["declaration"]["mismatch"] = not truth["declaration"]["mismatch"]
        gt.write_text(json.dumps(truth))
    r = asyncio.run(score_corpus(corpus))["aggregate"]["declared_n_mismatch"]
    assert (r["tp"], r["fp"], r["fn"]) == (0, 2, 2)
    assert r["precision"] == 0.0 and r["recall"] == 0.0


def test_a_corpus_without_declarations_scores_nothing_rather_than_perfectly(tmp_path):
    report = asyncio.run(score_corpus(_corpus(tmp_path / "c")))
    assert report["n_scored"] == 0 and report["n_without_declaration"] == 4
    r = report["aggregate"]["declared_n_mismatch"]
    assert r["precision"] is None and r["recall"] is None


def test_the_ci_gate_passes_only_when_every_plant_is_graded_as_planted(tmp_path):
    good = asyncio.run(score_corpus(_corpus(tmp_path / "good", "--declarations")))
    assert gate(good) == []
    corpus = _corpus(tmp_path / "bad", "--declarations")
    for gt in corpus.glob("exp_*/ground_truth.json"):
        truth = json.loads(gt.read_text())
        truth["declaration"]["mismatch"] = not truth["declaration"]["mismatch"]
        gt.write_text(json.dumps(truth))
    reasons = gate(asyncio.run(score_corpus(corpus)))
    assert any("PROCEEDED" in r for r in reasons)
    assert any("REFUSED" in r for r in reasons)


def test_the_ci_gate_fails_on_no_data(tmp_path):
    assert any("NO DATA" in r for r in gate(asyncio.run(score_corpus(_corpus(tmp_path / "c")))))
