"""The generated pre-registration declaration (H13-2).

`damsim` emitted monitor files and nothing else, so a generated corpus could not
state a declared n at all — the declared-n defect class was not scored badly, it
was invisible. What is pinned here is that the emitter produces declarations the
real loader accepts, in both shapes the class needs, without moving the corpus
that already exists.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from damsim.generate import DECL_GROUPS, make_experiment       # noqa: E402


def _make(tmp_path, exp_id="exp_000", *, declaration, monitors=2):
    (tmp_path / exp_id).mkdir(parents=True, exist_ok=True)
    return make_experiment(tmp_path, exp_id, seed=7, n_monitors=monitors, days=2,
                           declaration=declaration)


# ── the two shapes the class needs ───────────────────────────────────────────

def test_a_matching_declaration_is_the_negative_control(tmp_path):
    """Without it, a checksum that refused everything would score perfectly."""
    t = _make(tmp_path, declaration=False)
    d = t.declaration
    assert d["mismatch"] is False
    assert d["declared_n"] == d["computed_n"]
    assert d["mismatched_groups"] == []


def test_a_mismatched_declaration_is_the_planted_defect(tmp_path):
    t = _make(tmp_path, "exp_001", declaration=True)
    d = t.declaration
    assert d["mismatch"] is True
    assert d["declared_n"][DECL_GROUPS[0]] != d["computed_n"][DECL_GROUPS[0]]
    assert d["mismatched_groups"] == [DECL_GROUPS[0]]
    # the other arm still matches, so a refusal cannot be blamed on both
    assert d["declared_n"][DECL_GROUPS[1]] == d["computed_n"][DECL_GROUPS[1]]


def test_the_planted_gap_is_not_a_near_miss(tmp_path):
    """An off-by-one would be indistinguishable from a channel-expansion bug when
    it fails. The gap has to be unmistakably the checksum."""
    t = _make(tmp_path, "exp_001", declaration=True)
    d = t.declaration
    gap = abs(d["declared_n"][DECL_GROUPS[0]] - d["computed_n"][DECL_GROUPS[0]])
    assert gap > 1


# ── the loader has to accept what the generator writes ───────────────────────

def test_the_emitted_filename_satisfies_the_containment_rule(tmp_path):
    """`experiment:` must appear in the filename stem or the loader refuses. A
    corpus that emits files the loader rejects tests nothing."""
    t = _make(tmp_path, "exp_000", declaration=False)
    name = t.declaration["path"]
    assert t.declaration["experiment"] in Path(name).stem
    assert (tmp_path / "exp_000" / name).exists()


def test_the_emitted_declaration_parses_and_declares_the_n(tmp_path, monkeypatch):
    """Through the real loader, not by re-reading the YAML here — the point is
    that config.py accepts it."""
    pytest.importorskip("yaml")
    from dam_mcp import config
    t = _make(tmp_path, "exp_000", declaration=True)
    path = tmp_path / "exp_000" / t.declaration["path"]
    monkeypatch.setenv("DAM_PREREG_PATH", str(path))
    assert config.declared_groups() == set(DECL_GROUPS)
    assert config.declared_n() == t.declaration["declared_n"]
    assert config.declaration_warnings() == []      # integers are read, not ignored


# ── the truth has to be usable ───────────────────────────────────────────────

def test_the_truth_carries_the_mapping_a_scorer_needs(tmp_path):
    """A scorer cannot drive assign_groups without the other half of the
    comparison. Handing over only the declaration would leave it inventing the
    mapping it is scoring."""
    t = _make(tmp_path, "exp_000", declaration=False, monitors=2)
    mapping = t.declaration["mapping"]
    assert set(mapping) == set(DECL_GROUPS)
    assert set(mapping[DECL_GROUPS[0]]) == {"Monitor1.txt", "Monitor2.txt"}
    # every monitor feeds both arms, so the confound warning does not fire on
    # every experiment and bury the signal being scored
    assert set(mapping[DECL_GROUPS[0]]) == set(mapping[DECL_GROUPS[1]])


def test_computed_n_matches_what_the_mapping_actually_assigns(tmp_path):
    """The truth's computed_n is a claim about the mapping. If the two drift, the
    corpus scores against a number nothing produces."""
    t = _make(tmp_path, "exp_000", declaration=False, monitors=2)
    d = t.declaration
    for label, per_file in d["mapping"].items():
        total = sum(hi - lo + 1 for lo, hi in per_file.values())
        assert total == d["computed_n"][label]


# ── no drift into the existing corpus ────────────────────────────────────────

def test_without_the_flag_no_declaration_and_no_key_in_the_truth(tmp_path):
    """The existing corpus is a baseline people compare eval numbers against, so
    the opt-out path must produce the file it always produced — not the same file
    plus a null key."""
    t = _make(tmp_path, "exp_000", declaration=None)
    assert t.declaration is None
    assert not list((tmp_path / "exp_000").glob("contrasts-*.yaml"))
    truth = json.loads((tmp_path / "exp_000" / "ground_truth.json").read_text())
    assert "declaration" not in truth


def test_emitting_a_declaration_does_not_move_the_monitor_files(tmp_path):
    """The emitter draws no random numbers. One draw would shift every subsequent
    one and silently change the planted defects at the same seed — which is the
    failure mode 'do not change what --adversarial produces' is guarding."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make(a, "exp_000", declaration=None)
    _make(b, "exp_000", declaration=True)
    for name in ("Monitor1.txt", "Monitor2.txt"):
        assert (a / "exp_000" / name).read_bytes() == (b / "exp_000" / name).read_bytes()


def test_the_planted_defect_counts_are_identical_with_and_without(tmp_path):
    """The same claim one level up: the ground truth about defects is unchanged,
    so an existing scorer reports the same numbers."""
    a = _make(tmp_path / "a", "exp_000", declaration=None)
    b = _make(tmp_path / "b", "exp_000", declaration=True)
    for ma, mb in zip(a.monitors, b.monitors):
        assert ma.empty_channels == mb.empty_channels
        assert ma.deaths == mb.deaths
        assert ma.low_activity_channels == mb.low_activity_channels
    assert a.n_alive_expected == b.n_alive_expected
