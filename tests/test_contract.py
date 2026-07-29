"""Layer 1 — protocol contract tests (the P1 acceptance suite).

Each test below is a finding from the manual Inspector session, written down as an
assertion that drives the *running server* over stdio. Thirty unit tests saw none
of the P1 bugs; operating the server found them in minutes. This file is that
session made re-runnable, so a future change that reopens a rail turns it red.

The scenarios map 1:1 to the table in docs/../phase-3.5-eval-harness.md:

    compute before assign_groups     -> refuses, names the fix
    apply_exclusions confirm=false   -> applied:false, previews Δn
    run_contrast undeclared id       -> refuses, enumerates legal ids
    malformed exclusion              -> handled error, no traceback
    any read-only tool               -> annotations.readOnlyHint == true
    every error response             -> isError set consistently
    group labels not in config       -> assign_groups refuses
    any compute_* result             -> no array-shaped field in payload
"""

import pytest

pytest.importorskip("mcp")

from conftest import requires_rtivity
from evals.harness import MCPHarness

DECLARED = {"CG8093_mut": {"Monitor1.txt": [1, 16]},
            "w1118_ctrl": {"Monitor1.txt": [17, 32]}}


async def _load(h, monitor_files) -> str:
    r = await h.call("load_experiment", paths=monitor_files, name="contract")
    assert not r.is_error
    return r.data["session_id"]


def _has_numeric_array(obj) -> bool:
    """True if any list in the payload contains a bare number — a raw series."""
    if isinstance(obj, dict):
        return any(_has_numeric_array(v) for v in obj.values())
    if isinstance(obj, list):
        if any(isinstance(x, (int, float)) and not isinstance(x, bool) for x in obj):
            return True
        return any(_has_numeric_array(x) for x in obj)
    return False


# ── refusals ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compute_before_qc_refuses_and_names_fix(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = await _load(h, monitor_files)
        r = await h.call("compute_sleep", session_id=sid)
        assert r.is_error
        assert "run_qc" in r.text or "QC" in r.text


@pytest.mark.asyncio
async def test_compute_before_groups_refuses_and_names_fix(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = await _load(h, monitor_files)
        assert not (await h.call("run_qc", session_id=sid)).is_error
        r = await h.call("compute_sleep", session_id=sid)
        assert r.is_error
        assert "assign_groups" in r.text or "group" in r.text.lower()


@pytest.mark.asyncio
async def test_run_contrast_undeclared_id_enumerates_legal(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = await _load(h, monitor_files)
        await h.call("run_qc", session_id=sid)
        await h.call("assign_groups", session_id=sid, mapping=DECLARED)
        r = await h.call("run_contrast", session_id=sid, contrast_id="not_a_real_id")
        assert r.is_error
        # message enumerates the declared set so the model can pick a legal one
        assert "mut_vs_ctrl_sleep_night" in r.text


@pytest.mark.asyncio
async def test_malformed_exclusion_is_handled_not_a_traceback(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = await _load(h, monitor_files)
        r = await h.call("apply_exclusions", session_id=sid,
                         exclusions=["Monitor1.txt:xx"], reason="typo")
        assert r.is_error
        assert "Traceback" not in r.text
        assert "invalid literal" not in r.text          # the exact bug from HANDOFF-2
        assert "channel" in r.text.lower()               # actionable instead


@pytest.mark.asyncio
async def test_assign_groups_refuses_labels_not_in_config(tmp_path, monitor_files):
    pytest.importorskip("yaml")
    async with MCPHarness(tmp_path) as h:
        sid = await _load(h, monitor_files)
        r = await h.call("assign_groups", session_id=sid,
                         mapping={"foo": {"Monitor1.txt": [1, 16]},
                                  "bar": {"Monitor1.txt": [17, 32]}})
        assert r.is_error
        # Names the undeclared labels and what is declared instead — the check now
        # runs against groups:, not the contrast set (HANDOFF-9 reversal).
        assert "'foo'" in r.text and "'bar'" in r.text
        assert "CG8093_mut" in r.text


# ── the HITL gate previews before it acts ─────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_exclusions_preview_does_not_apply(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = await _load(h, monitor_files)
        await h.call("assign_groups", session_id=sid, mapping=DECLARED)
        r = await h.call("apply_exclusions", session_id=sid,
                         exclusions=["Monitor1.txt:5"], reason="empty tube",
                         confirm=False)
        assert not r.is_error
        assert r.data["applied"] is False
        assert "n_by_group" in r.data                     # previews the Δn


# ── annotations make the boundary visible at the protocol ─────────────────────

@pytest.mark.asyncio
async def test_read_only_tools_declare_read_only(tmp_path):
    async with MCPHarness(tmp_path) as h:
        ann = await h.annotations()
    for name in ("describe_experiment", "run_qc", "list_contrasts",
                 "compute_sleep", "compute_activity", "compute_rhythmicity",
                 "compute_survival", "run_contrast"):
        assert ann[name].readOnlyHint is True, name
    for name in ("apply_exclusions", "render_report"):
        assert ann[name].readOnlyHint is False, name
        assert ann[name].destructiveHint is True, name


# ── isError is consistent across every failure ────────────────────────────────

@pytest.mark.asyncio
async def test_isError_is_consistent_across_refusals(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = await _load(h, monitor_files)
        failures = [
            await h.call("describe_experiment", session_id="ghost"),
            await h.call("compute_sleep", session_id=sid),
            await h.call("run_contrast", session_id=sid, contrast_id="ghost"),
            await h.call("apply_exclusions", session_id=sid,
                         exclusions=["bad-format"], reason="x"),
        ]
    # every one of these cannot be fulfilled as made -> isError true, no green
    # success carrying an {"error": ...} body
    assert all(f.is_error for f in failures)


# ── window tools: choose the window before excluding ──────────────────────────

@pytest.mark.asyncio
async def test_window_tradeoff_returns_a_curve(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = await _load(h, monitor_files)
        r = await h.call("window_tradeoff", session_id=sid)
        assert not r.is_error
        rows = r.data["rows"]
        assert len(rows) >= 2
        for row in rows:
            assert {"end", "hours_from_start", "n_alive", "n_died"} <= set(row)
        # HANDOFF-7 item H11-2 — known: this holds only because the synthetic
        # corpus produces a monotone tradeoff curve. Real data falsifies it —
        # n_alive is not monotonic in window length (the curve dips and recovers
        # with the light-dark phase of the candidate end), so this asserts a
        # property of the fixture, not of window_tradeoff. The tool's own note
        # says n_alive is NOT monotonic; that is the accurate statement.
        # See docs/HANDOFF-11-first-real-run.md.
        assert rows[0]["n_alive"] >= rows[-1]["n_alive"]
        assert not _has_numeric_array(r.data)      # counts are labelled, not a series


@pytest.mark.asyncio
async def test_set_window_reruns_qc_then_refuses_after_exclusions(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = await _load(h, monitor_files)
        r = await h.call("set_analysis_window", session_id=sid,
                         end="2026-03-03T09:00:00")
        assert not r.is_error
        assert r.data["end"] == "2026-03-03T09:00:00"
        assert "tally" in r.data
        # window is chosen before exclusions; once excluded, a re-window refuses
        await h.call("assign_groups", session_id=sid, mapping=DECLARED)
        await h.call("apply_exclusions", session_id=sid,
                     exclusions=["Monitor1.txt:5"], reason="empty", confirm=True)
        r2 = await h.call("set_analysis_window", session_id=sid,
                          end="2026-03-04T09:00:00")
        assert r2.is_error
        assert "exclusion" in r2.text.lower()


# ── the boundary is a protocol-observable property ────────────────────────────

@requires_rtivity
@pytest.mark.asyncio
async def test_compute_result_has_no_array_shaped_field(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = await _load(h, monitor_files)
        await h.call("run_qc", session_id=sid)
        await h.call("assign_groups", session_id=sid, mapping=DECLARED)
        r = await h.call("compute_sleep", session_id=sid)
        assert not r.is_error
        assert not _has_numeric_array(r.data), "a raw series crossed the boundary"
