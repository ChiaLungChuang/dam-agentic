"""Tool-level behaviour: the guards, refusals, and warnings that make the design
claims real at the point a client calls a tool.

Requires the mcp package (the server module imports FastMCP). The @mcp.tool
decorator returns the original function unchanged, so the tools are called here as
plain functions with server.STORE pointed at a throwaway state dir.
"""

import pytest

pytest.importorskip("mcp")

from dam_mcp import server
from dam_mcp.errors import ToolError
from dam_mcp.sessions import SessionStore


@pytest.fixture
def srv(tmp_path):
    """Point the server's global store at an isolated state dir for each test."""
    server.STORE = SessionStore(state_dir=tmp_path / "state")
    return server


# ── #6/#7: malformed input is an actionable refusal, not a traceback ──────────

def test_unknown_session_raises_actionable(srv):
    with pytest.raises(ToolError) as exc:
        srv.describe_experiment("dam-nope")
    assert "No session" in str(exc.value)


def test_malformed_exclusion_is_actionable(srv, monitor_files):
    loaded = srv.load_experiment(monitor_files, "x")
    sid = loaded["session_id"]
    with pytest.raises(ToolError) as exc:
        srv.apply_exclusions(sid, ["Monitor1.txt:xyz"], reason="bad channel")
    msg = str(exc.value).lower()
    assert "whole-number" in msg or "channel" in msg
    assert "invalid literal" not in msg      # never a raw ValueError


def test_exclusion_requires_reason(srv, monitor_files):
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    with pytest.raises(ToolError):
        srv.apply_exclusions(sid, ["Monitor1.txt:5"], reason="")


# ── #9: config cross-check refuses labels the contrasts don't reference ───────

def test_assign_groups_refuses_undeclared_labels(srv, monitor_files):
    pytest.importorskip("yaml")
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    with pytest.raises(ToolError) as exc:
        srv.assign_groups(sid, {"foo": {"Monitor1.txt": [1, 16]},
                                "bar": {"Monitor1.txt": [17, 32]}})
    assert "declared contrasts" in str(exc.value)


# ── #13: confound warning fires only when groups split by monitor ─────────────

def test_within_monitor_split_has_no_confound(srv, monitor_files):
    pytest.importorskip("yaml")
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    res = srv.assign_groups(sid, {"CG8093_mut": {"Monitor1.txt": [1, 16]},
                                  "w1118_ctrl": {"Monitor1.txt": [17, 32]}})
    assert res["warnings"] == []
    assert res["group_sizes"] == {"CG8093_mut": 16, "w1118_ctrl": 16}


def test_cross_monitor_split_warns_confound(srv, monitor_files):
    pytest.importorskip("yaml")
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    res = srv.assign_groups(sid, {"CG8093_mut": {"Monitor1.txt": [1, 32]},
                                  "w1118_ctrl": {"Monitor2.txt": [1, 32]}})
    assert any("confounded with monitor" in w for w in res["warnings"])


# ── flow: load -> qc surfaces decisions, never repairs ────────────────────────

def test_load_then_qc(srv, monitor_files):
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    qc = srv.run_qc(sid, death_hours=24.0)
    assert isinstance(qc["decisions_required"], list)
    assert set(qc["tally"])            # tally keyed by monitor
    assert qc["report_uri"].endswith("qc-report")


def test_compute_before_qc_is_refused(srv, monitor_files):
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    # no QC yet -> guard refuses (metrics before QC aren't trustworthy)
    with pytest.raises(ToolError):
        srv.compute_sleep(sid)
