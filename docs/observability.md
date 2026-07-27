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
| `DAM_RUN_ID` | recorded run id — which run these lines belong to | `unattributed` |
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
jq -c '{tool, outcome, data_files, principal, run_id}' /tmp/dam-eval/audit.jsonl
```

## Run attribution — which run produced these lines

Audit records are keyed by session, and sessions are named by whatever label the
agent improvised, so `session_id` alone cannot tie a block of lines back to an
eval run. Every record and every `dam.tool.*` span therefore also carries
**`run_id`**.

```bash
# everything one run did, in order
jq -c 'select(.run_id == "eval-20260727T101500Z-qc_then_sleep-r0")' audit.jsonl

# everything one eval invocation did — all tasks, all runs share the stamp
jq -c 'select(.run_id | startswith("eval-20260727T101500Z-"))' audit.jsonl

# which runs touched a given monitor file
jq -r 'select(.data_files[]? | contains("Monitor3.txt")) | .run_id' audit.jsonl | sort -u
```

The eval prints the id pattern and the resolved audit path to stderr when it
starts, because the commonest way to conclude this did nothing is to grep the
default location out of habit while the run wrote elsewhere.

**Stamped server-side, not reconstructed caller-side.** That direction is forced:
`run_task`'s crash branch appends a `Trace` with no tool calls at all, and its
abort branch raises before any `Trace` exists — so anything harvested from the
agent's output is empty for precisely the runs someone opens `audit.jsonl` to
investigate. `load_experiment`'s `session_id` is also null by construction (that
call mints the id), and refused calls can carry stale handles. A session-keyed
join fails on all three interesting line classes.

**How the id travels.** The eval mints one per run *before* building the agent,
and passes it through the stdio launch spec (`build_agent(env_extra=...)` →
`_server_spec`), so the server has it at spawn and every call it serves is
stamped — including calls made before an abort. It is merged into the child's
environment only; setting `os.environ` in the parent would leak into the
in-process server the test suite drives elsewhere and stamp unrelated lines.

**It is not a tool argument, by design.** The model can neither see nor set it, so
it cannot label its own audit trail. Any caller can scope a block of lines with no
eval involved at all:

```bash
DAM_RUN_ID=hand-check-2026-07-27 python -m dam_mcp.server
```

Unset means `unattributed` — an explicit placeholder, never a blank, which would
read as a value. `run_id` is opaque to the server: caller-asserted, never parsed,
never a path component, and orthogonal to `principal` (Phase 3 fills that one;
this is not an identity claim).

**What it does not solve.** `load_experiment`'s audit `session_id` is still null.
`run_id` makes that line attributable to a run; it does not make it joinable to
the session that call created. Separate, still open.

## Cross-process correlation — the honest limit

When the agent drives the server over stdio, the two span sources live in **two
processes**: `dam.agent.run` in the eval process, `dam.tool.*` in the server
subprocess. Both export to the same collector (the subprocess inherits the env), and
both carry `dam.session_id` **and `dam.run_id`**, so a collector can join tool calls
to their run on either — `dam.run_id` is the reliable one, for the reasons above.
They do *not* yet share a single W3C trace context — true parent/child
nesting across the stdio boundary needs `traceparent` propagation through the MCP
call, which the client/adapter does not currently expose a hook for. The seam is left
open for it; it is not built. Within a single process (e.g. the acceptance demo, or a
future in-process transport) the spans nest into one tree already.

The **audit log has no such limit**: it is written server-side, where the data is
actually touched, so it accounts for every tool call and every file regardless of how
many processes are involved. See `docs/phase2-observability-report.md` for the
acceptance evidence.
