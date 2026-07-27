"""The audit stream — a structured record per tool invocation.

This is deliberately stdlib-only and independent of OpenTelemetry: the audit log
is a separate stream from the debugging traces (HANDOFF-6 Phase 2), with different
retention and different readers, so it must not need the tracing stack to exist.

What is pinned here:
  * every record carries a timezone-aware UTC timestamp. TriKinetics data is
    naive by domain rule (DTZ001/DTZ007 rejected), but an audit event is a
    wall-clock fact and must be tz-aware — the domain rule must not leak into the
    telemetry layer (HANDOFF-6 Phase 2, explicit).
  * outcome is one of ok / refused / error, the tool-layer shadow of the
    HANDOFF-5 taxonomy (a refusal is a defensive success; an error is a fault).
  * the log round-trips as JSONL and never raises on an un-serialisable param.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from dam_mcp import audit


def test_timestamp_is_timezone_aware_utc():
    rec = audit.AuditRecord.now(
        principal="anon", tool="run_qc", session_id="dam-1",
        params={}, data_files=[], outcome="ok", duration_ms=1.0,
    )
    parsed = datetime.fromisoformat(rec.timestamp)
    assert parsed.tzinfo is not None, "audit timestamps must be tz-aware, not naive"
    assert parsed.utcoffset() == timezone.utc.utcoffset(None), "must be UTC"


def test_outcomes_are_the_three_taxonomy_values():
    assert audit.OUTCOMES == ("ok", "refused", "error")


def test_record_round_trips_as_jsonl(tmp_path):
    log = audit.AuditLog(tmp_path / "audit.jsonl")
    rec = audit.AuditRecord.now(
        principal="anon", tool="load_experiment", session_id=None,
        params={"paths": ["/data/Monitor1.txt"], "name": "e"},
        data_files=["/data/Monitor1.txt"], outcome="ok", duration_ms=2.5,
    )
    log.record(rec)
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(lines) == 1
    back = json.loads(lines[0])
    assert back["tool"] == "load_experiment"
    assert back["data_files"] == ["/data/Monitor1.txt"]
    assert back["outcome"] == "ok"
    assert back["principal"] == "anon"


def test_appends_one_line_per_record(tmp_path):
    log = audit.AuditLog(tmp_path / "audit.jsonl")
    for i in range(3):
        log.record(audit.AuditRecord.now(
            principal="anon", tool="run_qc", session_id=f"dam-{i}",
            params={}, data_files=[], outcome="ok", duration_ms=0.0))
    assert len(audit.read_audit(tmp_path / "audit.jsonl")) == 3


def test_error_outcome_records_the_message(tmp_path):
    log = audit.AuditLog(tmp_path / "audit.jsonl")
    log.record(audit.AuditRecord.now(
        principal="anon", tool="run_qc", session_id="dam-x",
        params={"session_id": "dam-x"}, data_files=[], outcome="refused",
        error="No session 'dam-x'.", duration_ms=0.1))
    (rec,) = audit.read_audit(tmp_path / "audit.jsonl")
    assert rec["outcome"] == "refused"
    assert "No session" in rec["error"]


def test_unserialisable_params_do_not_crash_the_log(tmp_path):
    """A param the model somehow sent that is not JSON-native must be coerced, not
    raise — losing the audit line is worse than an imperfect repr of one arg."""
    log = audit.AuditLog(tmp_path / "audit.jsonl")
    log.record(audit.AuditRecord.now(
        principal="anon", tool="assign_groups", session_id="dam-1",
        params={"mapping": {1, 2, 3}},          # a set is not JSON-native
        data_files=[], outcome="ok", duration_ms=0.0))
    (rec,) = audit.read_audit(tmp_path / "audit.jsonl")
    assert rec["tool"] == "assign_groups"       # the line survived


def test_path_env_overrides_state_dir(tmp_path, monkeypatch):
    target = tmp_path / "custom" / "a.jsonl"
    monkeypatch.setenv("DAM_MCP_AUDIT_LOG", str(target))
    assert audit.resolve_audit_path() == target


def test_path_defaults_under_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("DAM_MCP_AUDIT_LOG", raising=False)
    monkeypatch.setenv("DAM_MCP_STATE_DIR", str(tmp_path / "state"))
    assert audit.resolve_audit_path() == tmp_path / "state" / "audit.jsonl"


def test_default_principal_is_a_placeholder(monkeypatch):
    """Phase 2 has no real identity yet (that is Phase 3). The principal is an
    explicit placeholder, overridable by env, never silently blank."""
    monkeypatch.delenv("DAM_PRINCIPAL", raising=False)
    assert audit.default_principal() == "anonymous"
    monkeypatch.setenv("DAM_PRINCIPAL", "alice@lab")
    assert audit.default_principal() == "alice@lab"
