"""Shared fixtures for the MCP-layer tests.

The repo root is put on sys.path so `import dam_mcp` works without an editable
install (the analysis engine is a path dependency; installing it is a separate,
deliberate step). Engine-dependent tests skip cleanly when Rtivity-Python is not
importable, so CI without the engine stays green rather than red.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE_CONTRASTS = Path(__file__).resolve().parent / "fixtures" / "contrasts.yaml"


# The contrast set is a scientific artifact belonging to whoever runs this server,
# not a test fixture. Before DAM_CONTRASTS_PATH existed the suite read the live
# config/contrasts.yaml directly, so replacing the EXAMPLE stub with a real
# pre-registration turned ten tests red — the suite was pinned to one lab's
# groups. Point every test at a fixture instead; the live file gets exactly one
# test of its own (test_config.py::test_the_repos_own_contrast_file_parses), which
# checks that it parses without caring what is in it.
@pytest.fixture(autouse=True)
def _pin_contrasts_to_fixture(monkeypatch):
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(FIXTURE_CONTRASTS))


# Session-scoped: the synthetic monitor files are read-only, so generating them
# once and sharing them across the whole run turns a ~2.5 min suite (a fresh
# corpus per test) into seconds of setup. Per-test state still uses function-scoped
# tmp_path, so sessions never collide.
@pytest.fixture(scope="session")
def corpus_dir(tmp_path_factory):
    """Generate a one-experiment synthetic corpus once and return its exp_000 dir."""
    out = tmp_path_factory.mktemp("corpus")
    subprocess.run(
        [sys.executable, str(ROOT / "damsim" / "generate.py"),
         "--out", str(out), "--n-experiments", "1", "--seed", "7",
         "--monitors", "2", "--days", "3"],
        check=True, capture_output=True,
    )
    return out / "exp_000"


@pytest.fixture(scope="session")
def monitor_files(corpus_dir):
    return sorted(str(p) for p in corpus_dir.glob("Monitor*.txt"))


def rtivity_available() -> bool:
    try:
        from dam_mcp import engine
        engine._ensure_rtivity()
        return True
    except Exception:
        return False


requires_rtivity = pytest.mark.skipif(
    not rtivity_available(),
    reason="Rtivity-Python engine not importable (set RTIVITY_PYTHON_PATH)",
)


# In-memory span capture for the observability tests. OpenTelemetry allows exactly
# one process-global tracer provider, so it is installed once and its exporter is
# cleared between tests. Reused by every test that asserts on emitted spans.
_SPAN_EXPORTER = None


@pytest.fixture
def spans():
    pytest.importorskip("opentelemetry")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    global _SPAN_EXPORTER
    if _SPAN_EXPORTER is None:
        _SPAN_EXPORTER = InMemorySpanExporter()
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            provider = TracerProvider()
            trace.set_tracer_provider(provider)
        provider.add_span_processor(SimpleSpanProcessor(_SPAN_EXPORTER))
    _SPAN_EXPORTER.clear()
    yield _SPAN_EXPORTER
    _SPAN_EXPORTER.clear()
