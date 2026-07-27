"""Tying a block of audit lines back to the eval run that produced them.

The gap (HANDOFF-8): audit records are keyed by session, and sessions are named
by whatever label the agent improvised, so nothing connects a run to its lines.

The fix stamps the run server-side rather than reconstructing it caller-side, and
that direction is forced rather than preferred. run_task's crash branch appends a
Trace with *no calls at all*, so anything harvested from the agent's output is
empty for exactly the runs someone opens audit.jsonl to investigate. The abort
path is worse: it raises before any Trace is built. A run that aborts halfway
still executed real tool calls against real data.

What is pinned here:
  * the id reaches the server through the subprocess launch spec, never by
    mutating this process's environment;
  * every run gets its own id, and two runs of one task do not share one;
  * the id is on the span for a completed, a crashed AND an aborted run — it is
    set before anything in the run can raise.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")
pytest.importorskip("opentelemetry")

from evals.run_agent_eval import EvalAborted, EvalTask, run_task


def _runs(spans):
    return [s for s in spans.get_finished_spans() if s.name == "dam.agent.run"]


# ── the launch spec is the channel ────────────────────────────────────────────

def test_server_spec_carries_extra_env():
    from agent.graph import _server_spec
    spec = _server_spec(env_extra={"DAM_RUN_ID": "eval-x-r0"})
    assert spec["env"]["DAM_RUN_ID"] == "eval-x-r0"
    # the inherited environment is still there — the extras are a merge, not a
    # replacement, or the server would lose DAM_MCP_STATE_DIR and RTIVITY_PYTHON_PATH
    assert "PATH" in spec["env"]


def test_server_spec_does_not_mutate_this_process(monkeypatch):
    """Setting os.environ['DAM_RUN_ID'] in the parent would be the easy version and
    is wrong: pytest runs the instrumented server in-process elsewhere in this
    suite, so a leaked id would stamp unrelated audit lines in whichever test ran
    next. Order-dependent, and invisible until it bites."""
    from agent.graph import _server_spec
    monkeypatch.delenv("DAM_RUN_ID", raising=False)
    _server_spec(env_extra={"DAM_RUN_ID": "eval-leak-r0"})
    assert "DAM_RUN_ID" not in os.environ


def test_server_spec_without_extras_is_unchanged():
    from agent.graph import _server_spec
    spec = _server_spec()
    assert spec["transport"] == "stdio"
    assert spec["args"] == ["-m", "dam_mcp.server"]
    assert "DAM_RUN_ID" not in spec["env"] or spec["env"]["DAM_RUN_ID"]


# ── the minted id ─────────────────────────────────────────────────────────────

def test_run_stamp_is_timezone_aware_utc():
    """Guards the tz rail from the direction nobody watches. A naive datetime.now()
    here would drift by the local offset and label records whose own timestamps are
    tz-aware UTC, so the id would read as a different hour than the lines it names."""
    from evals.run_agent_eval import _run_stamp
    parsed = datetime.strptime(_run_stamp(), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc)
    assert abs(parsed - datetime.now(timezone.utc)) < timedelta(minutes=5)


def test_run_ids_are_unique_per_run_and_name_the_task():
    from evals.run_agent_eval import _mint_run_id
    stamp = "20260727T101500Z"
    ids = [_mint_run_id("qc_then_sleep", stamp, i) for i in range(3)]
    assert len(set(ids)) == 3
    assert all("qc_then_sleep" in i for i in ids)
    assert all(i.startswith("eval-") for i in ids)


# ── the id survives every run outcome ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_crashed_run_span_carries_a_run_id(spans, tmp_path):
    os.environ["DAM_MCP_STATE_DIR"] = str(tmp_path)
    from langgraph.errors import GraphRecursionError

    from evals.fake import RaisingModel
    await run_task(EvalTask("rl", "go"), runs=1, model=None, provider="google",
                   llm=RaisingModel(exc=GraphRecursionError("recursion limit")))
    (span,) = _runs(spans)
    assert span.attributes["dam.eval.outcome"] == "crashed"
    assert span.attributes["dam.run_id"].startswith("eval-")


@pytest.mark.asyncio
async def test_aborted_run_span_carries_a_run_id(spans, tmp_path):
    """The one the design nearly missed. An abort raises before any Trace exists,
    so if the id were minted alongside the Trace this run would be unattributable —
    and a run that aborts halfway has already made real tool calls against real
    data. An id that only exists on the happy path attributes the runs that need
    it least."""
    os.environ["DAM_MCP_STATE_DIR"] = str(tmp_path)
    from evals.fake import RaisingModel
    with pytest.raises(EvalAborted):
        await run_task(EvalTask("rl", "go"), runs=1, model=None, provider="google",
                       llm=RaisingModel(exc=Exception("429 RESOURCE_EXHAUSTED")))
    (span,) = _runs(spans)
    assert span.attributes["dam.eval.outcome"] == "aborted"
    assert span.attributes["dam.run_id"].startswith("eval-")


@pytest.mark.asyncio
async def test_run_id_crosses_the_stdio_boundary_end_to_end(tmp_path, monkeypatch):
    """The one link the other tests assume rather than check: that the adapter
    actually hands `env` to the spawned subprocess. Everything else here proves
    the id reaches the launch spec, and the dispatch tests prove the server reads
    the environment — this closes the gap between them against a real subprocess.

    Keyless and engine-free: run_qc calls _require() before touching the analysis
    engine, so a bogus session id refuses without rtivity-python. A refusal is
    also the more interesting case — those lines must be attributable too."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from agent.graph import _server_spec
    from dam_mcp import audit
    monkeypatch.setenv("DAM_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DAM_MCP_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("DAM_RUN_ID", raising=False)

    client = MultiServerMCPClient(
        {"dam": _server_spec(env_extra={"DAM_RUN_ID": "eval-e2e-r0"})})
    tools = await client.get_tools()
    run_qc = next(t for t in tools if t.name == "run_qc")
    # Whether the adapter raises or returns the refusal as error content is its
    # own versioned business; this test is about the audit line either way.
    try:
        await run_qc.ainvoke({"session_id": "dam-does-not-exist"})
    except Exception:
        pass

    (rec,) = audit.read_audit(tmp_path / "audit.jsonl")
    assert rec["outcome"] == "refused"
    assert rec["run_id"] == "eval-e2e-r0"
    assert "DAM_RUN_ID" not in os.environ, "the id must not leak into this process"


@pytest.mark.asyncio
async def test_two_runs_of_one_task_get_different_ids(spans, tmp_path):
    """The regression guard for the most likely future edit: hoisting build_agent
    (or the id) out of the loop. Both runs would then share one id and a reviewer
    could no longer separate them, while every other test here still passed."""
    os.environ["DAM_MCP_STATE_DIR"] = str(tmp_path)
    from langgraph.errors import GraphRecursionError

    from evals.fake import RaisingModel
    await run_task(EvalTask("rl", "go"), runs=2, model=None, provider="google",
                   llm=RaisingModel(exc=GraphRecursionError("recursion limit")))
    ids = [s.attributes["dam.run_id"] for s in _runs(spans)]
    assert len(ids) == 2
    assert len(set(ids)) == 2
