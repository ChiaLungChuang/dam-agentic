"""Tool-level behaviour: the guards, refusals, and warnings that make the design
claims real at the point a client calls a tool.

Requires the mcp package (the server module imports FastMCP). The @mcp.tool
decorator returns the original function unchanged, so the tools are called here as
plain functions with server.STORE pointed at a throwaway state dir.
"""

from pathlib import Path

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


# ── #9: config cross-check refuses labels groups: does not declare ────────────

def test_assign_groups_refuses_undeclared_labels(srv, monitor_files):
    """The check moved from the contrast set onto groups: (HANDOFF-9 reversal), so
    it now names the offending label rather than the contrasts. Assert on the
    labels, not on prose: a message-only assertion would pass on a revert to any
    other refusal."""
    pytest.importorskip("yaml")
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    with pytest.raises(ToolError) as exc:
        srv.assign_groups(sid, {"foo": {"Monitor1.txt": [1, 16]},
                                "bar": {"Monitor1.txt": [17, 32]}})
    msg = str(exc.value)
    assert "'bar'" in msg and "'foo'" in msg          # the undeclared labels
    assert "CG8093_mut" in msg                        # what is declared instead


def test_assign_groups_accepts_the_declared_labels(srv, monitor_files):
    """Negative control for the test above: the same call with declared labels
    must succeed, or the refusal test would pass against a tool that refuses
    everything."""
    pytest.importorskip("yaml")
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    res = srv.assign_groups(sid, {"CG8093_mut": {"Monitor1.txt": [1, 16]},
                                  "w1118_ctrl": {"Monitor1.txt": [17, 32]}})
    assert res["group_sizes"] == {"CG8093_mut": 16, "w1118_ctrl": 16}


def test_a_groups_only_declaration_permits_the_pipeline(srv, monitor_files,
                                                        tmp_path, monkeypatch):
    """The headline claim of the reversal, exercised rather than asserted.

    A declaration with groups: and no contrasts must carry load -> window -> group
    all the way. The compute_* step needs the analysis engine, so it is covered by
    the engine-gated tests; everything up to it runs here."""
    pytest.importorskip("yaml")
    decl = tmp_path / "contrasts-designonly.yaml"
    decl.write_text("experiment: designonly\ngroups: [mut, ctrl]\n")
    monkeypatch.setenv("DAM_PREREG_PATH", str(decl))

    loaded = srv.load_experiment(monitor_files, "design-only")
    sid = loaded["session_id"]
    srv.run_qc(sid)
    srv.set_analysis_window(sid, start=loaded["time_window"][0])
    res = srv.assign_groups(sid, {"mut": {"Monitor1.txt": [1, 16]},
                                  "ctrl": {"Monitor1.txt": [17, 32]}})
    assert res["group_sizes"] == {"mut": 16, "ctrl": 16}
    listed = srv.list_contrasts(sid)
    assert listed["contrasts"] == []                       # empty, not an error
    # An empty contrast list is the NORMAL response now, so the reply has to say
    # what the experiment *does* declare, or it answers a question nobody asked.
    assert sorted(listed["groups"]) == ["ctrl", "mut"]


def test_list_contrasts_reports_the_declared_groups(srv, monitor_files):
    pytest.importorskip("yaml")
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    listed = srv.list_contrasts(sid)
    assert sorted(listed["groups"]) == ["CG8093_mut", "w1118_ctrl"]
    assert listed["config_path"].endswith("contrasts-testfixture.yaml")


def test_list_contrasts_surfaces_declaration_warnings(srv, monitor_files,
                                                      tmp_path, monkeypatch):
    """The mapping-values warning has to reach a caller, not just exist."""
    pytest.importorskip("yaml")
    decl = tmp_path / "contrasts-mapped.yaml"
    decl.write_text("experiment: mapped\ngroups:\n"
                    "  mut:\n    Monitor1.txt: [1, 16]\n"
                    "  ctrl:\n    Monitor1.txt: [17, 32]\n")
    monkeypatch.setenv("DAM_PREREG_PATH", str(decl))
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    joined = " ".join(srv.list_contrasts(sid)["warnings"])
    assert "NOT read" in joined and "assign_groups" in joined


def test_assign_groups_surfaces_declaration_warnings(srv, monitor_files,
                                                     tmp_path, monkeypatch):
    """assign_groups is where someone acts on a channel range, so it is the one
    place the 'those values did nothing' warning most needs to appear."""
    pytest.importorskip("yaml")
    decl = tmp_path / "contrasts-mapped.yaml"
    decl.write_text("experiment: mapped\ngroups:\n"
                    "  mut:\n    Monitor1.txt: [1, 16]\n"
                    "  ctrl:\n    Monitor1.txt: [17, 32]\n")
    monkeypatch.setenv("DAM_PREREG_PATH", str(decl))
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    res = srv.assign_groups(sid, {"mut": {"Monitor1.txt": [1, 16]},
                                  "ctrl": {"Monitor1.txt": [17, 32]}})
    joined = " ".join(res["warnings"])
    assert "NOT read" in joined


def test_unassigned_declared_group_warns_but_does_not_refuse(srv, monitor_files,
                                                             tmp_path, monkeypatch):
    """Subset, not equality: a partial load is legitimate. Flag it, do not block
    it — and do not silently accept it either."""
    pytest.importorskip("yaml")
    decl = tmp_path / "contrasts-four.yaml"
    decl.write_text("experiment: four\ngroups: [mut, ctrl, extra_a, extra_b]\n")
    monkeypatch.setenv("DAM_PREREG_PATH", str(decl))

    sid = srv.load_experiment(monitor_files, "partial")["session_id"]
    res = srv.assign_groups(sid, {"mut": {"Monitor1.txt": [1, 16]},
                                  "ctrl": {"Monitor1.txt": [17, 32]}})
    assert res["group_sizes"] == {"mut": 16, "ctrl": 16}
    joined = " ".join(res["warnings"])
    assert "extra_a" in joined and "extra_b" in joined


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


# ── declared-n checksum (HANDOFF-12) ─────────────────────────────────────────
#
# The declaration says how many independent animals a group has; the mapping says
# which channels carry them. Two statements of one number, never compared until
# now — which is how 96 channels were accepted as 96 animals when they were 32
# seen three times. This is a checksum, not a model of the apparatus.

FIXTURE_N = Path(__file__).resolve().parent / "fixtures" / "contrasts-nmismatch.yaml"


def _pin(monkeypatch, path):
    monkeypatch.setenv("DAM_PREREG_PATH", str(path))


def test_declared_n_mismatch_refuses_and_names_all_three_numbers(
        srv, monitor_files, monkeypatch):
    """nctrl declares 99; [17, 32] assigns 16. The refusal has to carry the group,
    the declared n and the computed n — a bare 'mismatch' leaves the caller to
    guess which side moved."""
    pytest.importorskip("yaml")
    _pin(monkeypatch, FIXTURE_N)
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    with pytest.raises(ToolError) as exc:
        srv.assign_groups(sid, {"nmut": {"Monitor1.txt": [1, 16]},
                                "nctrl": {"Monitor1.txt": [17, 32]}})
    msg = str(exc.value)
    assert "nctrl" in msg and "99" in msg and "16" in msg
    assert "Nothing was assigned" in msg


def test_a_refused_mismatch_does_not_persist_the_assignment(
        srv, monitor_files, monkeypatch):
    """The refusal is before the save. Otherwise the next call reads groups the
    checksum already rejected, and the refusal is advisory."""
    pytest.importorskip("yaml")
    _pin(monkeypatch, FIXTURE_N)
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    with pytest.raises(ToolError):
        srv.assign_groups(sid, {"nmut": {"Monitor1.txt": [1, 16]},
                                "nctrl": {"Monitor1.txt": [17, 32]}})
    assert srv.STORE.get(sid).groups == []


def test_declared_n_match_proceeds(srv, monitor_files, tmp_path, monkeypatch):
    """The positive control. Without it, a checksum that refused everything would
    pass the negative tests above."""
    pytest.importorskip("yaml")
    decl = tmp_path / "contrasts-nok.yaml"
    decl.write_text("experiment: nok\ngroups:\n  nmut: 16\n  nctrl: 16\n")
    _pin(monkeypatch, decl)
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    res = srv.assign_groups(sid, {"nmut": {"Monitor1.txt": [1, 16]},
                                  "nctrl": {"Monitor1.txt": [17, 32]}})
    assert res["group_sizes"] == {"nmut": 16, "nctrl": 16}
    assert not any("Declared-n" in w for w in res["warnings"])


def test_no_declared_n_proceeds_unchanged(srv, monitor_files):
    """The list form declares no n. Every declaration written before this check
    looks like this, and none of them may start failing."""
    pytest.importorskip("yaml")
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    res = srv.assign_groups(sid, {"CG8093_mut": {"Monitor1.txt": [1, 16]},
                                  "w1118_ctrl": {"Monitor1.txt": [17, 32]}})
    assert res["group_sizes"] == {"CG8093_mut": 16, "w1118_ctrl": 16}
    assert not any("Declared-n" in w for w in res["warnings"])


def test_a_declared_group_with_nothing_assigned_is_not_a_mismatch(
        srv, monitor_files, monkeypatch):
    """A partial load is legitimate and already warned about. Counting it as a
    mismatch would refuse the ordinary case of loading one arm at a time."""
    pytest.importorskip("yaml")
    _pin(monkeypatch, FIXTURE_N)
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    res = srv.assign_groups(sid, {"nmut": {"Monitor1.txt": [1, 16]}})
    assert res["group_sizes"] == {"nmut": 16}
    joined = " ".join(res["warnings"])
    assert "nctrl" in joined and "not assigned any channels" in joined


def test_override_requires_both_reason_and_confirm(srv, monitor_files,
                                                    monkeypatch):
    """A reason with no confirmation is not a decision; a confirmation with no
    reason is not a record. Each half alone is refused, and the refusal says
    which half is missing."""
    pytest.importorskip("yaml")
    _pin(monkeypatch, FIXTURE_N)
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    mapping = {"nmut": {"Monitor1.txt": [1, 16]},
               "nctrl": {"Monitor1.txt": [17, 32]}}

    with pytest.raises(ToolError) as reason_only:
        srv.assign_groups(sid, mapping, n_override_reason="83 tubes not loaded")
    assert "confirm_n_override is not true" in str(reason_only.value)

    with pytest.raises(ToolError) as confirm_only:
        srv.assign_groups(sid, mapping, confirm_n_override=True)
    assert "n_override_reason is empty" in str(confirm_only.value)

    with pytest.raises(ToolError) as blank_reason:
        srv.assign_groups(sid, mapping, n_override_reason="   ",
                          confirm_n_override=True)
    assert "n_override_reason is empty" in str(blank_reason.value)

    assert srv.STORE.get(sid).groups == []      # none of the three assigned


def test_override_with_both_proceeds_and_surfaces_the_reason(
        srv, monitor_files, monkeypatch):
    """Overridable, never silently. The reason reaches the caller in the result,
    because an override nobody can see is the same as no check."""
    pytest.importorskip("yaml")
    _pin(monkeypatch, FIXTURE_N)
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    res = srv.assign_groups(
        sid,
        {"nmut": {"Monitor1.txt": [1, 16]},
         "nctrl": {"Monitor1.txt": [17, 32]}},
        n_override_reason="83 tubes were never loaded in this run",
        confirm_n_override=True,
    )
    assert res["group_sizes"] == {"nmut": 16, "nctrl": 16}
    (note,) = [w for w in res["warnings"] if "Declared-n" in w]
    assert "ACCEPTED by explicit override" in note
    assert "83 tubes were never loaded" in note
    assert "99" in note and "16" in note


# ── H13-1: an accepted override has to reach the record, not just the caller ──

def _override(srv, sid):
    return srv.assign_groups(
        sid,
        {"nmut": {"Monitor1.txt": [1, 16]}, "nctrl": {"Monitor1.txt": [17, 32]}},
        n_override_reason="83 tubes were never loaded in this run",
        confirm_n_override=True,
    )


def test_an_accepted_override_is_persisted_to_the_session(srv, monitor_files,
                                                          monkeypatch):
    """The warnings list reaches the caller of assign_groups and nothing else.
    The session is what the report and the audit stream read."""
    pytest.importorskip("yaml")
    _pin(monkeypatch, FIXTURE_N)
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    _override(srv, sid)
    (rec,) = srv.STORE.get(sid).n_overrides
    assert rec["group"] == "nctrl"
    assert rec["declared_n"] == 99 and rec["computed_n"] == 16
    assert rec["confirmed"] is True
    assert "83 tubes" in rec["reason"] and rec["at"]


def test_the_override_survives_a_store_restart(srv, monitor_files, monkeypatch,
                                               tmp_path):
    """It is on disk, not only in the in-memory cache. A record that dies with the
    process is not a record."""
    pytest.importorskip("yaml")
    _pin(monkeypatch, FIXTURE_N)
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    _override(srv, sid)
    fresh = SessionStore(state_dir=srv.STORE.state_dir)      # cold, reads from disk
    assert fresh.get(sid).n_overrides[0]["computed_n"] == 16


def test_the_override_is_in_the_tool_return_as_structure_not_only_prose(
        srv, monitor_files, monkeypatch):
    pytest.importorskip("yaml")
    _pin(monkeypatch, FIXTURE_N)
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    res = _override(srv, sid)
    (rec,) = res["n_overrides"]
    assert rec["group"] == "nctrl" and rec["declared_n"] == 99
    # the prose form is kept as well — a caller reading only warnings loses nothing
    assert any("ACCEPTED by explicit override" in w for w in res["warnings"])


def test_reassigning_without_an_override_clears_the_previous_one(
        srv, monitor_files, monkeypatch, tmp_path):
    """Replaced, not appended. A stale justification attached to an assignment
    that no longer needs one is a false record, which is worse than none."""
    pytest.importorskip("yaml")
    _pin(monkeypatch, FIXTURE_N)
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    _override(srv, sid)
    assert srv.STORE.get(sid).n_overrides                     # in force
    srv.assign_groups(sid, {"nmut": {"Monitor1.txt": [1, 16]}})   # no mismatch now
    assert srv.STORE.get(sid).n_overrides == []


def test_a_clean_assignment_records_no_override(srv, monitor_files):
    pytest.importorskip("yaml")
    sid = srv.load_experiment(monitor_files, "x")["session_id"]
    res = srv.assign_groups(sid, {"CG8093_mut": {"Monitor1.txt": [1, 16]},
                                  "w1118_ctrl": {"Monitor1.txt": [17, 32]}})
    assert res["n_overrides"] == []
    assert srv.STORE.get(sid).n_overrides == []
