"""One instrumentation pass over MCP tool dispatch — spans and audit together.

HANDOFF-6 Phase 2 asks for two things that come from the same seam:

  * OpenTelemetry spans, for an operator watching a collector (Phoenix / an OTLP
    collector) understand what a run did and how long each step took;
  * the structured audit record (see ``dam_mcp.audit``) — who did what to which
    data — kept as a *separate* stream, because it has different retention and
    different readers.

Both are produced by wrapping the single dispatch chokepoint,
``FastMCP._tool_manager.call_tool``: every tool call, whether it arrives over
stdio from the agent or in-process from a test, passes through it exactly once.
Instrumenting there (rather than decorating each of the fourteen tools) means the
tool signatures FastMCP introspects to build its JSON schemas are never touched.

OpenTelemetry is a soft dependency. If it is not installed the tracer degrades to
a no-op (same pattern as ``agent.graph`` and truststore) and only the audit log —
which is stdlib-only — is written. Tracing is the layer you add, not one the
server cannot start without.

Outcome / span-status taxonomy (the tool-layer shadow of HANDOFF-5, not a new
vocabulary — see ``dam_mcp.audit``):

  * ``ok``      → span status OK. The tool did its job.
  * ``refused`` → span status OK + a ``tool.refused`` event. A ``dam_mcp`` guard
                  rejected the request (errors-as-prompts). This is a *successful
                  defensive response*, not a fault, so the span is not marked
                  errored — but the event and the ``dam.outcome`` attribute make it
                  findable.
  * ``error``   → span status ERROR, exception recorded. The tool raised an
                  unexpected exception: a server fault.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from . import audit
from .errors import ToolError as DamToolError


# ── tracer: real if OpenTelemetry is installed, a no-op otherwise ──────────────

class _NoopSpan:
    def set_attribute(self, *a, **k): pass
    def set_status(self, *a, **k): pass
    def record_exception(self, *a, **k): pass
    def add_event(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _NoopTracer:
    def start_as_current_span(self, name):        # noqa: ARG002 — mirrors the API
        return _NoopSpan()


def _tracer():
    try:
        from opentelemetry import trace
    except ImportError:
        return _NoopTracer()
    return trace.get_tracer("dam_mcp")


def get_tracer():
    """The dam tracer — a real one if OpenTelemetry is installed, else a no-op that
    supports the same span context-manager surface. For callers outside this module
    (the eval loop) that want to open their own spans."""
    return _tracer()


def mark_span(span, outcome: str, message: str | None = None) -> None:
    """Set span status from a taxonomy outcome. The success outcomes — ``ok`` (a
    tool did its job) and ``completed`` (a run finished) — are OK; everything else
    (``refused`` aside, which the dispatch wrapper handles specially) is a failure
    and carries the message. ``crashed`` (agent behaviour) and ``aborted``
    (infrastructure) are both ERROR here; the ``dam.eval.outcome`` attribute keeps
    them distinct, mirroring HANDOFF-5's two-way split."""
    if outcome in ("ok", "completed"):
        _status_ok(span)
    else:
        _status_error(span, message)


def _status_ok(span) -> None:
    try:
        from opentelemetry.trace import Status, StatusCode
        span.set_status(Status(StatusCode.OK))
    except ImportError:
        pass


def _status_error(span, message: str | None) -> None:
    try:
        from opentelemetry.trace import Status, StatusCode
        span.set_status(Status(StatusCode.ERROR, message or ""))
    except ImportError:
        pass


# ── default provider wiring for the entrypoints ────────────────────────────────

def _select_exporter():
    """Choose a span exporter + processor from the environment, in priority order:

      * ``OTEL_EXPORTER_OTLP_ENDPOINT`` set → OTLP/HTTP to that collector (Phoenix,
        the OpenTelemetry Collector, Langfuse), batched.
      * ``DAM_TELEMETRY=console``           → spans printed to **stderr** (never
        stdout — inside the stdio MCP server subprocess stdout carries the JSON-RPC
        stream, and a span written there would corrupt the protocol), immediately.
      * neither                             → ``None``: no exporter, so the offline
        default installs no provider and sends nothing off the machine. The
        private-inference path depends on this being the do-nothing default.

    Returns ``(exporter, processor_cls)`` or ``None``. Separated from provider
    installation so the decision is unit-testable without mutating the process-wide
    tracer provider (which can only be set once)."""
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        return OTLPSpanExporter(), BatchSpanProcessor
    if os.environ.get("DAM_TELEMETRY", "").lower() == "console":
        import sys
        return ConsoleSpanExporter(out=sys.stderr), SimpleSpanProcessor
    return None


def configure_default_tracing(service_name: str = "dam-mcp"):
    """Install an SDK tracer provider from the environment, once. Called by the
    server ``__main__`` and the eval entrypoint. Never clobbers a provider a caller
    (e.g. a test) already installed, and is a no-op when export is not requested
    (see ``_select_exporter``) or when OpenTelemetry is not installed."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        return None

    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return trace.get_tracer_provider()          # someone already configured it

    selected = _select_exporter()
    if selected is None:
        return None                                  # offline, no-op
    exporter, processor = selected

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(processor(exporter))
    trace.set_tracer_provider(provider)
    return provider


# ── data-files resolution ───────────────────────────────────────────────────

def resolve_data_files(arguments: dict, store) -> list[str]:
    """The monitor files a call touched, resolved server-side. Best-effort — it
    never raises, because a broken audit line is worse than a missing one.

    load_experiment names its files directly in ``paths``; every later tool refers
    to them through the ``session_id`` handle, so the files come from the session."""
    if not isinstance(arguments, dict):
        return []
    paths = arguments.get("paths")
    if isinstance(paths, list) and paths:
        return [str(p) for p in paths]
    sid = arguments.get("session_id")
    if sid and store is not None:
        try:
            session = store.get(sid)
            if session is not None:
                return list(getattr(session, "paths", []) or [])
        except Exception:
            return []
    return []


#: Tools that can accept a declared-n override. `assign_groups` is the only one:
#: it is where the checksum runs, and it REPLACES session.n_overrides wholesale on
#: every success. That replacement is what makes the event exact rather than
#: heuristic — a successful assign_groups leaving overrides in force is
#: necessarily the call that accepted them, and re-running the identical override
#: is correctly reported as another acceptance. A before/after diff of the session
#: would get that case wrong by reporting no change.
OVERRIDE_CREATING_TOOLS = frozenset({"assign_groups"})


def _override_accepted_here(name, outcome, sid, store) -> bool | None:
    """Did THIS call accept a declared-n override? The counting field.

    Three-valued on purpose. `False` is a claim — "this call did not accept one" —
    and it is only made where it is known. `None` means unknown, and the one case
    that produces it is a session the dispatch could not read; a reader must also
    treat the key's absence in older audit lines as None.

    A non-override tool is `False` rather than `None` because it is genuinely
    known: no other tool can accept one. A refused or errored assign_groups is
    `False` for the same reason — nothing was assigned, so nothing was accepted."""
    if name not in OVERRIDE_CREATING_TOOLS:
        return False
    if outcome != "ok":
        return False
    if not sid or store is None:
        return None
    try:
        session = store.get(sid)
    except Exception:
        return None
    if session is None:
        return None
    return bool(getattr(session, "n_overrides", None))


def _n_overrides(sid, store) -> list[dict]:
    """Declared-n overrides in force on this session. Best-effort, like
    resolve_data_files: a missing field or an unreadable session must not turn a
    successful call into a broken audit line.

    Read from the SESSION rather than from the tool's return value, deliberately.
    The dispatch wrapper is generic — it does not know what any tool returns, and
    in production the manager converts results to TextContent before this code
    could inspect them, so a return-value hook would work in tests and silently do
    nothing on the wire. The session is the one place the fact is durable and the
    wrapper already holds a handle to it.

    The consequence is that the field reads "in force on this session at this
    call", not "this call created it". That is the more useful audit semantics
    anyway: the compute_* line that produced a number is the line a reader has in
    front of them, and it is the one that most needs to say the n rests on a
    suppressed refusal."""
    if not sid or store is None:
        return []
    try:
        session = store.get(sid)
        if session is None:
            return []
        return list(getattr(session, "n_overrides", []) or [])
    except Exception:
        return []


# ── the instrumentation pass ────────────────────────────────────────────────

def _classify(exc: Exception) -> tuple[str, str]:
    """Map a dispatch exception to (outcome, message). A ``dam_mcp`` ToolError —
    raised directly or chained as ``__cause__`` when FastMCP wraps it — is a
    deliberate refusal; anything else is an unexpected server fault."""
    cause = exc.__cause__
    if isinstance(exc, DamToolError):
        return "refused", str(exc)
    if isinstance(cause, DamToolError):
        return "refused", str(cause)
    return "error", str(cause or exc)


def instrument_tool_dispatch(mcp, store_provider: Callable[[], object] | None = None,
                             audit_log: audit.AuditLog | None = None) -> None:
    """Wrap ``mcp._tool_manager.call_tool`` to emit one span and one audit record
    per tool call. Idempotent — a second call on the same server is a no-op, so
    importing the server module twice does not double-instrument.

    ``store_provider`` is a zero-arg callable returning the live SessionStore (a
    callable, not the store itself, so a test that swaps ``server.STORE`` is still
    seen). ``audit_log`` is injectable for tests; in production it is resolved from
    the environment per call so ``DAM_MCP_AUDIT_LOG`` / ``DAM_MCP_STATE_DIR`` are
    honoured without a restart.

    The run id (``DAM_RUN_ID``) is read here rather than accepted as a tool
    argument, so the model can neither see nor set the label on its own audit
    trail. Any caller can scope a block of lines — ``DAM_RUN_ID=... python -m
    dam_mcp.server`` from a shell or a batch job — with no eval involved.
    """
    if getattr(mcp, "_dam_instrumented", False):
        return
    manager = mcp._tool_manager
    original = manager.call_tool

    async def call_tool(name, arguments, context=None, convert_result=False):
        store = store_provider() if store_provider is not None else None
        sid = arguments.get("session_id") if isinstance(arguments, dict) else None
        # Resolved per call, not hoisted out of the wrapper: a value captured once
        # is indistinguishable from correct in a subprocess (whose environment
        # never changes after exec), so the bug would ship looking right.
        rid = audit.default_run_id()
        start = time.perf_counter()
        outcome, error = "ok", None
        tracer = _tracer()
        with tracer.start_as_current_span(f"dam.tool.{name}") as span:
            span.set_attribute("dam.tool", name)
            span.set_attribute("dam.session_id", sid or "")
            span.set_attribute("dam.run_id", rid)
            try:
                return await original(name, arguments, context=context,
                                      convert_result=convert_result)
            except Exception as exc:
                outcome, error = _classify(exc)
                if outcome == "error":
                    span.record_exception(exc)
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                files = resolve_data_files(arguments, store)
                span.set_attribute("dam.outcome", outcome)
                if files:
                    span.set_attribute("dam.data_files", files)
                if outcome == "error":
                    _status_error(span, error)
                else:
                    _status_ok(span)
                    if outcome == "refused":
                        span.add_event("tool.refused", {"dam.error": error or ""})
                overrides = _n_overrides(sid, store)
                accepted = _override_accepted_here(name, outcome, sid, store)
                if overrides:
                    span.set_attribute("dam.n_overridden_groups",
                                       [o.get("group", "?") for o in overrides])
                if accepted:
                    span.add_event("tool.n_override_accepted")
                log = audit_log or audit.AuditLog()
                log.record(audit.AuditRecord.now(
                    principal=audit.default_principal(),
                    tool=name, session_id=sid, run_id=rid,
                    params=arguments if isinstance(arguments, dict) else {},
                    data_files=files, outcome=outcome, error=error,
                    duration_ms=round(duration_ms, 3),
                    n_overrides=overrides,
                    override_accepted_here=accepted,
                ))

    manager.call_tool = call_tool
    mcp._dam_instrumented = True
