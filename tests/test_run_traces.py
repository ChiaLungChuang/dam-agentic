"""Each eval run keeps its own audit log and trace (GitHub issue #2, HANDOFF-7 H18-4).

The audit stream used to go to `~/.dam_mcp/sessions/audit.jsonl` for every eval
run — outside the repo, in a home directory a cloud container discards — and a
completed run whose tool calls errored kept only the sequence of tool names.
Now every run gets a directory of its own:

  * `audit.jsonl` — the server's audit stream for that run only, because the
    eval sets DAM_MCP_AUDIT_LOG for that run's server subprocess;
  * `trace.json`  — every tool call's name, arguments, is_error and full result
    text, plus model id, latency, tokens and outcome, written as the run ends,
    so a crashed or aborted run still leaves its record.

Where the directory lives depends on intent: exploratory runs (the default) go
under the gitignored `evals/.traces/`; published runs (`--publish`) go under
`evals/traces/<run_id>/`, which git tracks. Interactive use is unchanged —
nothing here sets the variable for a server someone starts by hand.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")

from evals.run_agent_eval import (EXPLORATORY_ROOT, PUBLISHED_ROOT, EvalAborted,
                                  EvalTask, run_dir, run_task)

REPO = Path(__file__).resolve().parent.parent


def _ignored(path: Path) -> bool:
    """git's own verdict, not a re-implementation of .gitignore."""
    rel = path.relative_to(REPO)
    return subprocess.run(["git", "check-ignore", "-q", str(rel)], cwd=REPO).returncode == 0


# ── where a run's directory lives ─────────────────────────────────────────────

def test_an_exploratory_run_lives_under_a_gitignored_path(monkeypatch):
    monkeypatch.delenv("DAM_EVAL_TRACE_ROOT", raising=False)   # conftest sets it
    d = run_dir("eval-20261103T000000Z-qc-r0", publish=False, root=None)
    assert d == EXPLORATORY_ROOT / "eval-20261103T000000Z-qc-r0"
    assert _ignored(d / "audit.jsonl") and _ignored(d / "trace.json")


def test_a_published_run_lives_under_evals_traces_and_is_tracked():
    d = run_dir("eval-20261103T000000Z-qc-r0", publish=True, root=None)
    assert d == REPO / "evals" / "traces" / "eval-20261103T000000Z-qc-r0"
    assert PUBLISHED_ROOT == REPO / "evals" / "traces"
    assert not _ignored(d / "audit.jsonl") and not _ignored(d / "trace.json")


def test_an_explicit_root_overrides_both():
    assert run_dir("r", publish=True, root=Path("/x")) == Path("/x/r")


def test_interactive_use_keeps_the_default_audit_path(monkeypatch, tmp_path):
    """No eval, no override: a hand-started server still logs where it always did."""
    from dam_mcp import audit
    monkeypatch.delenv("DAM_MCP_AUDIT_LOG", raising=False)
    monkeypatch.setenv("DAM_MCP_STATE_DIR", str(tmp_path / "state"))
    assert audit.resolve_audit_path() == tmp_path / "state" / "audit.jsonl"


def test_the_eval_does_not_set_the_variable_in_its_own_process(tmp_path):
    """Set for the server subprocess only, through the launch spec — never in this
    process, where it would redirect every later server it started."""
    from agent.graph import _server_spec
    before = os.environ.get("DAM_MCP_AUDIT_LOG")
    spec = _server_spec(env_extra={"DAM_MCP_AUDIT_LOG": str(tmp_path / "a.jsonl")})
    assert spec["env"]["DAM_MCP_AUDIT_LOG"] == str(tmp_path / "a.jsonl")
    assert os.environ.get("DAM_MCP_AUDIT_LOG") == before


# ── what each run leaves behind ───────────────────────────────────────────────

def _scripted(monitor_files):
    from evals.fake import PENDING_SID, ScriptedModel, final, tool_step
    return ScriptedModel(script=[
        tool_step("load_experiment", paths=monitor_files, name="traced"),
        tool_step("assign_groups", session_id=PENDING_SID,        # refused: an error
                  mapping={"not_a_declared_group": {"Monitor1.txt": [1, 4]}}),
        final("Loaded; the grouping was refused for an undeclared label."),
    ]), PENDING_SID


@pytest.mark.asyncio
async def test_each_run_writes_its_own_audit_log_and_trace(monitor_files, tmp_path,
                                                          monkeypatch):
    monkeypatch.setenv("DAM_MCP_STATE_DIR", str(tmp_path / "state"))
    llm, _ = _scripted(monitor_files)
    _, traces = await run_task(EvalTask("traced", "go"), runs=2, model=None,
                               provider="google", llm=llm, stamp="20261103T000000Z",
                               trace_root=tmp_path / "traces", model_id="fake:scripted")
    dirs = sorted((tmp_path / "traces").iterdir())
    assert [d.name for d in dirs] == ["eval-20261103T000000Z-traced-r0",
                                      "eval-20261103T000000Z-traced-r1"]
    from dam_mcp import audit
    for d in dirs:
        records = audit.read_audit(d / "audit.jsonl")
        assert records, f"no audit lines in {d}"
        assert {r["run_id"] for r in records} == {d.name}   # this run's lines only
        trace = json.loads((d / "trace.json").read_text())
        assert trace["run_id"] == d.name and trace["outcome"] == "completed"
        assert trace["model_id"] == "fake:scripted"
        names = [c["name"] for c in trace["calls"]]
        assert names == ["load_experiment", "assign_groups"]
        refused = trace["calls"][1]
        assert refused["args"]["mapping"] == {                         # args as sent
            "not_a_declared_group": {"Monitor1.txt": [1, 4]}}
        assert refused["is_error"] is True                             # error kept
        assert "not_a_declared_group" in refused["result_text"]        # with its text
        for key in ("latency_s", "input_tokens", "output_tokens", "final_text"):
            assert key in trace


@pytest.mark.asyncio
async def test_a_crashed_run_still_leaves_its_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("DAM_MCP_STATE_DIR", str(tmp_path / "state"))
    from langgraph.errors import GraphRecursionError

    from evals.fake import RaisingModel
    await run_task(EvalTask("rl", "go"), runs=1, model=None, provider="google",
                   llm=RaisingModel(exc=GraphRecursionError("recursion limit")),
                   stamp="20261103T000000Z", trace_root=tmp_path / "traces")
    trace = json.loads(
        (tmp_path / "traces" / "eval-20261103T000000Z-rl-r0" / "trace.json").read_text())
    assert trace["outcome"] == "crashed"
    assert "GraphRecursionError" in trace["crash_cause"]


@pytest.mark.asyncio
async def test_an_aborted_run_still_leaves_its_trace(tmp_path, monkeypatch):
    """An abort raises before any Trace exists; the record must not depend on one."""
    monkeypatch.setenv("DAM_MCP_STATE_DIR", str(tmp_path / "state"))
    from evals.fake import RaisingModel
    with pytest.raises(EvalAborted):
        await run_task(EvalTask("rl", "go"), runs=1, model=None, provider="google",
                       llm=RaisingModel(exc=Exception("429 RESOURCE_EXHAUSTED")),
                       stamp="20261103T000000Z", trace_root=tmp_path / "traces")
    trace = json.loads(
        (tmp_path / "traces" / "eval-20261103T000000Z-rl-r0" / "trace.json").read_text())
    assert trace["outcome"] == "aborted"
    assert "429" in trace["abort_reason"] or "quota" in trace["abort_reason"].lower()
