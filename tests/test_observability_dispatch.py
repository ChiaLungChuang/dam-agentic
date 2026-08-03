"""Instrumentation of MCP tool dispatch: one span + one audit record per call.

Most of this is keyless and engine-free — a two-line mini FastMCP with fake tools
exercises the wrapper in isolation, so ok / refused / error are pinned without the
analysis engine. One end-to-end test (requires the engine) drives the *real*
server's load_experiment through in-process MCP dispatch to prove data_files
resolution against a genuine tool.

The distinction that matters: a refusal (a dam_mcp guard firing) is a defensive
success, not a fault. The span stays OK-status; the audit outcome is 'refused'.
Only an unexpected exception is an errored span and a 'error' outcome.
"""

import pytest

pytest.importorskip("mcp")

from mcp.server.fastmcp import FastMCP

from conftest import requires_rtivity
from dam_mcp import audit, observability
from dam_mcp.errors import ToolError


class _FakeSession:
    def __init__(self, paths):
        self.paths = paths


class _FakeStore:
    def __init__(self, sessions):
        self._sessions = sessions

    def get(self, sid):
        return self._sessions.get(sid)


@pytest.fixture
def mini(tmp_path):
    """A minimal instrumented server: three tools, one per outcome, plus a fake
    store so data_files resolves without the engine. Returns (mcp, audit_path)."""
    mcp = FastMCP("mini")

    @mcp.tool()
    def ok_tool(session_id: str) -> dict:
        return {"session_id": session_id, "ran": True}

    @mcp.tool()
    def refuse_tool(session_id: str) -> dict:
        raise ToolError("Monitor 'X' is not in this session. Use a loaded key.")

    @mcp.tool()
    def boom_tool(session_id: str) -> dict:
        raise RuntimeError("engine segfault-equivalent: not the caller's fault")

    store = _FakeStore({"dam-1": _FakeSession(["/data/Monitor1.txt",
                                               "/data/Monitor2.txt"])})
    log_path = tmp_path / "audit.jsonl"
    observability.instrument_tool_dispatch(
        mcp, store_provider=lambda: store,
        audit_log=audit.AuditLog(log_path))
    return mcp, log_path


# ── audit outcomes (keyless, no engine) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_ok_call_is_audited_with_data_files(mini):
    mcp, log_path = mini
    await mcp.call_tool("ok_tool", {"session_id": "dam-1"})
    (rec,) = audit.read_audit(log_path)
    assert rec["tool"] == "ok_tool"
    assert rec["outcome"] == "ok"
    assert rec["session_id"] == "dam-1"
    assert rec["data_files"] == ["/data/Monitor1.txt", "/data/Monitor2.txt"]
    assert rec["error"] is None
    assert rec["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_refusal_is_audited_as_refused(mini):
    mcp, log_path = mini
    with pytest.raises(Exception):                 # FastMCP surfaces isError=true
        await mcp.call_tool("refuse_tool", {"session_id": "dam-1"})
    (rec,) = audit.read_audit(log_path)
    assert rec["outcome"] == "refused"
    assert "not in this session" in rec["error"]


@pytest.mark.asyncio
async def test_unexpected_exception_is_audited_as_error(mini):
    mcp, log_path = mini
    with pytest.raises(Exception):
        await mcp.call_tool("boom_tool", {"session_id": "dam-1"})
    (rec,) = audit.read_audit(log_path)
    assert rec["outcome"] == "error"
    assert "segfault-equivalent" in rec["error"]


@pytest.mark.asyncio
async def test_every_call_produces_exactly_one_record(mini):
    mcp, log_path = mini
    await mcp.call_tool("ok_tool", {"session_id": "dam-1"})
    with pytest.raises(Exception):
        await mcp.call_tool("refuse_tool", {"session_id": "dam-1"})
    await mcp.call_tool("ok_tool", {"session_id": "dam-1"})
    recs = audit.read_audit(log_path)
    assert [r["outcome"] for r in recs] == ["ok", "refused", "ok"]


def test_instrumentation_is_idempotent(mini):
    mcp, _ = mini
    before = mcp._tool_manager.call_tool
    observability.instrument_tool_dispatch(mcp, store_provider=lambda: None)
    assert mcp._tool_manager.call_tool is before      # not re-wrapped


# ── run attribution (HANDOFF-8) ───────────────────────────────────────────────
#
# DAM_RUN_ID is handed to the server out of band by whoever launched it, and
# stamped on every line. This is where run_id first carries a real value: before
# this the field existed and always read "unattributed".


@pytest.mark.asyncio
async def test_run_id_from_the_environment_is_stamped(mini, monkeypatch):
    monkeypatch.setenv("DAM_RUN_ID", "eval-20260727T101500Z-r0")
    mcp, log_path = mini
    await mcp.call_tool("ok_tool", {"session_id": "dam-1"})
    (rec,) = audit.read_audit(log_path)
    assert rec["run_id"] == "eval-20260727T101500Z-r0"


@pytest.mark.asyncio
async def test_run_id_is_stamped_on_refused_and_errored_calls_too(mini, monkeypatch):
    """The runs a reviewer opens audit.jsonl for are the ones that went wrong. A
    stamp present only on the happy path attributes the lines that need it least."""
    monkeypatch.setenv("DAM_RUN_ID", "eval-r7")
    mcp, log_path = mini
    for tool in ("refuse_tool", "boom_tool"):
        with pytest.raises(Exception):
            await mcp.call_tool(tool, {"session_id": "dam-1"})
    recs = audit.read_audit(log_path)
    assert [r["outcome"] for r in recs] == ["refused", "error"]
    assert {r["run_id"] for r in recs} == {"eval-r7"}


@pytest.mark.asyncio
async def test_run_id_is_read_per_call_not_cached(mini, monkeypatch):
    """A resolver cached at import or in instrument_tool_dispatch's body is
    indistinguishable from correct in production — a subprocess's environment
    never changes after exec — so it would ship and look right indefinitely. Only
    an in-process change between two calls discriminates it."""
    mcp, log_path = mini
    monkeypatch.setenv("DAM_RUN_ID", "run-A")
    await mcp.call_tool("ok_tool", {"session_id": "dam-1"})
    monkeypatch.setenv("DAM_RUN_ID", "run-B")
    await mcp.call_tool("ok_tool", {"session_id": "dam-1"})
    assert [r["run_id"] for r in audit.read_audit(log_path)] == ["run-A", "run-B"]


@pytest.mark.asyncio
async def test_unset_run_id_records_the_placeholder(mini, monkeypatch):
    """A human running the stdio server in Claude Code sets nothing. Those lines
    must say 'unattributed', not blank — blank reads as a value."""
    monkeypatch.delenv("DAM_RUN_ID", raising=False)
    mcp, log_path = mini
    await mcp.call_tool("ok_tool", {"session_id": "dam-1"})
    (rec,) = audit.read_audit(log_path)
    assert rec["run_id"] == "unattributed"


@pytest.mark.asyncio
async def test_span_carries_the_run_id(mini, spans, monkeypatch):
    monkeypatch.setenv("DAM_RUN_ID", "eval-span-r1")
    mcp, _ = mini
    await mcp.call_tool("ok_tool", {"session_id": "dam-1"})
    (span,) = [s for s in spans.get_finished_spans() if s.name == "dam.tool.ok_tool"]
    assert span.attributes["dam.run_id"] == "eval-span-r1"


def test_run_id_is_never_a_tool_parameter(mini):
    """Forward guard (does NOT fail on revert). The run id arrives out of band
    precisely so the model can neither see nor set it: a tool argument would let
    the agent label its own audit trail. It also keeps the instrumentation off the
    signatures FastMCP introspects to build its JSON schemas, which is why the
    seam is _tool_manager.call_tool and not a per-tool decorator."""
    mcp, _ = mini
    for tool in mcp._tool_manager.list_tools():
        assert "run_id" not in (tool.parameters.get("properties") or {}), (
            f"{tool.name} exposes run_id as a parameter")


# ── spans (needs opentelemetry; the `spans` fixture importorskips it) ──────────

@pytest.mark.asyncio
async def test_ok_call_emits_ok_span(mini, spans):
    mcp, _ = mini
    await mcp.call_tool("ok_tool", {"session_id": "dam-1"})
    from opentelemetry.trace import StatusCode
    (span,) = [s for s in spans.get_finished_spans() if s.name == "dam.tool.ok_tool"]
    assert span.attributes["dam.tool"] == "ok_tool"
    assert span.attributes["dam.outcome"] == "ok"
    assert span.attributes["dam.data_files"] == ("/data/Monitor1.txt",
                                                  "/data/Monitor2.txt")
    assert span.status.status_code == StatusCode.OK


@pytest.mark.asyncio
async def test_refusal_span_is_ok_status_with_event(mini, spans):
    """A refusal is a defensive success: the span must NOT be errored, or every
    guard firing would look like a server fault in the trace tree."""
    mcp, _ = mini
    with pytest.raises(Exception):
        await mcp.call_tool("refuse_tool", {"session_id": "dam-1"})
    from opentelemetry.trace import StatusCode
    (span,) = [s for s in spans.get_finished_spans()
               if s.name == "dam.tool.refuse_tool"]
    assert span.attributes["dam.outcome"] == "refused"
    assert span.status.status_code != StatusCode.ERROR
    assert any(e.name == "tool.refused" for e in span.events)


@pytest.mark.asyncio
async def test_error_span_is_errored_and_records_exception(mini, spans):
    mcp, _ = mini
    with pytest.raises(Exception):
        await mcp.call_tool("boom_tool", {"session_id": "dam-1"})
    from opentelemetry.trace import StatusCode
    (span,) = [s for s in spans.get_finished_spans()
               if s.name == "dam.tool.boom_tool"]
    assert span.attributes["dam.outcome"] == "error"
    assert span.status.status_code == StatusCode.ERROR
    assert any(e.name == "exception" for e in span.events)


# ── end-to-end against the real server (needs the engine) ─────────────────────

@requires_rtivity
@pytest.mark.asyncio
async def test_real_load_experiment_audits_the_files(monitor_files, tmp_path,
                                                     monkeypatch):
    """Drive the real server's load_experiment through in-process MCP dispatch and
    confirm the audit line names the actual monitor files."""
    monkeypatch.setenv("DAM_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DAM_MCP_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    from dam_mcp import server
    from dam_mcp.sessions import SessionStore
    server.STORE = SessionStore(state_dir=tmp_path / "state")

    await server.mcp.call_tool("load_experiment",
                               {"paths": monitor_files, "name": "e2e"})
    recs = audit.read_audit(tmp_path / "audit.jsonl")
    load = [r for r in recs if r["tool"] == "load_experiment"]
    assert load, "load_experiment was not audited"
    assert sorted(load[-1]["data_files"]) == sorted(monitor_files)
    assert load[-1]["outcome"] == "ok"


# ── H13-1: the override reaches the audit stream through the chokepoint ──────

class _OverriddenSession:
    """A session whose n contradicts its own pre-registration and was confirmed."""
    paths = ["/data/Monitor1.txt"]
    n_overrides = [{"group": "ctrl", "declared_n": 32, "computed_n": 96,
                    "reason": "one rack was not loaded", "confirmed": True}]


@pytest.mark.asyncio
async def test_an_overridden_session_stamps_every_call_not_just_assign_groups(
        tmp_path):
    """Read from the session, so the compute call that produced a number carries
    the fact that its n rests on a suppressed refusal — not only the
    assign_groups that created the override, which nobody re-reads."""
    mcp = FastMCP("ov")

    @mcp.tool()
    def compute_thing(session_id: str) -> dict:
        return {"session_id": session_id}

    log_path = tmp_path / "audit.jsonl"
    observability.instrument_tool_dispatch(
        mcp, store_provider=lambda: _FakeStore({"dam-1": _OverriddenSession()}),
        audit_log=audit.AuditLog(log_path))
    await mcp.call_tool("compute_thing", {"session_id": "dam-1"})
    (rec,) = audit.read_audit(log_path)
    assert rec["outcome"] == "ok"                       # a field, not an outcome
    assert rec["n_overrides"][0]["group"] == "ctrl"
    assert rec["n_overrides"][0]["computed_n"] == 96


@pytest.mark.asyncio
async def test_a_clean_session_audits_an_empty_override_list(mini):
    """Negative control. Without it, a stamp that fired unconditionally would pass
    the test above."""
    mcp, log_path = mini
    await mcp.call_tool("ok_tool", {"session_id": "dam-1"})
    (rec,) = audit.read_audit(log_path)
    assert rec["n_overrides"] == []


@pytest.mark.asyncio
async def test_a_session_without_the_field_does_not_break_the_audit_line(tmp_path):
    """Best-effort, like data_files: a session written by an older build has no
    n_overrides attribute at all, and a broken audit line is worse than a missing
    field."""
    mcp = FastMCP("old")

    @mcp.tool()
    def t(session_id: str) -> dict:
        return {"session_id": session_id}

    log_path = tmp_path / "audit.jsonl"
    observability.instrument_tool_dispatch(
        mcp, store_provider=lambda: _FakeStore({"dam-1": _FakeSession(["/a.txt"])}),
        audit_log=audit.AuditLog(log_path))
    await mcp.call_tool("t", {"session_id": "dam-1"})
    (rec,) = audit.read_audit(log_path)
    assert rec["outcome"] == "ok" and rec["n_overrides"] == []


# ── the counting field, through the chokepoint ───────────────────────────────

@pytest.mark.asyncio
async def test_only_the_assign_groups_call_is_marked_as_accepting(tmp_path):
    """The whole point: three calls on one overridden session, one decision. The
    state field says all three rest on it; the event field says which one made it."""
    mcp = FastMCP("acc")

    @mcp.tool()
    def assign_groups(session_id: str) -> dict:
        return {"session_id": session_id}

    @mcp.tool()
    def compute_sleep(session_id: str) -> dict:
        return {"session_id": session_id}

    log_path = tmp_path / "audit.jsonl"
    observability.instrument_tool_dispatch(
        mcp, store_provider=lambda: _FakeStore({"dam-1": _OverriddenSession()}),
        audit_log=audit.AuditLog(log_path))
    await mcp.call_tool("assign_groups", {"session_id": "dam-1"})
    await mcp.call_tool("compute_sleep", {"session_id": "dam-1"})
    await mcp.call_tool("compute_sleep", {"session_id": "dam-1"})

    recs = audit.read_audit(log_path)
    assert [r["override_accepted_here"] for r in recs] == [True, False, False]
    assert sum(1 for r in recs if r["n_overrides"]) == 3     # calls on the session
    assert sum(1 for r in recs if r["override_accepted_here"]) == 1   # decisions


@pytest.mark.asyncio
async def test_a_clean_assign_groups_did_not_accept_anything(tmp_path):
    """Negative control. A session with no overrides must record False, not True —
    otherwise the field would count every assignment as a decision."""
    mcp = FastMCP("clean")

    @mcp.tool()
    def assign_groups(session_id: str) -> dict:
        return {"session_id": session_id}

    log_path = tmp_path / "audit.jsonl"
    observability.instrument_tool_dispatch(
        mcp, store_provider=lambda: _FakeStore({"dam-1": _FakeSession(["/a.txt"])}),
        audit_log=audit.AuditLog(log_path))
    await mcp.call_tool("assign_groups", {"session_id": "dam-1"})
    (rec,) = audit.read_audit(log_path)
    assert rec["override_accepted_here"] is False


@pytest.mark.asyncio
async def test_a_refused_assign_groups_accepted_nothing(tmp_path):
    """A refusal assigned nothing, so it accepted nothing. False, not None — this
    is known, not unknown."""
    mcp = FastMCP("refuse")

    @mcp.tool()
    def assign_groups(session_id: str) -> dict:
        raise ToolError("Declared-n mismatch: ... Nothing was assigned.")

    log_path = tmp_path / "audit.jsonl"
    observability.instrument_tool_dispatch(
        mcp, store_provider=lambda: _FakeStore({"dam-1": _OverriddenSession()}),
        audit_log=audit.AuditLog(log_path))
    with pytest.raises(Exception):
        await mcp.call_tool("assign_groups", {"session_id": "dam-1"})
    (rec,) = audit.read_audit(log_path)
    assert rec["outcome"] == "refused"
    assert rec["override_accepted_here"] is False


@pytest.mark.asyncio
async def test_an_unreadable_session_is_unknown_not_false(tmp_path):
    """None where it genuinely cannot be determined. Recording False here would
    claim the call accepted nothing, which the dispatch does not know."""
    mcp = FastMCP("unknown")

    @mcp.tool()
    def assign_groups(session_id: str) -> dict:
        return {"session_id": session_id}

    log_path = tmp_path / "audit.jsonl"
    observability.instrument_tool_dispatch(
        mcp, store_provider=lambda: _FakeStore({}),      # session not present
        audit_log=audit.AuditLog(log_path))
    await mcp.call_tool("assign_groups", {"session_id": "dam-missing"})
    (rec,) = audit.read_audit(log_path)
    assert rec["override_accepted_here"] is None
