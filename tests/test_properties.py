"""Layer 2 property assertions, tested on hand-built traces — no model, no key.

These prove the scorer itself is correct: a good trace passes, each specific
violation is caught, and aggregation reports a distribution. The LLM-in-the-loop
runner (evals/run_agent_eval.py) reuses exactly these functions, so what CI checks
here is what scores the real agent.
"""

from evals.properties import (
    contrasts_within_policy,
    evaluate,
    exclusions_previewed,
    groups_before_metrics,
    load_first,
    qc_before_metrics,
    recovered_not_looped,
)
from evals.scoring import aggregate, format_report
from evals.trace import ToolCall, Trace, from_messages


def _call(name, is_error=False, result_text="", **args):
    return ToolCall(name=name, args=args, is_error=is_error, result_text=result_text)


def _good_trace() -> Trace:
    return Trace(task="qc+sleep", calls=[
        _call("load_experiment"),
        _call("run_qc"),
        _call("assign_groups"),
        _call("apply_exclusions", confirm=False),
        _call("apply_exclusions", confirm=True),
        _call("compute_sleep"),
    ], final_text="Night sleep computed.")


# ── individual properties ─────────────────────────────────────────────────────

def test_load_first():
    assert load_first(_good_trace()).passed
    bad = Trace(calls=[_call("compute_sleep"), _call("load_experiment")])
    assert not load_first(bad).passed


def test_qc_before_metrics():
    assert qc_before_metrics(_good_trace()).passed
    no_qc = Trace(calls=[_call("load_experiment"), _call("assign_groups"),
                         _call("compute_sleep")])
    assert not qc_before_metrics(no_qc).passed
    wrong_order = Trace(calls=[_call("compute_sleep"), _call("run_qc")])
    assert not qc_before_metrics(wrong_order).passed
    assert qc_before_metrics(Trace(calls=[_call("run_qc")])).passed  # no metrics


def test_groups_before_metrics():
    assert groups_before_metrics(_good_trace()).passed
    no_groups = Trace(calls=[_call("run_qc"), _call("compute_activity")])
    assert not groups_before_metrics(no_groups).passed


def test_exclusions_previewed():
    assert exclusions_previewed(_good_trace()).passed
    no_preview = Trace(calls=[_call("apply_exclusions", confirm=True)])
    assert not exclusions_previewed(no_preview).passed


def test_contrasts_within_policy():
    clean = Trace(calls=[_call("run_contrast", contrast_id="mut_vs_ctrl_sleep_night")])
    assert contrasts_within_policy(clean).passed
    fished = Trace(calls=[_call("run_contrast", is_error=True, contrast_id="made_up",
                                result_text="No declared contrast 'made_up'. ...")])
    assert not contrasts_within_policy(fished).passed


def test_recovered_not_looped():
    changed = Trace(calls=[
        _call("apply_exclusions", is_error=True, exclusions=["M:xx"]),
        _call("apply_exclusions", exclusions=["M:5"]),
    ])
    assert recovered_not_looped(changed).passed
    looped = Trace(calls=[
        _call("apply_exclusions", is_error=True, exclusions=["M:xx"]),
        _call("apply_exclusions", is_error=True, exclusions=["M:xx"]),
    ])
    assert not recovered_not_looped(looped).passed


def test_evaluate_all_pass_on_good_trace():
    results = evaluate(_good_trace())
    assert all(r.passed for r in results)
    assert {r.name for r in results} >= {"load_first", "qc_before_metrics"}


# ── trace extraction from messages ────────────────────────────────────────────

class _Msg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_from_messages_extracts_calls_tokens_and_answer():
    messages = [
        _Msg(type="ai", content="",
             tool_calls=[{"id": "c1", "name": "load_experiment",
                          "args": {"name": "exp"}}],
             usage_metadata={"input_tokens": 12, "output_tokens": 4}),
        _Msg(type="tool", tool_call_id="c1", content='{"session_id": "s1"}',
             status=None),
        _Msg(type="ai", content="Loaded. Now QC.",
             tool_calls=[{"id": "c2", "name": "run_qc", "args": {}}],
             usage_metadata={"input_tokens": 8, "output_tokens": 6}),
        _Msg(type="tool", tool_call_id="c2",
             content="Error executing tool run_qc: ...", status="error"),
        _Msg(type="ai", content="Done. n=31 for control.", tool_calls=[]),
    ]
    tr = from_messages("demo", messages, latency_s=1.5)
    assert tr.names == ["load_experiment", "run_qc"]
    assert tr.calls[1].is_error is True
    assert tr.input_tokens == 20 and tr.output_tokens == 10
    assert tr.total_tokens == 30
    assert tr.final_text == "Done. n=31 for control."
    assert tr.calls[0].result["session_id"] == "s1"


# ── aggregation reports a distribution ────────────────────────────────────────

def test_aggregate_reports_variance():
    good = _good_trace()
    bad = Trace(calls=[_call("compute_sleep")])   # skips load/qc/groups
    scores = aggregate("qc+sleep", [good, good, good, bad, good])
    assert scores.n_runs == 5
    # 4/5 runs have the full sane sequence
    assert scores.tool_sequence_accuracy == 0.8
    assert 0.0 < scores.property_pass_std["load_first"] < 0.5   # non-zero spread
    report = format_report([scores])
    assert "tool-sequence accuracy" in report and "±" in report
