# Phase 2 acceptance — observability & audit

**Written:** 2026-07-27 · **Branch:** `phase2-observability` · **Keyless.**

The HANDOFF-6 Phase 2 acceptance criterion:

> a full eval run produces a viewable trace tree, and a separate audit log in which
> every tool call and every data file touched is accounted for.

This is the evidence. It is produced with **no API key and no network** — the real
MCP server, real dispatch, and the real instrumentation pass, with the agent loop
replaced by a direct in-process pipeline so the whole trace tree lands in one process
and is trivially viewable. (The stochastic-model version is the same code path; the
CI-enforced proofs of the eval-loop spans against a scripted/raising model are
`tests/test_eval_spans.py`.)

## What is enforced in CI

| Test | Proves |
|---|---|
| `tests/test_audit.py` (9) | audit records: tz-aware UTC timestamps, the 3 outcomes, JSONL round-trip, path resolution, un-serialisable params do not crash the log, principal placeholder |
| `tests/test_observability_dispatch.py` (9) | one span + one audit record per call; ok/refused/error classification; refusal span stays OK-status with a `tool.refused` event; error span is ERROR + records the exception; idempotent instrumentation; end-to-end data-file resolution on the real `load_experiment` |
| `tests/test_eval_spans.py` (3) | `dam.agent.run` span outcome = completed / crashed / aborted, status OK only when completed, `dam.task_completed` recorded |
| `tests/test_telemetry_config.py` (3) | offline default exports nothing; console exporter writes to **stderr** not stdout; OTLP endpoint takes priority |

## Trace tree (captured)

Root span with the five tool-dispatch spans nested under it — the last a deliberate
undeclared-contrast call, to show a refusal in the tree:

```
dam.session.demo  [UNSET]
  dam.tool.load_experiment  [OK]  outcome=ok  data_files=2
  dam.tool.run_qc  [OK]  outcome=ok  data_files=2
  dam.tool.assign_groups  [OK]  outcome=ok  data_files=2
  dam.tool.compute_sleep  [OK]  outcome=ok  data_files=2
  dam.tool.run_contrast  [OK]  outcome=refused  data_files=2
```

The refused call's span is **OK-status, not ERROR** — a `dam_mcp` guard firing is a
defensive success, not a fault (it carries a `tool.refused` event and
`dam.outcome=refused` instead). This is the distinction the trace exists to make
legible.

## Audit log (captured)

Every tool call accounted for, each naming the data files it touched:

```
[ok      ] load_experiment  principal=anonymous  files=[Monitor1.txt, Monitor2.txt]  76.9ms
[ok      ] run_qc           principal=anonymous  files=[Monitor1.txt, Monitor2.txt]  263.3ms
[ok      ] assign_groups    principal=anonymous  files=[Monitor1.txt, Monitor2.txt]  18.8ms
[ok      ] compute_sleep    principal=anonymous  files=[Monitor1.txt, Monitor2.txt]  52957.1ms
[refused ] run_contrast     principal=anonymous  files=[Monitor1.txt, Monitor2.txt]  5.7ms
           error: No declared contrast 'made_up'. The pre-registered set is: [...]

accounted: 5 tool calls, 5 with resolved data files
```

The refusal record keeps the full errors-as-prompts message — the same string the
model reads — so the audit log records *why* the pre-registration gate fired:

```json
{
  "timestamp": "2026-07-27T06:30:15.963049+00:00",
  "principal": "anonymous",
  "tool": "run_contrast",
  "session_id": "dam-0454f2c45b73",
  "params": {"session_id": "dam-0454f2c45b73", "contrast_id": "made_up"},
  "data_files": [".../Monitor1.txt", ".../Monitor2.txt"],
  "outcome": "refused",
  "error": "No declared contrast 'made_up'. The pre-registered set is: [...]",
  "duration_ms": 5.666
}
```

`load_experiment` records `session_id: null` and resolves its files from the `paths`
argument — the session does not exist until the call returns, which the record shows
honestly rather than back-filling.

## What is proven, and what is not

**Proven.** Both streams come from one instrumentation pass; every tool call is
audited exactly once with its outcome, principal, params, files touched, and
duration; the outcome taxonomy is the HANDOFF-5 one, with refusals distinguished from
faults; timestamps are tz-aware UTC; the offline default sends nothing off the box.

**Out of scope (stated, not a gap to hide).** Across the stdio boundary the
`dam.agent.run` span (eval process) and the `dam.tool.*` spans (server subprocess) do
not yet share a single W3C trace context; they correlate by `dam.session_id`, which a
collector can join on. True cross-process nesting needs `traceparent` propagation
through the MCP call — the seam is left open, not built. The **audit log has no such
limit**: it is written server-side where the data is touched, so its accounting is
complete regardless of process count. See `docs/observability.md`.

## Reproduce

```bash
export DAM_MCP_STATE_DIR=/tmp/dam-eval
export DAM_MCP_AUDIT_LOG=/tmp/dam-eval/audit.jsonl
.venv/bin/python -m pytest tests/test_audit.py tests/test_observability_dispatch.py \
    tests/test_eval_spans.py tests/test_telemetry_config.py -q
```

To send a real run's tree to a collector, set `OTEL_EXPORTER_OTLP_ENDPOINT` and run
`python -m evals.run_agent_eval --synthetic --runs 1 --provider google` (needs a key).
