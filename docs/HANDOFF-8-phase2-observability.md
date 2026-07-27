# HANDOFF-8 — Phase 2 (observability & audit) landed

**Written:** 2026-07-27 · **Branch:** `phase2-observability` (off `main` at `92fe29d`)

Read `CLAUDE.md` first (the architectural rails), then `HANDOFF-7` for the state this
continues from. This handoff records what Phase 2 of
`HANDOFF-6-identity-security-deployment.md` actually did. Phase 1 (Ollama) was
**skipped, not done** — see below.

## Status in one paragraph

HANDOFF-6 **Phase 2 is closed**: one instrumentation pass over MCP tool dispatch
emits an OpenTelemetry span and a separate stdlib-only audit record per tool call,
and the eval loop emits a span per run. The outcome taxonomy is HANDOFF-5's
(infrastructure-vs-agent-behaviour), extended to the tool layer as ok/refused/error.
Acceptance evidence is keyless: `docs/phase2-observability-report.md`. Design and
env knobs: `docs/observability.md`. **Four new test files (24 tests); the full suite
was 103 and grows by these; `ruff==0.16.0` clean.**

## Why Phase 1 was skipped

Phase 1 (private inference via Ollama) needs to pull a local model. In this
environment the agent proxy returns **403 to `ollama.com`, `registry.ollama.ai`, and
`huggingface.co`** (confirmed via `$HTTPS_PROXY/__agentproxy/status`), so no local
model can be fetched and the phase cannot be exercised end-to-end. Real Gemini calls
are likewise out (no key path here). Phase 2 was done instead because it is fully
keyless. **Phase 1 remains open**; the `build_agent(provider="ollama")` seam is
untouched and still never exercised end-to-end.

## What landed (four commits)

| Commit | Contents |
|---|---|
| `Add the audit stream` | `dam_mcp/audit.py` — `AuditRecord` (tz-aware UTC), `AuditLog` JSONL writer, `resolve_audit_path`, `default_principal`. Stdlib-only, no OTel dependency. `tests/test_audit.py`. |
| `Instrument MCP tool dispatch` | `dam_mcp/observability.py` — wraps `FastMCP._tool_manager.call_tool` to emit one span + one audit record per call. Wired into `server.py`. New `[telemetry]` extra (OTel api/sdk/otlp-http), pulled into `[agent]`. `tests/test_observability_dispatch.py`. |
| `Instrument the eval loop` | `evals/run_agent_eval.py` — `dam.agent.run` span per run under `dam.eval.task`, `dam.eval.outcome` ∈ completed/crashed/aborted. `tests/test_eval_spans.py`. |
| `Docs + telemetry config` | `_select_exporter` (console→stderr), `docs/observability.md`, `docs/phase2-observability-report.md`, `tests/test_telemetry_config.py`, this handoff, Phase 2 CLOSED marker. |

## Decisions worth knowing (do not silently reverse)

1. **The seam is `_tool_manager.call_tool`, not per-tool decorators.** Decorating the
   fourteen tools would risk the signatures FastMCP introspects for its JSON schemas.
   The chokepoint wraps every call once and touches no schema.
2. **A refusal is a defensive success, not a fault.** `outcome=refused` (a `dam_mcp`
   guard fired) keeps span status **OK** with a `tool.refused` event. Marking it ERROR
   would make every guard firing — the point of this QC server — look like a crash in
   the trace. Only an unexpected exception (`error`) errors the span.
3. **ok/refused/error is the HANDOFF-5 taxonomy at the tool layer, not a third
   vocabulary.** refused ⇔ agent behaviour caught by a guard; error ⇔ infrastructure
   fault. The run-level span uses completed/crashed/aborted directly.
4. **Task success is a separate axis from run outcome.** A run can `complete`, violate
   no rail, and still fail its task (`dam.task_completed=false`) — the same reason task
   completion is a fourth aggregate state and not an eighth property (HANDOFF-6 §A). A
   task failure does not error the span.
5. **Offline sends nothing.** With no `OTEL_EXPORTER_OTLP_ENDPOINT` and no
   `DAM_TELEMETRY`, no provider is installed. The private-inference path must not have
   to turn tracing *off*. Console export goes to **stderr** — stdout is the stdio MCP
   protocol channel and a span there corrupts it.
6. **Audit timestamps are tz-aware UTC**, unlike analysis timestamps (naive by domain
   rule). An audit event is a fact about the server, not experimental time; the
   `DTZ001/DTZ007` rule must not leak into telemetry.
7. **OTel is a soft dependency.** Absent it, the tracer no-ops and the audit log still
   writes (it is stdlib-only).

## Known limit, stated (not a bug)

Across the stdio boundary, `dam.agent.run` (eval process) and `dam.tool.*` (server
subprocess) **do not share a W3C trace context**. They correlate by `dam.session_id`,
which a collector can join on. True cross-process nesting needs `traceparent`
propagation through the MCP call; no client/adapter hook exposes that yet, so the seam
is left open, not built. The audit log is unaffected — it is written server-side where
the data is touched. (This is the "sixth layer" the amendment told us to assume
exists: the thing that looks complete — a trace tree — is complete only within a
process. Named, not hidden.)

## GitHub issue #2 (persist run traces)

Phase 2's audit record subsumes most of #2 (args, outcome, timestamp, files, error
text per tool call) — but server-side, keyed by session, **not** per eval-run. The
run-attribution gap HANDOFF-7 describes (sessions named by the agent's improvised
label) is still open: a reviewer can read *what each tool call did* from `audit.jsonl`,
but tying a block of audit lines to a specific eval run/task still wants the
session-naming fix. Recommend closing that gap next to the audit stream, before Phase 3.

## Exact next steps

- **Phase 1 (Ollama)** in an environment with model-registry egress. Seam is ready;
  `langchain-ollama` still not installed / not in an extra.
- **Phase 3 is still gated on a real `config/contrasts.yaml`** (HANDOFF-6-amendment-1
  §E) — a human, pre-registration task. Do not open it until the stub is replaced.
- Wire the authenticated principal (Phase 3) into `AuditRecord.principal`, replacing
  the `anonymous` placeholder. `DAM_PRINCIPAL` is the current override point.

## Working agreements (unchanged)

TDD; scoped commits; `pytest -q` **and** `ruff check .` before declaring done, with
actual counts reported; CI green on 3.11/3.12/3.13; network- and key-dependent work is
keyless-tested or skipped, never left to fail intermittently. The `test` job installs
`.[dev,engine,agent]`, and `[agent]` now pulls `[telemetry]`, so the span tests run in
CI without a CI edit.
