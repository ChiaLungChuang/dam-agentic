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
# Tolerances are stated from the transformation, before looking at the result:
#
# * Total activity is a sum of counts; splitting a count into halves preserves
#   it exactly. Tolerance: none.
# * Sleep is ">= 5 min without a count". Halving can only ADD immobility: a
#   minute with one count becomes a quiet 30 s plus a moving 30 s. So 30 s sleep
#   may exceed 60 s sleep, by at most one 30 s bin at each end of each bout, plus
#   the half-bin difference in how a bout's length is measured at two bin widths
#   (Rtivity measures a bout first-bin to last-bin). Tolerance: 90 s per bout, on
#   the 60 s bout count.
# * Counts per waking minute is a rate per minute. The same counts over the same
#   waking time must give the same rate; halving moves a little waking time to
#   quiet, so the 30 s rate can only be a little higher. Tolerance: 10 %.

async def _at_two_bin_widths(monitor_files, tmp_path):
    src = Path(monitor_files[0])
    half = split_to_30s(src, tmp_path / "30s" / src.name)
    groups = _one_monitor_groups(src.name)
    async with MCPHarness(tmp_path / "s") as h:
        return (await _metrics(h, [src], groups), await _metrics(h, [half], groups))


@pytest.mark.asyncio
async def test_total_activity_is_the_same_at_30s_and_60s(monitor_files, tmp_path):
    m60, m30 = await _at_two_bin_widths(monitor_files, tmp_path)
    a = _rows_by_key(m60["compute_activity"]["total_activity_by_phase"])
    b = _rows_by_key(m30["compute_activity"]["total_activity_by_phase"])
    assert set(a) == set(b)
    for k in a:
        assert_same(a[k]["mean"], b[k]["mean"], f"total_activity {k}")


@pytest.mark.asyncio
async def test_total_sleep_agrees_at_30s_and_60s_within_a_bin_per_bout(
        monitor_files, tmp_path):
    m60, m30 = await _at_two_bin_widths(monitor_files, tmp_path)
    tst60 = _rows_by_key(m60["compute_sleep"]["total_sleep_time_h"])
    tst30 = _rows_by_key(m30["compute_sleep"]["total_sleep_time_h"])
    bouts = _rows_by_key(m60["compute_sleep"]["sleep_bout_count_by_phase"])
    assert set(tst60) == set(tst30)
    for (label, x), r60 in tst60.items():
        # Whole-run bouts in both phases: total sleep is per day, so this
        # over-counts the bouts behind it — a looser bound, never a tighter one.
        n_bouts = sum(r["mean"] for (lab, _), r in bouts.items() if lab == label)
        bound_h = (n_bouts + 1) * 90 / 3600
        diff = tst30[(label, x)]["mean"] - r60["mean"]
        assert abs(diff) <= bound_h, (
            f"{label}: total sleep {r60['mean']:.4f} h at 60 s, "
            f"{tst30[(label, x)]['mean']:.4f} h at 30 s (bound {bound_h:.4f} h)")


@pytest.mark.asyncio
async def test_counts_per_waking_minute_agree_at_30s_and_60s(monitor_files, tmp_path):
    m60, m30 = await _at_two_bin_widths(monitor_files, tmp_path)
    a = _rows_by_key(m60["compute_activity"]["counts_per_waking_minute_by_phase"])
    b = _rows_by_key(m30["compute_activity"]["counts_per_waking_minute_by_phase"])
    assert set(a) == set(b)
    for k in a:
        assert math.isclose(a[k]["mean"], b[k]["mean"], rel_tol=0.10), (
            f"counts per waking minute {k}: {a[k]['mean']:.3f} at 60 s, "
            f"{b[k]['mean']:.3f} at 30 s")
