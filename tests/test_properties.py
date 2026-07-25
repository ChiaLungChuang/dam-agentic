"""Layer 2 property assertions, tested on hand-built traces — no model, no key.

These prove the scorer itself is correct: a good trace passes, each specific
violation is caught, and aggregation reports a distribution. The LLM-in-the-loop
runner (evals/run_agent_eval.py) reuses exactly these functions, so what CI checks
here is what scores the real agent.
"""

import json

from evals.properties import (
    ambiguous_death_surfaced,
    contrasts_within_policy,
    evaluate,
    exclusions_previewed,
    groups_before_metrics,
    load_first,
    qc_before_metrics,
    recovered_not_looped,
    window_before_exclusions,
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


def test_window_before_exclusions():
    ok = Trace(calls=[_call("set_analysis_window"),
                      _call("apply_exclusions", confirm=True)])
    assert window_before_exclusions(ok).passed
    bad = Trace(calls=[_call("apply_exclusions", confirm=True),
                       _call("set_analysis_window")])          # windowed after excluding
    assert not window_before_exclusions(bad).passed
    no_window = Trace(calls=[_call("apply_exclusions", confirm=True)])
    assert window_before_exclusions(no_window).passed          # vacuous: no window set


def test_ambiguous_death_surfaced():
    surv = json.dumps({"decisions_required":
                       ["M|03: recorded activity after inferred death — confirm"]})
    ignored = Trace(calls=[ToolCall(name="compute_survival", result_text=surv)],
                    final_text="Median survival was 8 days for both groups.")
    assert not ambiguous_death_surfaced(ignored).passed        # silently resolved
    shown = Trace(calls=[ToolCall(name="compute_survival", result_text=surv)],
                  final_text="One fly recorded activity after its inferred death; "
                             "flagged for your review before trusting the curve.")
    assert ambiguous_death_surfaced(shown).passed
    clean = Trace(calls=[ToolCall(name="compute_survival",
                                  result_text='{"decisions_required": []}')],
                  final_text="Survival computed.")
    assert ambiguous_death_surfaced(clean).passed              # nothing to surface


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
    bad = Trace(calls=[_call("compute_sleep")])   # scorable, but skips load/qc/groups
    scores = aggregate("qc+sleep", [good, good, good, bad, good])
    assert scores.n_attempted == 5 and scores.n_completed == 5 and scores.n_crashed == 0
    # 4/5 runs have the full sane sequence
    assert scores.tool_sequence_accuracy == 0.8
    assert 0.0 < scores.property_pass_std["load_first"] < 0.5   # non-zero spread
    report = format_report([scores])
    assert "tool-sequence accuracy" in report and "±" in report


def test_aggregate_counts_crashes_and_refuses_to_score_zero():
    good = _good_trace()
    crashed = Trace(crashed=True, crash_cause="RateLimit: 429 RESOURCE_EXHAUSTED")
    empty = Trace(calls=[])                                # zero tool calls -> crash
    mixed = aggregate("t", [good, crashed, empty])
    assert (mixed.n_attempted, mixed.n_completed, mixed.n_crashed) == (3, 1, 2)
    assert len(mixed.crash_causes) == 2                    # distinct causes recorded

    none_done = aggregate("t", [crashed, empty])
    assert none_done.no_data is True and none_done.n_completed == 0
    report = format_report([none_done])
    assert "NO DATA" in report and "RateLimit" in report   # cause surfaced, no metrics
    assert "tool-sequence accuracy" not in report


# ── task completion: a fourth state, deliberately NOT an eighth property ──────
#
# The first real run failed its task -- two errored assign_groups calls, step-limit
# exhaustion, final text "Sorry, need more steps to process this request." -- and
# scored 1.000 on all seven properties. The properties were not wrong: they are
# rail checks, and a run that stops early violates no rail. The report was wrong,
# because nothing in it distinguished that run from one that did the work.
#
# Completion is tracked alongside n_attempted/n_completed/n_crashed rather than
# averaged into the property set, where a failed task would still read as 6/7.

def test_step_limit_sentinel_is_not_task_completion():
    from evals.trace import STEP_LIMIT_SENTINEL, Trace
    tr = Trace(task="t", calls=[_call("load_experiment"), _call("run_qc")],
               final_text=STEP_LIMIT_SENTINEL)
    assert tr.completed_task(required=("run_qc",)) is False


def test_required_tool_must_have_succeeded():
    from evals.trace import Trace
    ran = Trace(task="t", calls=[_call("load_experiment"), _call("compute_sleep")],
                final_text="Night sleep was computed for both genotypes.")
    assert ran.completed_task(required=("compute_sleep",)) is True

    errored = Trace(task="t", calls=[_call("compute_sleep", is_error=True)],
                    final_text="I could not compute it.")
    assert errored.completed_task(required=("compute_sleep",)) is False

    missing = Trace(task="t", calls=[_call("load_experiment")],
                    final_text="Loaded the files.")
    assert missing.completed_task(required=("compute_sleep",)) is False


def test_completion_is_a_separate_state_not_a_property():
    """The load-bearing distinction: completion must not be averaged in with the
    rails. If it were a property, a run that failed its task would still score
    6/7 and read as broadly fine."""
    from evals.properties import STRUCTURAL
    names = {p.__name__ for p in STRUCTURAL}
    assert not any("complet" in n for n in names)


def test_failed_task_is_visible_in_the_report_despite_perfect_rails():
    from evals.trace import STEP_LIMIT_SENTINEL
    good = _good_trace()
    good.task_completed = True
    stalled = _good_trace()                       # every rail still passes...
    stalled.final_text = STEP_LIMIT_SENTINEL      # ...but the task was not done
    stalled.task_completed = False

    score = aggregate("qc+sleep", [good, stalled])
    assert score.n_task_completed == 1
    assert score.task_completion_rate == 0.5
    report = format_report([score])
    assert "task completion" in report.lower()
    # every property still reads 1.0 -- truthfully -- so the report must not let
    # that be mistaken for a clean result
    assert all(v == 1.0 for v in score.property_pass_rate.values())
    assert "1 of 2 runs did not complete" in report or "did not complete" in report
