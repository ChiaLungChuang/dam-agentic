"""The suite's own skip machinery, tested.

`requires_rtivity` gates ten tests. When the analysis engine is not importable it
skips them — and the reason it prints is the only signal anyone gets about *why*.
A generic reason sends a reader to the wrong place: "set RTIVITY_PYTHON_PATH" is
useless advice if the real cause was a broken transitive dependency or a version
mismatch inside the engine itself.

This is the same defect the repo keeps finding, one layer down in the harness: a
bare `except Exception` that discards the cause and reports a guess.
"""

from __future__ import annotations

import conftest


def test_status_reports_available_when_the_engine_imports(monkeypatch):
    monkeypatch.setattr(conftest, "_ensure_engine", lambda: None)
    ok, cause = conftest.rtivity_status()
    assert ok is True
    assert cause == ""


def test_status_captures_the_cause_rather_than_discarding_it(monkeypatch):
    """The cause must survive into the reason string. Without this the ten skipped
    tests all read 'engine not importable' whatever actually went wrong."""
    def boom():
        raise ModuleNotFoundError("No module named 'longitudinalData'")

    monkeypatch.setattr(conftest, "_ensure_engine", boom)
    ok, cause = conftest.rtivity_status()
    assert ok is False
    assert "ModuleNotFoundError" in cause
    assert "longitudinalData" in cause


def test_skip_reason_names_the_cause(monkeypatch):
    """A skip whose reason is a fixed string is indistinguishable from a skip whose
    reason was never looked up. Pin that the built reason carries the exception."""
    def boom():
        raise RuntimeError("engine v0.11.0 does not export sleep_bout_duration")

    monkeypatch.setattr(conftest, "_ensure_engine", boom)
    reason = conftest.rtivity_skip_reason()
    assert "RuntimeError" in reason
    assert "does not export" in reason
    assert "RTIVITY_PYTHON_PATH" in reason      # the advice is still there


def test_available_helper_still_answers_a_bool(monkeypatch):
    """rtivity_available() is the older, simpler surface. Keep it working — it is
    the readable thing to call when the cause is not wanted."""
    monkeypatch.setattr(conftest, "_ensure_engine", lambda: None)
    assert conftest.rtivity_available() is True
