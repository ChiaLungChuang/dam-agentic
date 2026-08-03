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
  * every record carries a run_id, so a block of lines can be tied back to the
    run that produced it (HANDOFF-8's run-attribution gap). Its default is the
    *constant*, never an environment read — that is what makes the dispatch
    wiring in the next layer detectable when reverted.
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


# ── run attribution (HANDOFF-8) ───────────────────────────────────────────────
#
# The gap: audit lines are keyed by session, and sessions are named by whatever
# label the agent improvised, so a reviewer cannot say which eval run produced a
# given block of lines. The fix stamps the run on the record itself rather than
# trying to reconstruct it from the agent's output — a crashed run's Trace holds
# no tool calls at all, so anything harvested eval-side is empty for exactly the
# runs worth investigating.


def test_default_run_id_is_an_explicit_placeholder(monkeypatch):
    """Mirrors default_principal: an explicit placeholder, env-overridable, never
    a silent blank that would read as 'attributed to nothing'."""
    monkeypatch.delenv("DAM_RUN_ID", raising=False)
    assert audit.default_run_id() == "unattributed"
    monkeypatch.setenv("DAM_RUN_ID", "eval-20260727T101500Z-r0")
    assert audit.default_run_id() == "eval-20260727T101500Z-r0"


def test_empty_run_id_env_falls_back_to_the_placeholder(monkeypatch):
    """A launch spec that builds env from a maybe-None value yields "", which
    serialises as a present-but-blank key: it reads as attributed-to-nothing and
    a grep for the placeholder misses it. Pins `or`, not `get(..., default)`."""
    monkeypatch.setenv("DAM_RUN_ID", "")
    assert audit.default_run_id() == "unattributed"


def test_run_id_is_serialised_on_every_line(tmp_path):
    log = audit.AuditLog(tmp_path / "audit.jsonl")
    log.record(audit.AuditRecord.now(
        principal="anon", tool="run_qc", session_id="dam-1",
        params={}, data_files=[], outcome="ok", duration_ms=0.0,
        run_id="eval-x-r0"))
    (line,) = (tmp_path / "audit.jsonl").read_text().splitlines()
    back = json.loads(line)
    assert back["run_id"] == "eval-x-r0"


def test_record_without_a_run_id_says_unattributed_not_blank(monkeypatch, tmp_path):
    """The default is the CONSTANT, not a call to default_run_id(). This is the
    load-bearing line: it is what makes reverting the dispatch wiring show up as
    'unattributed' on the record while the environment says otherwise, instead of
    the record quietly picking the env up by itself and hiding the revert."""
    monkeypatch.setenv("DAM_RUN_ID", "eval-set-in-the-environment")
    log = audit.AuditLog(tmp_path / "audit.jsonl")
    log.record(audit.AuditRecord.now(
        principal="anon", tool="run_qc", session_id="dam-1",
        params={}, data_files=[], outcome="ok", duration_ms=0.0))
    (rec,) = audit.read_audit(tmp_path / "audit.jsonl")
    assert rec["run_id"] == "unattributed"
    assert rec["run_id"] is not None and rec["run_id"] != ""


def test_audit_module_never_imports_opentelemetry():
    """Forward guard (does NOT fail on revert). The audit stream must keep writing
    with the whole tracing stack absent — different retention, different readers,
    and the private-inference path must not have to turn tracing off to get an
    audit log. This is the only mechanical check of that rail; everything else
    about it is convention."""
    import pathlib

    import dam_mcp.audit as m
    src = pathlib.Path(m.__file__).read_text()
    assert "opentelemetry" not in src
    assert "from . import observability" not in src


def test_reader_accepts_a_legacy_line_without_a_run_id(tmp_path):
    """Forward guard (does NOT fail on revert): audit.jsonl is append-only and
    long-lived, so a file written before this change must stay readable. Pins
    that read_audit never gains schema validation that would reject old lines."""
    legacy = {
        "timestamp": "2026-07-26T09:00:00+00:00", "principal": "anonymous",
        "tool": "run_qc", "session_id": "dam-1", "params": {}, "data_files": [],
        "outcome": "ok", "error": None, "duration_ms": 1.0,
    }
    p = tmp_path / "audit.jsonl"
    p.write_text(json.dumps(legacy) + "\n")
    (back,) = audit.read_audit(p)
    assert back["tool"] == "run_qc"
    assert "run_id" not in back           # absent, and that is fine


# ── H13-1: a declared-n override reaches the audit stream ────────────────────

def test_n_overrides_defaults_to_empty_and_does_not_change_the_outcome():
    """A field, not a fourth outcome. The call succeeded — its outcome is `ok`,
    and ok/refused/error stays the shadow of HANDOFF-5's taxonomy rather than a
    vocabulary this module extends on its own."""
    rec = audit.AuditRecord.now(
        principal="p", tool="assign_groups", session_id="dam-1",
        params={}, data_files=[], outcome="ok",
        n_overrides=[{"group": "ctrl", "declared_n": 32, "computed_n": 96,
                      "reason": "one rack not loaded", "confirmed": True}],
    )
    assert rec.outcome == "ok"
    assert rec.n_overrides[0]["computed_n"] == 96
    assert audit.OUTCOMES == ("ok", "refused", "error")     # still three


def test_n_overrides_round_trips_through_jsonl():
    rec = audit.AuditRecord.now(
        principal="p", tool="compute_sleep", session_id="dam-1",
        params={}, data_files=[], outcome="ok",
        n_overrides=[{"group": "ctrl", "declared_n": 32, "computed_n": 96,
                      "reason": "r", "confirmed": True}],
    )
    back = json.loads(rec.to_json())
    assert back["n_overrides"][0]["group"] == "ctrl"
    assert back["outcome"] == "ok"


def test_a_record_with_no_override_carries_an_empty_list_not_a_missing_key():
    """Absent-vs-empty matters to whoever greps the stream: a missing key reads as
    'this build predates the field', an empty list reads as 'no override'."""
    rec = audit.AuditRecord.now(
        principal="p", tool="run_qc", session_id="dam-1",
        params={}, data_files=[], outcome="ok",
    )
    assert json.loads(rec.to_json())["n_overrides"] == []


# ── the counting field (H13-1 follow-on) ─────────────────────────────────────

def test_override_accepted_here_is_the_field_you_sum():
    """n_overrides is a state carried by every call on the session;
    override_accepted_here is an event true on exactly one. Summing the second
    counts decisions, summing the first counts calls."""
    session_overrides = [{"group": "ctrl", "declared_n": 32, "computed_n": 96,
                          "reason": "one rack not loaded", "confirmed": True}]
    stream = [
        audit.AuditRecord.now(principal="p", tool="assign_groups",
                              session_id="dam-1", params={}, data_files=[],
                              outcome="ok", n_overrides=session_overrides,
                              override_accepted_here=True),
        audit.AuditRecord.now(principal="p", tool="run_qc", session_id="dam-1",
                              params={}, data_files=[], outcome="ok",
                              n_overrides=session_overrides,
                              override_accepted_here=False),
        audit.AuditRecord.now(principal="p", tool="compute_sleep",
                              session_id="dam-1", params={}, data_files=[],
                              outcome="ok", n_overrides=session_overrides,
                              override_accepted_here=False),
    ]
    assert sum(1 for r in stream if r.n_overrides) == 3          # calls
    assert sum(1 for r in stream if r.override_accepted_here) == 1  # decisions


def test_absence_is_none_and_none_is_not_false():
    """A record written before the field exists has no key. Reading that as False
    would assert 'this call did not accept an override' about a record that never
    recorded either way."""
    rec = audit.AuditRecord.now(principal="p", tool="run_qc", session_id="dam-1",
                                params={}, data_files=[], outcome="ok")
    assert rec.override_accepted_here is None
    assert rec.override_accepted_here is not False


def test_an_old_audit_line_parses_and_reads_as_unknown(tmp_path):
    """The concrete back-compatibility case: JSONL on disk from before this field.
    It must load, and the missing key must surface as None rather than False."""
    old_line = json.dumps({
        "timestamp": "2026-08-01T00:00:00+00:00", "principal": "anonymous",
        "tool": "assign_groups", "session_id": "dam-old", "run_id": "r",
        "params": {}, "data_files": [], "outcome": "ok", "error": None,
        "duration_ms": 1.0, "n_overrides": [],
    })
    path = tmp_path / "old.jsonl"
    path.write_text(old_line + "\n")
    (rec,) = audit.read_audit(path)
    assert "override_accepted_here" not in rec           # absent, not false
    assert rec.get("override_accepted_here") is None
    assert rec.get("override_accepted_here") is not False


def test_the_field_round_trips_through_jsonl():
    for value in (True, False, None):
        rec = audit.AuditRecord.now(principal="p", tool="assign_groups",
                                    session_id="dam-1", params={}, data_files=[],
                                    outcome="ok", override_accepted_here=value)
        assert json.loads(rec.to_json())["override_accepted_here"] is value
