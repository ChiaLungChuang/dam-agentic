"""`_bin_minutes` reads the bin width from the data or refuses — never assumes one.

It scales asleep bins to hours for the `total_sleep` contrast metric. When no
positive gap between reads could be found, it used to return 1.0 minute: an
assumed bin width, the exact failure CLAUDE.md names ("derive it from
consecutive timestamps, never assume"). It now raises a ToolError that says
what is wrong and what to check. Frames are built by hand; no engine needed.
"""

from __future__ import annotations

import pandas as pd
import pytest

from dam_mcp.engine import _bin_minutes
from dam_mcp.errors import ToolError


def _frame(time_diff: list[float | None]) -> pd.DataFrame:
    return pd.DataFrame({"time_diff": time_diff})


def test_bin_width_is_read_from_the_gaps():
    assert _bin_minutes(_frame([None, 60.0, 60.0, 60.0])) == 1.0
    assert _bin_minutes(_frame([None, 30.0, 30.0, 30.0])) == 0.5
    assert _bin_minutes(_frame([None, 300.0, 300.0])) == 5.0


def test_one_dropped_read_does_not_change_it():
    assert _bin_minutes(_frame([None, 60.0, 120.0, 60.0, 60.0])) == 1.0


def test_no_readable_gap_refuses_instead_of_assuming_one_minute():
    """A single read per animal (every time_diff missing) used to give 1.0."""
    with pytest.raises(ToolError) as exc:
        _bin_minutes(_frame([None, None, None]))
    msg = str(exc.value)
    assert "bin width" in msg and "timestamp" in msg
    assert "window" in msg                       # says what to check


def test_only_zero_or_negative_gaps_refuse_too():
    """Duplicate or out-of-order timestamps give no positive gap to read."""
    with pytest.raises(ToolError):
        _bin_minutes(_frame([None, 0.0, 0.0, -60.0]))
