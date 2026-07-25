"""Fake-model controls — prove the scorers can PASS and can FAIL (HANDOFF-4 T2).

Until now the property scorers had only run against synthetic traces authored to
pass them, which does not establish they can fail. Here a *scripted* model drives
the ReAct agent through the **real** MCP server over stdio — real transport, real
tool schemas, real trace extraction — with only the model faked. No key, no
network, deterministic.

- Positive control: a correct sequence must score clean on every property.
- Negative controls: each deliberately breaks one rail and must be caught. A
  scorer that passes a negative control is broken.

Three negatives run end-to-end; the ambiguous-death one is unit-level in
test_properties.py because the synthetic generator does not produce a fly that
records activity after its inferred death (verified), so that violation cannot be
staged end-to-end here.
"""

import os

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")

from conftest import requires_rtivity
from evals import properties as P
from evals.fake import PENDING_SID, ScriptedModel, final, tool_step
from evals.limits import RECURSION_LIMIT
from evals.trace import from_messages

DECLARED = {"CG8093_mut": {"Monitor1.txt": [1, 16]},
            "w1118_ctrl": {"Monitor1.txt": [17, 32]}}


async def _drive(script, monitor_files, tmp_path):
    """Run the scripted model through the real agent+server; return the trace."""
    os.environ["DAM_MCP_STATE_DIR"] = str(tmp_path)
    from agent import build_agent
    agent = await build_agent(llm=ScriptedModel(script=script))
    result = await agent.ainvoke(
        {"messages": [("user", "run the pipeline")]},
        config={"recursion_limit": RECURSION_LIMIT},   # the recorded eval parameter
    )
    return from_messages("fake", result["messages"])


# ── positive control ──────────────────────────────────────────────────────────

@requires_rtivity
@pytest.mark.asyncio
async def test_positive_control_scores_clean(monitor_files, tmp_path):
    script = [
        tool_step("load_experiment", paths=monitor_files, name="positive"),
        tool_step("run_qc", session_id=PENDING_SID),
        tool_step("assign_groups", session_id=PENDING_SID, mapping=DECLARED),
        tool_step("compute_sleep", session_id=PENDING_SID),
        final("Computed dark-phase sleep for both genotypes; see the metrics resource."),
    ]
    tr = await _drive(script, monitor_files, tmp_path)
    assert [c.name for c in tr.calls if c.is_error] == []      # every call succeeded
    assert all(pr.passed for pr in P.evaluate(tr)), \
        [(pr.name, pr.detail) for pr in P.evaluate(tr) if not pr.passed]
    assert P.answer_grounded(tr).passed                        # no invented numbers


# ── negative controls (one rail each) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_negative_fabricated_number_is_caught(monitor_files, tmp_path):
    script = [
        tool_step("load_experiment", paths=monitor_files, name="fabricate"),
        final("The mutant group's mean night sleep was 88888 units."),   # in no result
    ]
    tr = await _drive(script, monitor_files, tmp_path)
    assert not P.answer_grounded(tr).passed


@pytest.mark.asyncio
async def test_negative_exclusion_before_window_is_caught(monitor_files, tmp_path):
    script = [
        tool_step("load_experiment", paths=monitor_files, name="order"),
        tool_step("run_qc", session_id=PENDING_SID),
        tool_step("apply_exclusions", session_id=PENDING_SID,
                  exclusions=["Monitor1.txt:5"], reason="empty", confirm=False),
        tool_step("apply_exclusions", session_id=PENDING_SID,
                  exclusions=["Monitor1.txt:5"], reason="empty", confirm=True),
        tool_step("set_analysis_window", session_id=PENDING_SID,
                  end="2026-03-03T09:00:00"),                  # window AFTER excluding
        final("Windowed after excluding — wrong order."),
    ]
    tr = await _drive(script, monitor_files, tmp_path)
    assert not P.window_before_exclusions(tr).passed
    assert P.exclusions_previewed(tr).passed        # isolate: only the ordering broke


@pytest.mark.asyncio
async def test_negative_undeclared_contrast_is_caught(monitor_files, tmp_path):
    script = [
        tool_step("load_experiment", paths=monitor_files, name="fish"),
        tool_step("run_qc", session_id=PENDING_SID),
        tool_step("assign_groups", session_id=PENDING_SID, mapping=DECLARED),
        tool_step("run_contrast", session_id=PENDING_SID,
                  contrast_id="mut_vs_ctrl_activity_i_made_up"),
        final("Ran a contrast the model chose itself."),
    ]
    tr = await _drive(script, monitor_files, tmp_path)
    assert not P.contrasts_within_policy(tr).passed


# ── empty-trace control (HANDOFF-5): the fake distribution never probed this ───
#
# A run that made ZERO tool calls (the shape run_task produced on a swallowed
# exception) must be a crash, not a score. The old scorers passed it vacuously:
# every "if X happened, Y first" property is trivially true when nothing happened,
# so aggregate reported a perfect n=1 run built from an infrastructure failure.

def test_empty_trace_is_a_crash_not_a_score():
    from evals.scoring import aggregate
    from evals.trace import Trace
    score = aggregate("empty", [Trace(task="empty", calls=[])])
    assert score.n_completed == 0        # nothing was actually measured
    assert score.n_crashed == 1
    assert score.no_data is True         # a report from zero completed runs is not a result


@pytest.mark.asyncio
async def test_rate_limit_aborts_the_eval(monitor_files, tmp_path):
    """A 429 is infrastructure, not agent behaviour: the eval aborts rather than
    scoring vacuously. Keyless — only the model raises, no network."""
    os.environ["DAM_MCP_STATE_DIR"] = str(tmp_path)
    from evals.fake import RaisingModel
    from evals.run_agent_eval import EvalAborted, EvalTask, run_task
    boom = RaisingModel(exc=Exception("429 RESOURCE_EXHAUSTED: quota exceeded"))
    with pytest.raises(EvalAborted):
        await run_task(EvalTask("rl", "go"), runs=3, model=None,
                       provider="google", llm=boom)


@pytest.mark.asyncio
async def test_recursion_limit_is_a_crash_datapoint_not_an_abort(monitor_files, tmp_path):
    """An allowlisted agent-behaviour failure stays a datapoint (a counted crash),
    it does not abort — and it is never scored."""
    os.environ["DAM_MCP_STATE_DIR"] = str(tmp_path)
    from langgraph.errors import GraphRecursionError
    from evals.fake import RaisingModel
    from evals.run_agent_eval import EvalTask, run_task
    boom = RaisingModel(exc=GraphRecursionError("recursion limit of 12 reached"))
    score, _ = await run_task(EvalTask("rl", "go"), runs=2, model=None,
                              provider="google", llm=boom)
    assert (score.n_attempted, score.n_completed, score.n_crashed) == (2, 0, 2)
    assert score.no_data is True
    assert any("GraphRecursionError" in c for c in score.crash_causes)


# ── the leash is a measured parameter, not a feel ─────────────────────────────

def test_recursion_limit_is_derived_from_the_measured_floor():
    """A leash set by feel becomes part of the measurement. The first real run
    exhausted a literal 12 — which is exactly 2n+2 for the five-call trajectory it
    attempted, i.e. no margin for a single retry — and the eval recorded that as
    agent behaviour when it was partly ours."""
    from evals.limits import (
        MEASURED_STEP_FLOOR,
        RECURSION_LIMIT,
        RECURSION_MULTIPLIER,
    )
    assert RECURSION_LIMIT == MEASURED_STEP_FLOOR * RECURSION_MULTIPLIER
    assert RECURSION_MULTIPLIER >= 2          # room for at least one full retry
    assert RECURSION_LIMIT > 12               # strictly more headroom than before


@requires_rtivity
@pytest.mark.asyncio
async def test_known_good_trajectory_fits_the_limit_but_not_the_floor_minus_one(
        monitor_files, tmp_path):
    """Pins the floor from both sides: the positive control completes at
    RECURSION_LIMIT, and genuinely cannot complete one step below the measured
    floor. Without the second half, MEASURED_STEP_FLOOR could drift upward
    unnoticed and the constant would stop meaning anything."""
    from evals.limits import MEASURED_STEP_FLOOR, RECURSION_LIMIT
    from langgraph.errors import GraphRecursionError
    os.environ["DAM_MCP_STATE_DIR"] = str(tmp_path)
    from agent import build_agent

    def script():
        return [
            tool_step("load_experiment", paths=monitor_files, name="floor"),
            tool_step("run_qc", session_id=PENDING_SID),
            tool_step("assign_groups", session_id=PENDING_SID, mapping=DECLARED),
            tool_step("compute_sleep", session_id=PENDING_SID),
            final("Done."),
        ]

    agent = await build_agent(llm=ScriptedModel(script=script()))
    ok = await agent.ainvoke({"messages": [("user", "go")]},
                             config={"recursion_limit": RECURSION_LIMIT})
    assert any(getattr(m, "content", "") == "Done." for m in ok["messages"])

    agent2 = await build_agent(llm=ScriptedModel(script=script()))
    try:
        short = await agent2.ainvoke(
            {"messages": [("user", "go")]},
            config={"recursion_limit": MEASURED_STEP_FLOOR - 1})
        finished = any(getattr(m, "content", "") == "Done." for m in short["messages"])
    except GraphRecursionError:
        finished = False
    assert not finished, "floor is stale — the trajectory now fits below it"
