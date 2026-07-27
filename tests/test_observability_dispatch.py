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
