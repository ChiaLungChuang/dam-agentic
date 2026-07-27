# Observability & audit (HANDOFF-6 Phase 2)

Two streams come out of one instrumentation pass over MCP tool dispatch:

| Stream | What it answers | Reader | Retention | Transport |
|---|---|---|---|---|
| **Spans** (OpenTelemetry) | *What did this run do, and how long did each step take?* | an operator watching a collector, live | short — debugging | OTLP → a collector |
| **Audit log** (`dam_mcp.audit`) | *Who did what, to which data?* | someone reviewing months later | long — the record | plain JSONL on disk |

They are deliberately separate — different readers, different retention — but they
are produced together, because both need the same three facts at the same moment: the
tool, its outcome, and the data files it touched.

## Where the instrumentation lives

`dam_mcp/observability.py` wraps a **single** chokepoint,
`FastMCP._tool_manager.call_tool`. Every tool call — over stdio from the agent, or
in-process from a test — passes through it exactly once. Wrapping there rather than
decorating the fourteen tools means the function signatures FastMCP introspects to
build its JSON schemas are never touched.

The wrapper, per call, opens a `dam.tool.<name>` span and writes one `AuditRecord`.
The eval loop (`evals/run_agent_eval.py`) adds a `dam.agent.run` span per run under a
`dam.eval.task` span, so the run-level view nests above the tool-level one.

## The outcome taxonomy (shared with HANDOFF-5, not a new one)

Both streams record the same three outcomes. They are the tool-layer shadow of the
HANDOFF-5 infrastructure-vs-agent-behaviour split, not a parallel vocabulary:

| Outcome | Meaning | Span status | HANDOFF-5 analogue |
|---|---|---|---|
| `ok` | the tool did its job | OK | completed |
| `refused` | a `dam_mcp` guard rejected the request (errors-as-prompts) | **OK** + a `tool.refused` event | agent behaviour — the caller sent something the guard caught |
| `error` | the tool raised an unexpected exception | ERROR + exception recorded | infrastructure / server fault |

A **refusal is a defensive success**, so its span is *not* errored — otherwise every
guard firing (the thing this QC server exists to do) would read as a fault in the
trace. It is still findable: the `dam.outcome` attribute and the `tool.refused` event.

At the run level (`dam.agent.run`) the same idea appears as `dam.eval.outcome` ∈
`completed` / `crashed` / `aborted`, with status OK only for `completed`. Task success
is a *separate* axis (`dam.task_completed`): a run can complete, violate no rail, and
still fail its task — so a task failure does not by itself error the span, the same
reason task completion is a fourth aggregate state and not an eighth property.

## Configuration

All wall-clock, all timezone-aware UTC. (The DAM analysis layer is deliberately
naive — TriKinetics files carry no timezone, so `DTZ001/DTZ007` are rejected there —
but an audit event is a fact about the *server*, not experimental time; the domain
rule must not leak into telemetry.)

| Env var | Effect | Default |
|---|---|---|
| `DAM_MCP_AUDIT_LOG` | path to the audit JSONL | `<state_dir>/audit.jsonl` |
| `DAM_MCP_STATE_DIR` | where sessions + the default audit log live | `~/.dam_mcp/sessions` |
| `DAM_PRINCIPAL` | recorded principal (placeholder until Phase 3) | `anonymous` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | send spans here (OTLP/HTTP) | unset |
| `DAM_TELEMETRY=console` | print spans to **stderr** (for a quick look) | unset |

With **neither** OTLP endpoint nor `DAM_TELEMETRY` set, no tracer provider is
installed at all: spans are no-ops, nothing leaves the machine. That silence is the
default on purpose — the private-inference path (no external egress) must not have to
turn tracing *off*.

OpenTelemetry is a soft dependency (the `telemetry` extra, pulled into `agent`). If it
is absent the tracer degrades to a no-op and **the audit log still writes** — it is
stdlib-only.

## Viewing the trace tree

Point the endpoint at any OTLP/HTTP collector and run the eval:

```bash
# e.g. Arize Phoenix locally: `phoenix serve` then
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006/v1/traces
export DAM_MCP_AUDIT_LOG=/tmp/dam-eval/audit.jsonl
python -m evals.run_agent_eval --synthetic --runs 1 --provider google
```

The audit log is just a file:

```bash
jq -c '{tool, outcome, data_files, principal}' /tmp/dam-eval/audit.jsonl
```

## Cross-process correlation — the honest limit

When the agent drives the server over stdio, the two span sources live in **two
processes**: `dam.agent.run` in the eval process, `dam.tool.*` in the server
subprocess. Both export to the same collector (the subprocess inherits the env), and
both carry `dam.session_id`, so a collector can **join tool calls to their run by
session id**. They do *not* yet share a single W3C trace context — true parent/child
nesting across the stdio boundary needs `traceparent` propagation through the MCP
call, which the client/adapter does not currently expose a hook for. The seam is left
open for it; it is not built. Within a single process (e.g. the acceptance demo, or a
future in-process transport) the spans nest into one tree already.

The **audit log has no such limit**: it is written server-side, where the data is
actually touched, so it accounts for every tool call and every file regardless of how
many processes are involved. See `docs/phase2-observability-report.md` for the
acceptance evidence.
