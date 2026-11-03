"""Invariance tests: run the system twice and compare, with no ground truth.

Most of this suite proves the server refuses what it should and that every path
runs. Few tests can say a NUMBER is right, because that needs a known answer, and
for a real metric there is none to hand. These tests sidestep that: each runs
the system on two inputs that differ by a transformation whose effect on the
answer is known in advance — usually "none" — and asserts the two outputs relate
exactly that way. A wiring bug (a channel read from the wrong column, a label
attached to the wrong arm, an exclusion applied to the wrong row) breaks the
relation even when every individual output looks plausible.

Each relation is chosen so that a real implementation must satisfy it whatever
the data are, and is stated with the reason. All of them drive the real server
over stdio. The metric-level ones need the engine and use four tubes per group to
keep each store build to ~10 s (see test_happy_path).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from conftest import requires_rtivity
from evals.harness import MCPHarness

MUT, CTRL = "CG8093_mut", "w1118_ctrl"
CONTRAST = "mut_vs_ctrl_sleep_night"


# ── building transformed inputs ───────────────────────────────────────────────

def _rows(path: Path) -> list[list[str]]:
    return [ln.split("\t") for ln in path.read_text().splitlines() if ln.count("\t") >= 41]


def _write(path: Path, rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join("\t".join(r) + "\n" for r in rows))
    return path


def reversed_channels(src: Path, dest: Path) -> Path:
    """`src` with channel k moved to channel 33 - k — a pure relabelling of tubes."""
    return _write(dest, [r[:10] + r[10:42][::-1] + r[42:] for r in _rows(src)])


def _mirror(ch: int) -> int:
    return 33 - ch


# ── comparing outputs ─────────────────────────────────────────────────────────

def assert_same(a, b, path="result"):
    """Structural equality with a float tolerance: the same numbers reached by
    a different summation order may differ in the last bits, and that is not a
    violation of any relation here."""
    if isinstance(a, float) or isinstance(b, float):
        assert isinstance(a, (int, float)) and isinstance(b, (int, float)), path
        assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12), f"{path}: {a} != {b}"
    elif isinstance(a, dict):
        assert set(a) == set(b), f"{path}: keys {sorted(set(a) ^ set(b))}"
        for k in a:
            assert_same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            assert_same(x, y, f"{path}[{i}]")
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


async def _session(h, files) -> str:
    r = await h.call("load_experiment", paths=[str(f) for f in files], name="inv")
    assert not r.is_error, r.text
    return r.data["session_id"]


async def _qc(h, sid) -> dict:
    r = await h.call("run_qc", session_id=sid)
    assert not r.is_error, r.text
    return r.data


def _qc_body(qc: dict) -> dict:
    """A QC result without the per-session parts (id, report URI)."""
    return {k: v for k, v in qc.items() if k not in ("session_id", "report_uri")}


async def _sleep(h, files, mapping, exclude=()) -> dict:
    sid = await _session(h, files)
    await _qc(h, sid)
    r = await h.call("assign_groups", session_id=sid, mapping=mapping)
    assert not r.is_error, r.text
    if exclude:
        r = await h.call("apply_exclusions", session_id=sid, exclusions=list(exclude),
                         reason="invariance", confirm=True)
        assert not r.is_error, r.text
    r = await h.call("compute_sleep", session_id=sid)
    assert not r.is_error, r.text
    return r.data["summary"]


# ── QC-level relations (no engine) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_relabelling_tubes_relabels_the_qc_calls_and_nothing_else(
        monitor_files, tmp_path):
    """QC judges each channel on its own counts. Moving every channel to another
    index must move its call with it — same state, same evidence — and leave the
    per-monitor tally untouched."""
    src = Path(monitor_files[0])
    perm = reversed_channels(src, tmp_path / "perm" / src.name)
    async with MCPHarness(tmp_path / "s") as h:
        a = await _qc(h, await _session(h, [src]))
        b = await _qc(h, await _session(h, [perm]))
    assert a["tally"] == b["tally"]
    calls_a = {(f["channel"], f["state"], f["evidence"]) for f in a["flags"]}
    calls_b = {(_mirror(f["channel"]), f["state"], f["evidence"]) for f in b["flags"]}
    assert calls_a and calls_a == calls_b


@pytest.mark.asyncio
async def test_qc_is_deterministic(monitor_files, tmp_path):
    """The same files, QC'd twice, give the same answer — the precondition for
    every other comparison in this file meaning anything."""
    async with MCPHarness(tmp_path / "s") as h:
        sid = await _session(h, monitor_files)
        first, second = await _qc(h, sid), await _qc(h, sid)
    assert_same(_qc_body(first), _qc_body(second))


@pytest.mark.asyncio
async def test_a_window_spanning_the_whole_run_changes_no_qc_call(monitor_files,
                                                                 tmp_path):
    """A window whose edges are the data's own common start and end selects every
    read, so it must reproduce the unwindowed QC exactly. An off-by-one at either
    edge — one read dropped — shows up here as a changed call."""
    async with MCPHarness(tmp_path / "s") as h:
        r = await h.call("load_experiment", paths=monitor_files, name="inv")
        sid, (start, end) = r.data["session_id"], r.data["time_window"]
        plain = await _qc(h, sid)
        windowed = await h.call("set_analysis_window", session_id=sid,
                                start=start, end=end)
    assert not windowed.is_error, windowed.text
    assert windowed.data["tally"] == plain["tally"]
    assert windowed.data["decisions_required"] == plain["decisions_required"]


# ── metric-level relations (need Rtivity-Python) ──────────────────────────────

@requires_rtivity
@pytest.mark.asyncio
async def test_relabelling_tubes_leaves_every_group_metric_unchanged(monitor_files,
                                                                    tmp_path):
    """The same flies under different channel numbers are the same groups. Every
    sleep summary must match to rounding."""
    src = Path(monitor_files[0])
    perm = reversed_channels(src, tmp_path / "perm" / src.name)
    mapping = {MUT: {src.name: [1, 4]}, CTRL: {src.name: [17, 20]}}
    mirrored = {MUT: {src.name: sorted(_mirror(c) for c in range(1, 5))},
                CTRL: {src.name: sorted(_mirror(c) for c in range(17, 21))}}
    async with MCPHarness(tmp_path / "s") as h:
        a = await _sleep(h, [src], mapping)
        b = await _sleep(h, [perm], mirrored)
    assert_same(a, b)


@requires_rtivity
@pytest.mark.asyncio
async def test_swapping_the_group_labels_mirrors_the_contrast(monitor_files, tmp_path):
    """Give each label the other's flies. The declared order of the contrast does
    not change, so the two-sided p must not either; n and medians swap; the
    Mann-Whitney U becomes n1*n2 - U, and the rank-biserial effect changes sign.
    A label attached to the wrong arm anywhere in the pipeline breaks this."""
    src = Path(monitor_files[0])
    fwd = {MUT: {src.name: [1, 4]}, CTRL: {src.name: [17, 20]}}
    rev = {MUT: {src.name: [17, 20]}, CTRL: {src.name: [1, 4]}}
    results = []
    async with MCPHarness(tmp_path / "s") as h:
        for mapping in (fwd, rev):
            sid = await _session(h, [src])
            await _qc(h, sid)
            await h.call("assign_groups", session_id=sid, mapping=mapping)
            r = await h.call("run_contrast", session_id=sid, contrast_id=CONTRAST)
            assert not r.is_error, r.text
            results.append(r.data)
    a, b = results
    assert a["test"] == b["test"] == "mann_whitney_u"
    assert math.isclose(a["p_value"], b["p_value"], rel_tol=1e-12)
    assert (a["n"][MUT], a["n"][CTRL]) == (b["n"][CTRL], b["n"][MUT])
    assert (a["median"][MUT], a["median"][CTRL]) == (b["median"][CTRL], b["median"][MUT])
    n1n2 = a["n"][MUT] * a["n"][CTRL]
    assert math.isclose(a["statistic"] + b["statistic"], n1n2)
    assert math.isclose(a["effect_size"]["value"], -b["effect_size"]["value"],
                        abs_tol=1e-12)


@requires_rtivity
@pytest.mark.asyncio
async def test_excluding_a_tube_equals_never_assigning_it(monitor_files, tmp_path):
    """An exclusion removes an animal from the analysis — so assigning tube 4 and
    excluding it must give exactly what never assigning it gives. An exclusion
    keyed to the wrong row, or applied after the metric, breaks this."""
    src = Path(monitor_files[0])
    async with MCPHarness(tmp_path / "s") as h:
        excluded = await _sleep(h, [src], {MUT: {src.name: [1, 4]},
                                           CTRL: {src.name: [17, 20]}},
                                exclude=[f"{src.name}:4"])
        omitted = await _sleep(h, [src], {MUT: {src.name: [1, 3]},
                                          CTRL: {src.name: [17, 20]}})
    assert_same(excluded, omitted)


@requires_rtivity
@pytest.mark.asyncio
async def test_duplicating_the_data_doubles_n_and_keeps_every_mean(monitor_files,
                                                                  tmp_path):
    """Two identical copies of the same flies, assigned identically: every group
    has twice the animals and exactly the same mean and median. A copy read at a
    different offset, or an n counted once, breaks this."""
    src = Path(monitor_files[0])
    twin = _write(tmp_path / "twin" / "Monitor9.txt", _rows(src))
    one = {MUT: {src.name: [1, 4]}, CTRL: {src.name: [17, 20]}}
    two = {MUT: {src.name: [1, 4], twin.name: [1, 4]},
           CTRL: {src.name: [17, 20], twin.name: [17, 20]}}
    async with MCPHarness(tmp_path / "s") as h:
        a = await _sleep(h, [src], one)
        b = await _sleep(h, [src, twin], two)
    for table, rows_a in a.items():
        if not isinstance(rows_a, list):
            continue
        rows_b = {(r["labels"], r.get("x")): r for r in b[table]}
        for r in rows_a:
            twin_row = rows_b[(r["labels"], r.get("x"))]
            assert twin_row["n"] == 2 * r["n"], f"{table} {r['labels']} {r.get('x')}"
            for stat in ("mean", "median"):
                assert_same(r[stat], twin_row[stat], f"{table}.{r['labels']}.{stat}")
