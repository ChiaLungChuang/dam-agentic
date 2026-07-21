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


def test_window_tradeoff_curve_is_non_increasing(monitor_files):
    res = engine.window_tradeoff(monitor_files, death_hours=24.0)
    rows = res["rows"]
    assert len(rows) == 6
    alive = [r["n_alive"] for r in rows]
    assert alive == sorted(alive, reverse=True)     # more deaths accrue as it extends
    assert rows[0]["n_alive"] >= rows[-1]["n_alive"]
    assert all(r["n_died"] >= 0 and r["n_empty"] >= 0 for r in rows)
