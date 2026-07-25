"""Window tools (#12) — the plumbing, without needing the analysis engine.

build_conditions must clamp each file's range to the analysis window so metrics are
computed over exactly the window QC ran on, and window_tradeoff must produce a
sane n-alive curve. Both are checked here on the synthetic corpus; the end-to-end
protocol behaviour is in test_contract.py.
"""

from dam_mcp import engine
from dam_mcp.sessions import Session


def _session(monitor_files):
    monitors, _ = engine.scan_structural(monitor_files)
    s = Session(session_id="x", name="n", created_at="t",
                paths=monitor_files, monitors=monitors)
    s.groups = [{"monitor": monitors[0]["file"], "channel": 1,
                 "labels": "A", "order": 1}]
    return s, monitors


def test_build_conditions_uses_full_range_without_window(monitor_files):
    s, monitors = _session(monitor_files)
    df = engine.build_conditions(s)
    assert df.iloc[0]["start_datetime"] == monitors[0]["first_ts"]
    assert df.iloc[0]["stop_datetime"] == monitors[0]["last_ts"]


def test_build_conditions_clamps_to_window(monitor_files):
    s, monitors = _session(monitor_files)
    s.window = {"start": "2026-03-02T12:00:00", "end": "2026-03-03T00:00:00"}
    df = engine.build_conditions(s)
    # start clamps up to the window (data begins ~09:0x, window starts 12:00)
    assert df.iloc[0]["start_datetime"] == "2026-03-02T12:00:00"
    assert df.iloc[0]["stop_datetime"] == "2026-03-03T00:00:00"


def test_window_dropping_a_monitor_is_reported_not_silent(monitor_files, tmp_path):
    """D.1 — the most serious of the contract defects, because of *position*: the
    window is set before the window-before-exclusions rail, so a silently truncated
    dataset flows into grouping, exclusions, metrics and contrasts while the tally
    looks clean. Where monitors map to genotypes, a whole arm can vanish and still
    produce a tidy result. Dropping is legitimate; dropping silently is not.
    """
    import asyncio

    from evals.harness import MCPHarness

    async def run():
        async with MCPHarness(tmp_path) as h:
            data = (await h.call("load_experiment", paths=monitor_files,
                                 name="drop")).data
            sid = data["session_id"]
            ends = sorted(m["last_ts"] for m in data["monitors"])
            # A window inside the gap between the two monitors' end times keeps the
            # longer-running monitor and drops the other entirely.
            r = await h.call("set_analysis_window", session_id=sid,
                             start=ends[0], end=ends[1])
            return r

    r = asyncio.run(run())
    assert not r.is_error                      # dropping is legitimate...
    assert r.data["monitors_dropped"] == ["Monitor2.txt"]   # ...but it is reported
    assert "Monitor2.txt" not in r.data["tally"]            # and it is really gone
    assert "Monitor2.txt" in r.data["message"]              # visible without digging


def test_window_keeping_everything_reports_no_drops(monitor_files, tmp_path):
    import asyncio

    from evals.harness import MCPHarness

    async def run():
        async with MCPHarness(tmp_path) as h:
            sid = (await h.call("load_experiment", paths=monitor_files,
                                name="nodrop")).data["session_id"]
            return await h.call("set_analysis_window", session_id=sid,
                                start="2026-03-02T09:05:00")

    r = asyncio.run(run())
    assert not r.is_error
    assert r.data["monitors_dropped"] == []
    assert len(r.data["tally"]) == 2


def test_window_tradeoff_curve_is_non_increasing(monitor_files):
    res = engine.window_tradeoff(monitor_files, death_hours=24.0)
    rows = res["rows"]
    assert len(rows) == 6
    alive = [r["n_alive"] for r in rows]
    assert alive == sorted(alive, reverse=True)     # more deaths accrue as it extends
    assert rows[0]["n_alive"] >= rows[-1]["n_alive"]
    assert all(r["n_died"] >= 0 and r["n_empty"] >= 0 for r in rows)
