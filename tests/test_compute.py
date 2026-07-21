"""The compute layer and the boundary guarantee.

These require the Rtivity-Python engine. The load-bearing assertion is not that
the numbers are right (the 229 engine tests cover that) but that only *summaries*
cross back — a fixed, small number of aggregate rows, never a per-timepoint series.
If a future change returned a dataframe "for convenience", the boundary test fails.
"""

import pytest

from dam_mcp import engine
from dam_mcp.sessions import SessionStore

from conftest import requires_rtivity

pytestmark = requires_rtivity


def _ready_session(tmp_path, monitor_files):
    """A session loaded, grouped (1-16 A / 17-32 B per monitor), and QC'd."""
    store = SessionStore(state_dir=tmp_path)
    session = store.create(name="exp", paths=monitor_files)
    session.monitors, session.warnings = engine.scan_structural(monitor_files)
    session.groups = [
        {"monitor": m["file"], "channel": ch,
         "labels": "GroupA" if ch <= 16 else "GroupB",
         "order": 1 if ch <= 16 else 2}
        for m in session.monitors for ch in range(1, 33)
    ]
    store.save(session)
    return store, session


def _numeric_leaves(obj) -> int:
    if isinstance(obj, dict):
        return sum(_numeric_leaves(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_numeric_leaves(v) for v in obj)
    return 1 if isinstance(obj, (int, float)) else 0


def test_compute_sleep_returns_only_summaries(tmp_path, monitor_files):
    store, session = _ready_session(tmp_path, monitor_files)
    ds = engine.build_store(session, store)
    out = engine.compute_sleep(ds)

    rows = out["total_sleep_time_h"]
    assert {r["labels"] for r in rows} == {"GroupA", "GroupB"}
    for r in rows:
        assert set(r) <= {"labels", "x", "n", "mean", "sd", "median", "sem", "q1", "q3"}
    # Boundary: two groups over a handful of metrics is dozens of numbers, not the
    # ~276k activity samples behind them. Bound it well below any raw-series size.
    assert _numeric_leaves(out) < 500


def test_compute_activity_and_survival(tmp_path, monitor_files):
    store, session = _ready_session(tmp_path, monitor_files)
    ds = engine.build_store(session, store)

    act = engine.compute_activity(ds)
    assert "total_activity_by_phase" in act
    assert _numeric_leaves(act) < 500

    surv = engine.compute_survival(ds, death_hours=24.0)
    assert "summary" in surv and "logrank_pairwise" in surv
    assert isinstance(surv["decisions_required"], list)


def test_run_contrast_math(tmp_path, monitor_files):
    store, session = _ready_session(tmp_path, monitor_files)
    ds = engine.build_store(session, store)
    contrast = {
        "id": "a_vs_b_sleep_night", "metric": "total_sleep", "phase": "dark",
        "groups": ["GroupA", "GroupB"], "test": "wilcoxon",
    }
    res = engine.run_contrast(ds, contrast, n_exclusions=0)
    assert res["n"]["GroupA"] > 0 and res["n"]["GroupB"] > 0
    assert 0.0 <= res["p_value"] <= 1.0
    assert res["effect_size"]["kind"] == "rank_biserial"
    assert res["test"] == "mann_whitney_u"


def test_contrast_unknown_metric_is_actionable(tmp_path, monitor_files):
    store, session = _ready_session(tmp_path, monitor_files)
    ds = engine.build_store(session, store)
    with pytest.raises(engine.ToolError):
        engine.run_contrast(
            ds, {"id": "x", "metric": "made_up", "phase": "dark",
                 "groups": ["GroupA", "GroupB"], "test": "wilcoxon"}, 0)


def test_exclusions_shrink_n(tmp_path, monitor_files):
    store, session = _ready_session(tmp_path, monitor_files)
    session.exclusions = [
        {"monitor": session.monitors[0]["file"], "channel": 1,
         "reason": "test", "at": "t"}
    ]
    store.save(session)
    ds = engine.build_store(session, store)
    # channel 1 (GroupA) removed -> GroupA has one fewer animal than GroupB
    labels = ds.metadata["labels"].value_counts().to_dict()
    assert labels["GroupA"] == labels["GroupB"] - 1
