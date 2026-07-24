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
        config={"recursion_limit": 12},          # first-run leash (HANDOFF-4 §8)
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
