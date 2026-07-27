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

def configure_default_tracing(service_name: str = "dam-mcp"):
    """Install an SDK tracer provider from the environment, once. Called by the
    server ``__main__`` and the eval entrypoint.

    Export target, in priority order:
      * ``OTEL_EXPORTER_OTLP_ENDPOINT`` set  → OTLP/HTTP to that collector (Phoenix,
        the OpenTelemetry Collector, Langfuse), batched.
      * ``DAM_TELEMETRY=console``            → spans printed to stderr, immediately.
      * neither                              → **no provider installed**: the API's
        no-op tracer stays in place, so the offline default costs nothing and sends
        nothing off the machine. This matters for the private-inference path.

    Never clobbers a provider a caller (e.g. a test) already installed.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
    except ImportError:
        return None

    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return trace.get_tracer_provider()          # someone already configured it

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    mode = os.environ.get("DAM_TELEMETRY", "").lower()
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        exporter, processor = OTLPSpanExporter(), BatchSpanProcessor
    elif mode == "console":
        exporter, processor = ConsoleSpanExporter(), SimpleSpanProcessor
    else:
        return None                                  # offline, no-op

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
    """
    if getattr(mcp, "_dam_instrumented", False):
        return
    manager = mcp._tool_manager
    original = manager.call_tool

    async def call_tool(name, arguments, context=None, convert_result=False):
        store = store_provider() if store_provider is not None else None
        sid = arguments.get("session_id") if isinstance(arguments, dict) else None
        start = time.perf_counter()
        outcome, error = "ok", None
        tracer = _tracer()
        with tracer.start_as_current_span(f"dam.tool.{name}") as span:
            span.set_attribute("dam.tool", name)
            span.set_attribute("dam.session_id", sid or "")
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
                log = audit_log or audit.AuditLog()
                log.record(audit.AuditRecord.now(
                    principal=audit.default_principal(),
                    tool=name, session_id=sid,
                    params=arguments if isinstance(arguments, dict) else {},
                    data_files=files, outcome=outcome, error=error,
                    duration_ms=round(duration_ms, 3),
                ))

    manager.call_tool = call_tool
    mcp._dam_instrumented = True
