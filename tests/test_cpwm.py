"""Counts per waking minute is per MINUTE, at any bin width (definition change).

`_cpwm_by_phase` used to divide total counts by the number of bins with
movement and label the result "per waking minute". That is per waking minute
only at 60 s bins; at 30 s it is per half-minute, half the true rate
(tests/test_metamorphic.py found 4.758 at 60 s vs 2.564 at 30 s for the same
recording). The owner decided the definition: counts per actual waking
minute, the waking time being moving bins × the bin width read from the
timestamps (HANDOFF-14, "Definition changes").

Frames are built by hand, so these need no engine and run everywhere.
"""

from __future__ import annotations

import math

import pandas as pd

from dam_mcp.engine import _cpwm_by_phase


def _frame(bin_s: int, counts: list[int], animal: str = "M|01") -> pd.DataFrame:
    return pd.DataFrame({
        "id": animal, "labels": "g", "order": 1, "channel": 1, "monitor": "M",
        "phase": "Light",
        "t": [float(i * bin_s) for i in range(len(counts))],
        "activity": counts,
        "moving": [c > 0 for c in counts],
    })


def _cpwm(df: pd.DataFrame) -> float:
    out = _cpwm_by_phase(df)
    assert len(out) == 1
    return float(out["y"].iloc[0])


def test_counts_per_waking_minute_at_60s_bins_is_counts_per_moving_bin():
    """Unchanged at 60 s: 30 counts over 3 moving minutes is 10 per minute."""
    assert _cpwm(_frame(60, [10, 0, 10, 10, 0])) == 10.0


def test_counts_per_waking_minute_at_30s_bins_is_per_minute_not_per_bin():
    """30 counts over 3 moving 30 s bins is 1.5 waking minutes: 20 per minute.
    The old definition reported 10."""
    assert _cpwm(_frame(30, [10, 0, 10, 10, 0])) == 20.0


def test_the_same_movement_at_two_bin_widths_gives_one_rate():
    """Each 60 s count split into two 30 s halves, both moving: same rate."""
    sixty = [8, 0, 6, 4]
    thirty = [h for c in sixty for h in (c // 2, c - c // 2)]
    assert math.isclose(_cpwm(_frame(60, sixty)), _cpwm(_frame(30, thirty)))


def test_bin_width_is_taken_per_animal_from_its_own_timestamps():
    """Two animals on different rigs in one table: each gets its own width."""
    df = pd.concat([_frame(60, [6, 6], "A|01"), _frame(30, [6, 6], "B|01")])
    out = _cpwm_by_phase(df).set_index("id")["y"]
    assert out["A|01"] == 6.0 and out["B|01"] == 12.0


def test_one_dropped_read_does_not_change_the_bin_width():
    df = _frame(60, [5] * 10).drop(index=4)
    assert _cpwm(df) == 5.0


def test_an_animal_that_never_moves_scores_zero():
    assert _cpwm(_frame(60, [0, 0, 0])) == 0.0
