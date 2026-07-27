"""Exporter selection from the environment. Kept separate from the span-capture
tests because it must not install a process-wide tracer provider (that can only be
set once, and the `spans` fixture owns it)."""

import sys

import pytest

pytest.importorskip("opentelemetry")

from dam_mcp import observability


def test_offline_default_selects_no_exporter(monkeypatch):
    """No env set → nothing is exported. The private-inference path depends on the
    default sending nothing off the machine."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("DAM_TELEMETRY", raising=False)
    assert observability._select_exporter() is None


def test_console_mode_writes_to_stderr_not_stdout(monkeypatch):
    """stdout is the stdio MCP protocol channel; a console exporter there would
    corrupt the JSON-RPC stream, so console spans must go to stderr."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("DAM_TELEMETRY", "console")
    exporter, _ = observability._select_exporter()
    assert exporter.out is sys.stderr


def test_otlp_endpoint_takes_priority(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("DAM_TELEMETRY", "console")
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    exporter, _ = observability._select_exporter()
    assert isinstance(exporter, OTLPSpanExporter)
