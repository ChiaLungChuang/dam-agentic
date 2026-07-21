"""The structural + QC-wrapping paths, which need only validate_dam.py (no engine).

These cover load_experiment's scan and run_qc's reshaping — the boundary between
the tested detector and the MCP layer.
"""

import pytest

from dam_mcp import engine
from dam_mcp.errors import ToolError


def test_scan_structural_reports_shape_not_counts(monitor_files):
    monitors, warnings = engine.scan_structural(monitor_files)
    assert len(monitors) == 2
    for m in monitors:
        assert m["n_channels"] == 32
        assert m["bin_seconds"] == 60
        assert "first_ts" in m and "last_ts" in m
        # shape only — never a counts array
        assert "counts" not in m and "activity" not in m
    assert isinstance(warnings, list)


def test_scan_missing_file_is_actionable():
    with pytest.raises(ToolError) as exc:
        engine.scan_structural(["/no/such/Monitor1.txt"])
    assert "not found" in str(exc.value).lower()


def test_scan_non_dam_file_is_actionable(tmp_path):
    bad = tmp_path / "Monitor1.txt"
    bad.write_text("this is not a DAM file\n")
    with pytest.raises(ToolError) as exc:
        engine.scan_structural([str(bad)])
    msg = str(exc.value).lower()
    assert "no rows parse" in msg or "format" in msg


def test_run_validate_and_reshape(monitor_files, tmp_path):
    qc = engine.run_validate(monitor_files, death_hours=24.0, out_path=tmp_path / "qc.json")
    shaped = engine.qc_tally_and_decisions(qc)
    assert set(shaped) >= {"tally", "flags", "decisions_required"}
    # every flagged channel carries its own evidence string
    for flag in shaped["flags"]:
        assert flag["evidence"]
        assert flag["state"] in ("empty", "died", "suspect")
    assert isinstance(shaped["decisions_required"], list)


def test_run_validate_bad_file_raises_actionable(tmp_path):
    bad = tmp_path / "Monitor1.txt"
    bad.write_text("garbage\n")
    with pytest.raises(ToolError) as exc:
        engine.run_validate([str(bad)], death_hours=24.0, out_path=tmp_path / "qc.json")
    assert "QC detector" in str(exc.value) or "process" in str(exc.value)
