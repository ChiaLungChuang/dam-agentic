"""The eval loop emits a span per run reflecting the HANDOFF-5 taxonomy.

Every run is a `dam.agent.run` span under a `dam.eval.task` span. The
`dam.eval.outcome` attribute is completed / crashed / aborted; status is OK only
for a completed run. The crash and abort cases are keyless (a RaisingModel needs
no engine and no key); the completed case drives the real server over stdio with a
scripted model, so it needs the engine.
"""

import os

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")
pytest.importorskip("opentelemetry")

from conftest import requires_rtivity
from evals.fake import PENDING_SID, ScriptedModel, final, tool_step
from evals.run_agent_eval import EvalAborted, EvalTask, run_task

DECLARED = {"CG8093_mut": {"Monitor1.txt": [1, 16]},
            "w1118_ctrl": {"Monitor1.txt": [17, 32]}}


def _runs(spans):
    return [s for s in spans.get_finished_spans() if s.name == "dam.agent.run"]


@pytest.mark.asyncio
async def test_crash_run_span_is_errored_and_marked_crashed(spans, tmp_path):
    os.environ["DAM_MCP_STATE_DIR"] = str(tmp_path)
    from langgraph.errors import GraphRecursionError

    from evals.fake import RaisingModel
    from opentelemetry.trace import StatusCode
    boom = RaisingModel(exc=GraphRecursionError("recursion limit of 30 reached"))
    await run_task(EvalTask("rl", "go"), runs=1, model=None, provider="google",
                   llm=boom)
    (span,) = _runs(spans)
    assert span.attributes["dam.eval.outcome"] == "crashed"
    assert span.status.status_code == StatusCode.ERROR


@pytest.mark.asyncio
async def test_abort_run_span_is_marked_aborted_then_raises(spans, tmp_path):
    os.environ["DAM_MCP_STATE_DIR"] = str(tmp_path)
    from evals.fake import RaisingModel
    from opentelemetry.trace import StatusCode
    boom = RaisingModel(exc=Exception("429 RESOURCE_EXHAUSTED: quota exceeded"))
    with pytest.raises(EvalAborted):
        await run_task(EvalTask("rl", "go"), runs=1, model=None, provider="google",
                       llm=boom)
    (span,) = _runs(spans)
    assert span.attributes["dam.eval.outcome"] == "aborted"
    assert span.status.status_code == StatusCode.ERROR


@requires_rtivity
@pytest.mark.asyncio
async def test_completed_run_span_is_ok_and_records_task_completion(
        spans, monitor_files, tmp_path):
    os.environ["DAM_MCP_STATE_DIR"] = str(tmp_path)
    from opentelemetry.trace import StatusCode
    script = [
        tool_step("load_experiment", paths=monitor_files, name="spans"),
        tool_step("run_qc", session_id=PENDING_SID),
        tool_step("assign_groups", session_id=PENDING_SID, mapping=DECLARED),
        tool_step("compute_sleep", session_id=PENDING_SID),
        final("Computed night sleep for both genotypes."),
    ]
    task = EvalTask("qc_then_sleep", "run the pipeline",
                    requires=("assign_groups", "compute_sleep"))
    await run_task(task, runs=1, model=None, provider="google",
                   llm=ScriptedModel(script=script))
    (span,) = _runs(spans)
    assert span.attributes["dam.eval.outcome"] == "completed"
    assert span.attributes["dam.task_completed"] is True
    assert span.status.status_code == StatusCode.OK
    # the per-task parent span is present too
    assert any(s.name == "dam.eval.task" for s in spans.get_finished_spans())
