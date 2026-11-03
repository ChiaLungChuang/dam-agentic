"""More metamorphic relations: transformations whose effect is known in advance.

Same method as test_invariance.py — no ground truth, two runs, a relation between
them that any correct implementation must satisfy whatever the data are. Each
relation states why it must hold. A failure here is a FINDING about the system,
recorded in the PR that adds it; none of these tests is relaxed to pass.

All drive the real server over stdio and need the engine.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from conftest import requires_rtivity
from evals.harness import MCPHarness
from test_invariance import CTRL, MUT, _qc, _rows, _session, _write, assert_same

pytestmark = requires_rtivity

_FMT = "%d %b %y %H:%M:%S"


# ── transformations ───────────────────────────────────────────────────────────

def shifted_in_time(src: Path, dest: Path, delta: timedelta) -> Path:
    """`src` with every timestamp moved by `delta`; counts untouched."""
    out = []
    for r in _rows(src):
        ts = datetime.strptime(f"{r[1]} {r[2]}", _FMT) + delta
        out.append([r[0], ts.strftime("%d %b %y"), ts.strftime("%H:%M:%S"), *r[3:]])
    return _write(dest, out)


def with_channel_zeroed(src: Path, dest: Path, channel: int) -> Path:
    """`src` with one channel's counts set to 0 throughout — an empty tube."""
    col = 9 + channel  # 0-indexed tab field of channel k is 9 + k (field 9 = light)
    return _write(dest, [r[:col] + ["0"] + r[col + 1:] for r in _rows(src)])


def split_to_30s(src: Path, dest: Path) -> Path:
    """`src`, a 60 s file, as a 30 s file whose 60 s resample is exactly `src`:
    each read becomes two, at t and t + 30 s, and each count c is split
    c // 2 then c - c // 2. Summing consecutive pairs gives back every original
    count, so the two files are the same recording at two bin widths."""
    out, idx = [], 0
    for r in _rows(src):
        ts = datetime.strptime(f"{r[1]} {r[2]}", _FMT)
        counts = [int(c) for c in r[10:42]]
        halves = ([c // 2 for c in counts], [c - c // 2 for c in counts])
        for k, half in enumerate(halves):
            idx += 1
            t = ts + timedelta(seconds=30 * k)
            out.append([str(idx), t.strftime("%d %b %y"), t.strftime("%H:%M:%S"),
                        *r[3:10], *(str(c) for c in half), *r[42:]])
    return _write(dest, out)


def doubled(src: Path, dest: Path) -> Path:
    """`src` with every count doubled. Split by `split_to_30s`, a doubled count
    2c becomes (c, c): both halves move exactly when the minute moved, so the
    30 s file has the same total counts, the same waking time and the same quiet
    time as this 60 s file — with no count of 1 to split into (0, 1), which
    would turn half a moving minute quiet."""
    return _write(dest, [r[:10] + [str(2 * int(c)) for c in r[10:42]] + r[42:]
                         for r in _rows(src)])


# ── running the system ────────────────────────────────────────────────────────

async def _metrics(h, files, mapping) -> dict:
    """Sleep and activity summaries for one session."""
    sid = await _session(h, files)
    await _qc(h, sid)
    r = await h.call("assign_groups", session_id=sid, mapping=mapping)
    assert not r.is_error, r.text
    out = {}
    for tool in ("compute_sleep", "compute_activity"):
        r = await h.call(tool, session_id=sid)
        assert not r.is_error, r.text
        out[tool] = r.data["summary"]
    return out


def _one_monitor_groups(name: str) -> dict:
    return {MUT: {name: [1, 4]}, CTRL: {name: [17, 20]}}


def _rows_by_key(table: list[dict]) -> dict:
    return {(r["labels"], r.get("x")): r for r in table}


# ── relations ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shifting_every_timestamp_by_whole_days_changes_no_metric(
        monitor_files, tmp_path):
    """Moving the whole recording one week later moves nothing relative to the
    light-dark cycle — ZT0 is a clock time and a week is a whole number of
    24 h periods — and changes no count. Every summary must match. A metric that
    depends on the calendar date, or on absolute rather than elapsed time, breaks
    this. (A shift by a fraction of a day is NOT a symmetry: it moves the data
    against the declared lights-on time, which legitimately changes phase
    metrics.)"""
    src = Path(monitor_files[0])
    later = shifted_in_time(src, tmp_path / "later" / src.name, timedelta(days=7))
    async with MCPHarness(tmp_path / "s") as h:
        a = await _metrics(h, [src], _one_monitor_groups(src.name))
        b = await _metrics(h, [later], _one_monitor_groups(src.name))
    assert_same(a, b)


@pytest.mark.asyncio
async def test_emptying_an_unassigned_tube_changes_no_other_call_or_metric(
        monitor_files, tmp_path):
    """Channels are independent animals. Emptying tube 10 — assigned to no group
    — must make tube 10 an `empty` call and change nothing else: no other
    channel's QC call or evidence, and no group's metric. A reader or classifier
    that mixes channels (a neighbour's zeros leaking in, a per-file normalisation)
    breaks this."""
    src = Path(monitor_files[0])
    zeroed = with_channel_zeroed(src, tmp_path / "zero" / src.name, channel=10)
    groups = _one_monitor_groups(src.name)
    async with MCPHarness(tmp_path / "s") as h:
        qa = await _qc(h, await _session(h, [src]))
        qb = await _qc(h, await _session(h, [zeroed]))
        ma = await _metrics(h, [src], groups)
        mb = await _metrics(h, [zeroed], groups)
    others = lambda qc: {(f["channel"], f["state"], f["evidence"])  # noqa: E731
                         for f in qc["flags"] if f["channel"] != 10}
    assert others(qa) == others(qb)
    assert any(f["channel"] == 10 and f["state"] == "empty" for f in qb["flags"])
    assert_same(ma, mb)


@pytest.mark.asyncio
async def test_listing_the_monitors_in_another_order_changes_no_group_statistic(
        monitor_files, tmp_path):
    """A group spanning two monitors is the same animals whichever file is named
    first. Every group summary must match. A pipeline that keys anything by file
    position rather than by file name breaks this."""
    files = [Path(p) for p in monitor_files[:2]]
    groups = {MUT: {f.name: [1, 4] for f in files},
              CTRL: {f.name: [17, 20] for f in files}}
    async with MCPHarness(tmp_path / "s") as h:
        a = await _metrics(h, files, groups)
        b = await _metrics(h, files[::-1], groups)
    assert_same(a, b)


# The 30 s / 60 s relation is split by metric, so that each verdict stands alone.
#
# The pair is a doubled 60 s file and its exact 30 s split (`doubled`, then
# `split_to_30s`): the same counts, the same waking time and the same quiet time
# at two bin widths. Every metric below is a quantity of time or counts, not of
# rows, so each must be EQUAL at the two widths, to rounding:
#
# * total activity — a sum of counts;
# * total sleep — quiet runs cover the same minutes, and a run of n bins lasts
#   n x bin width (Rtivity-Python 0.13.0);
# * counts per waking minute — counts over waking minutes, the waking time
#   being moving bins x bin width (HANDOFF-14, "Definition changes").
#
# History: the first version split the ORIGINAL counts, so a one-count minute
# became a quiet half and a moving half, and stated tolerances (90 s per bout,
# 10 %) for that. Counts per waking minute failed it at 4.758 vs 2.564 (counts
# per bin); with the per-minute definition it still failed, at 2.165 vs 2.822,
# because that split itself halves the waking time of every one-count minute.
# The transformation was what made the comparison inexact, so it was replaced
# by one under which the relation is exact, and the tolerances tightened to
# rounding. Nothing here is looser than before.

async def _at_two_bin_widths(monitor_files, tmp_path):
    src = Path(monitor_files[0])
    sixty = doubled(src, tmp_path / "60s" / src.name)
    half = split_to_30s(sixty, tmp_path / "30s" / src.name)
    groups = _one_monitor_groups(src.name)
    async with MCPHarness(tmp_path / "s") as h:
        return (await _metrics(h, [sixty], groups), await _metrics(h, [half], groups))


@pytest.mark.asyncio
async def test_total_activity_is_the_same_at_30s_and_60s(monitor_files, tmp_path):
    m60, m30 = await _at_two_bin_widths(monitor_files, tmp_path)
    a = _rows_by_key(m60["compute_activity"]["total_activity_by_phase"])
    b = _rows_by_key(m30["compute_activity"]["total_activity_by_phase"])
    assert set(a) == set(b)
    for k in a:
        assert_same(a[k]["mean"], b[k]["mean"], f"total_activity {k}")


@pytest.mark.asyncio
async def test_total_sleep_is_the_same_at_30s_and_60s(monitor_files, tmp_path):
    m60, m30 = await _at_two_bin_widths(monitor_files, tmp_path)
    a = _rows_by_key(m60["compute_sleep"]["total_sleep_time_h"])
    b = _rows_by_key(m30["compute_sleep"]["total_sleep_time_h"])
    assert set(a) == set(b)
    for k in a:
        assert_same(a[k]["mean"], b[k]["mean"], f"total sleep {k}")


@pytest.mark.asyncio
async def test_counts_per_waking_minute_agree_at_30s_and_60s(monitor_files, tmp_path):
    m60, m30 = await _at_two_bin_widths(monitor_files, tmp_path)
    a = _rows_by_key(m60["compute_activity"]["counts_per_waking_minute_by_phase"])
    b = _rows_by_key(m30["compute_activity"]["counts_per_waking_minute_by_phase"])
    assert set(a) == set(b)
    for k in a:
        assert math.isclose(a[k]["mean"], b[k]["mean"], rel_tol=1e-9), (
            f"counts per waking minute {k}: {a[k]['mean']:.3f} at 60 s, "
            f"{b[k]['mean']:.3f} at 30 s")
