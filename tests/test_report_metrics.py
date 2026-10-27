"""The Metrics section of the rendered report shows everything a metric carries.

It did not. `_render_metric` kept a fixed list of six columns — labels, x, n,
mean, sd, median — and skipped every key that was not a list of rows. Found by
the first end-to-end test of the happy path (PR #21), on real engine output:

* survival `summary` rendered "n / events / median / mean" with no values (the
  value is in `y`);
* `logrank_pairwise` rendered as an empty, malformed table (no column matched);
* `rhythmic_fraction` rendered n but not the fraction;
* sem, q1 and q3 were dropped from every summary table — a median with no spread;
* the computation's parameters (immobility_minutes, method, death_hours) and
  survival's `decisions_required` were dropped entirely.

The stored metrics and the metrics resource carried all of it; only the document
a person is handed lost it. The payloads below are trimmed copies of real engine
output, so the shapes are the engine's and not a guess at them.

No engine needed: the renderer reads stored summaries, so these run anywhere.
"""

from __future__ import annotations

import copy

from conftest import unrendered_metric_values
from dam_mcp.report import render_report
from dam_mcp.sessions import Session

METRICS = {
    "sleep": {
        "immobility_minutes": 5.0,
        "total_sleep_time_h": [
            {"labels": "CG8093_mut", "x": "TotalSleepTime", "n": 4, "mean": 2.111,
             "sd": 4.093, "median": 0.088, "sem": 2.046, "q1": 0.052, "q3": 2.147},
            {"labels": "w1118_ctrl", "x": "TotalSleepTime", "n": 4, "mean": 0.612,
             "sd": 0.701, "median": 0.415, "sem": 0.351, "q1": 0.163, "q3": 0.864},
        ],
    },
    "rhythmicity": {
        "method": "chi_sq",
        "dominant_period_h": [
            {"labels": "CG8093_mut", "x": "DominantPeriod", "n": 4, "mean": 24.0,
             "sd": 0.0, "median": 24.0, "sem": 0.0, "q1": 24.0, "q3": 24.0},
        ],
        "rhythmic_fraction": [
            {"labels": "CG8093_mut", "n": 4, "n_rhythmic": 3, "rhythmic_fraction": 0.75},
            {"labels": "w1118_ctrl", "n": 4, "n_rhythmic": 4, "rhythmic_fraction": 1.0},
        ],
    },
    "survival": {
        "death_hours": 12.0,
        # Long format, with the engine's always-null identity columns left in.
        "summary": [
            {"id": None, "labels": "CG8093_mut", "order": None, "channel": None,
             "monitor": None, "x": "n", "y": 4.0},
            {"id": None, "labels": "CG8093_mut", "order": None, "channel": None,
             "monitor": None, "x": "median", "y": 61.5},
        ],
        "logrank_pairwise": [
            {"group1": "CG8093_mut", "group2": "w1118_ctrl",
             "test_statistic": 2.734, "p_value": 0.0982},
        ],
        "decisions_required": [
            "Monitor2.txt ch14: active after inferred death at 2026-03-04T03:11:00",
        ],
    },
}


def _session(metrics) -> Session:
    s = Session(session_id="dam-test", name="metrics", created_at="2026-10-27")
    s.metrics = copy.deepcopy(metrics)
    return s


def _metrics_section(text: str) -> str:
    return text.split("## Metrics", 1)[1].split("## Contrasts", 1)[0]


def test_every_value_a_metric_carries_reaches_the_report():
    """The invariant, over every payload shape the engine produces: summary-stat
    tables, the rhythmic fraction, survival's long format, the pairwise log-rank
    table, scalar parameters and a decision list."""
    text = render_report(_session(METRICS))
    assert unrendered_metric_values(METRICS, text) == []


def test_survival_values_are_shown_not_just_their_names():
    text = render_report(_session(METRICS))
    row = next(ln for ln in text.splitlines()
               if ln.startswith("| CG8093_mut |") and "| median |" in ln)
    assert row.endswith("| 61.5 |")


def test_the_logrank_table_carries_its_p_value():
    text = render_report(_session(METRICS))
    (row,) = [ln for ln in text.splitlines()
              if ln.startswith("| CG8093_mut | w1118_ctrl |")]
    assert "| 2.73 |" in row and "| 0.0982 |" in row


def test_the_parameters_a_number_depends_on_are_stated():
    """A total sleep time is uninterpretable without the immobility threshold that
    defines sleep; a survival table without the death threshold likewise."""
    text = render_report(_session(METRICS))
    for line in ("- immobility_minutes: 5", "- method: chi_sq",
                 "- death_hours: 12"):
        assert line in text.splitlines()


def test_an_empty_list_is_said_to_be_empty_not_omitted():
    """An empty decisions list and a missing one must not read the same."""
    metrics = copy.deepcopy(METRICS)
    metrics["survival"]["decisions_required"] = []
    text = render_report(_session(metrics))
    lines = _metrics_section(text).splitlines()
    i = lines.index("**decisions_required**")
    assert lines[i + 1] == "_none_"


def test_a_column_that_is_null_in_every_row_is_not_rendered():
    """id / order / channel / monitor are always null in the survival summary.
    Dropping them hides no value, and rendering them would bury the one that is."""
    text = render_report(_session(METRICS))
    header = next(ln for ln in _metrics_section(text).splitlines()
                  if ln.startswith("| labels | x |") and "y" in ln)
    assert header == "| labels | x | y |"


def test_every_metric_table_is_well_formed():
    """The old renderer emitted `|  |` and `||` for a table with no matching
    columns. Every table line must have as many cells as its header."""
    text = render_report(_session(METRICS))
    width = None
    for line in _metrics_section(text).splitlines():
        if not line.startswith("|"):
            width = None
            continue
        cells = line.strip().strip("|").split("|")
        assert all(c.strip() for c in cells), f"empty cell in {line!r}"
        if width is None:
            width = len(cells)
        assert len(cells) == width, f"ragged table line {line!r}"


def test_the_oracle_catches_a_dropped_value():
    """The shared checker is what the end-to-end test relies on, so it is pinned
    to fail when a value really is missing — not only to pass when none is."""
    text = render_report(_session(METRICS)).replace("| 0.0982 |", "| — |")
    missing = unrendered_metric_values(METRICS, text)
    assert len(missing) == 1 and "logrank_pairwise" in missing[0]
