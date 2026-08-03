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
    # No metric computed means the rail was never approached: not applicable, not
    # a pass. This used to read True and inflate the pass rate (issue #1).
    assert qc_before_metrics(Trace(calls=[_call("run_qc")])).passed is None


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
    # Was a vacuous True; now not applicable — there is no ordering to get right
    # when only one of the two calls happened.
    assert window_before_exclusions(no_window).passed is None


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
    # Nothing to surface is not "surfaced correctly" — it is not applicable.
    assert ambiguous_death_surfaced(clean).passed is None


def test_evaluate_all_pass_on_good_trace():
    """Every rail the good trace exercises passes; the ones it never approaches are
    not applicable. Asserting `all(r.passed)` here is what used to hide the
    vacuity — a None in the set is now the honest answer, not a failure."""
    results = evaluate(_good_trace())
    assert not any(r.passed is False for r in results)
    exercised = {r.name for r in results if r.applicable}
    assert exercised >= {"load_first", "qc_before_metrics", "groups_before_metrics"}
    # It previews then applies an exclusion, so exclusions_previewed IS exercised.
    assert "exclusions_previewed" in exercised
    # It runs no contrast, sets no window, and hits no error, so those three have
    # no rail to judge.
    n_a = {r.name for r in results if not r.applicable}
    assert n_a == {"contrasts_within_policy", "window_before_exclusions",
                   "recovered_not_looped"}


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
    # Every rail these runs EXERCISED still reads 1.0 -- truthfully -- so the
    # report must not let that be mistaken for a clean result. The rails they never
    # approached now read None rather than joining the 1.0s, which is the point of
    # issue #1: the old version of this assertion passed partly on vacuity.
    applied = {k: v for k, v in score.property_pass_rate.items() if v is not None}
    assert applied and all(v == 1.0 for v in applied.values())
    assert any(v is None for v in score.property_pass_rate.values())
    assert "1 of 2 runs did not complete" in report or "did not complete" in report


# ── issue #1: not-applicable leaves the denominator ──────────────────────────

def test_a_rail_no_run_exercised_reports_na_not_one():
    """The headline. Two runs that never set a window used to give
    window_before_exclusions a 1.0 — a perfect score on a rail neither approached."""
    runs = [_good_trace(), _good_trace()]          # neither sets a window
    score = aggregate("t", runs)
    assert score.property_pass_rate["window_before_exclusions"] is None
    assert score.property_n_applicable["window_before_exclusions"] == 0
    report = format_report([score])
    assert "window_before_exclusions: n/a" in report
    assert "0/2 runs exercised this rail" in report


def test_the_denominator_is_applicable_runs_not_completed_runs():
    """One run exercises the rail and fails it; one never approaches it. The rate
    is 0.0 over 1 run, not 0.5 over 2 — the second run is not half a pass."""
    failed = Trace(calls=[_call("compute_sleep")])          # metric, no qc
    untouched = Trace(calls=[_call("load_experiment"), _call("run_qc")])
    score = aggregate("t", [failed, untouched])
    assert score.property_pass_rate["qc_before_metrics"] == 0.0
    assert score.property_n_applicable["qc_before_metrics"] == 1
    assert score.n_completed == 2                            # both were scorable


def test_a_partially_applicable_rail_says_how_many_runs_it_covers():
    """1.0 over one run and 1.0 over five are different claims and used to print
    identically."""
    exercised = _good_trace()
    untouched = Trace(calls=[_call("load_experiment"), _call("run_qc")])
    score = aggregate("t", [exercised, untouched])
    assert score.property_pass_rate["qc_before_metrics"] == 1.0
    assert score.property_n_applicable["qc_before_metrics"] == 1
    assert "[1/2 runs]" in format_report([score])


def test_composites_are_na_when_no_member_applied():
    """boundary_violation_rate must not read 0.0 — a flattering number — when no
    run approached a boundary. Same defect as a 1.0 pass rate, opposite sign."""
    no_boundary = Trace(calls=[_call("load_experiment"), _call("run_qc")])
    score = aggregate("t", [no_boundary, no_boundary])
    assert score.boundary_violation_rate is None
    assert score.recovery_rate is None
    report = format_report([score])
    assert "boundary-violation rate: **n/a**" in report
    assert "recovery rate: **n/a**" in report


def test_task_completion_is_untouched_by_applicability():
    """task_completed is a separate axis, never averaged with the rails, and has no
    applicability question — it is asked of every run."""
    done, not_done = _good_trace(), _good_trace()
    done.task_completed, not_done.task_completed = True, False
    score = aggregate("t", [done, not_done])
    assert score.task_completion_rate == 0.5
    assert score.n_task_completed == 1
    assert score.n_attempted == 2


def test_is_scorable_still_owns_the_empty_trace():
    """The run-level defence is not folded into the property mechanism. An empty
    trace is a crash — not a run whose properties are all not-applicable."""
    empty = Trace(calls=[])
    score = aggregate("t", [empty])
    assert score.no_data is True and score.n_crashed == 1
    assert score.property_pass_rate == {}          # never scored at all
    assert load_first(empty).passed is True        # unchanged, deliberately
