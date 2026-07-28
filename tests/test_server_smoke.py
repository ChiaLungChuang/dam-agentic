"""Smoke test for the MCP wiring. Requires the mcp package; skips without it.

This does not drive a client — the real integration gate is MCP Inspector or
Claude Desktop discovering the tools (see docs). It only proves the server module
imports, registers its tools, and exposes the expected surface, so a syntax or
wiring regression is caught by pytest rather than at client-connect time.
"""

import pytest

pytest.importorskip("mcp")

EXPECTED_TOOLS = {
    "load_experiment", "describe_experiment", "run_qc", "assign_groups",
    "apply_exclusions", "list_contrasts", "compute_sleep", "compute_activity",
    "compute_rhythmicity", "compute_survival", "run_contrast", "render_report",
    "window_tradeoff", "set_analysis_window",
}


@pytest.mark.asyncio
async def test_server_registers_expected_tools():
    from dam_mcp import server
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names


@pytest.mark.asyncio
async def test_server_starts_and_lists_tools_with_no_contrast_set(monkeypatch):
    """DAM_PREREG_PATH has no default and refuses when unset. That refusal must
    be per *tool call*, never at startup: an operator whose MCP client shows the
    server as failed reads it as a broken server, not as a missing
    pre-registration, and goes looking in the wrong place entirely.

    So the whole tool surface must still register with nothing configured. The
    three contrast-dependent tools refuse when called — see test_config.py."""
    monkeypatch.delenv("DAM_PREREG_PATH", raising=False)
    from dam_mcp import server
    tools = await server.mcp.list_tools()
    assert EXPECTED_TOOLS <= {t.name for t in tools}


@pytest.mark.asyncio
async def test_server_registers_resources():
    from dam_mcp import server
    templates = await server.mcp.list_resource_templates()
    uris = {t.uriTemplate for t in templates}
    assert any("manifest" in u for u in uris)
    assert any("qc-report" in u for u in uris)


READ_ONLY_TOOLS = {
    "describe_experiment", "run_qc", "list_contrasts", "compute_sleep",
    "compute_activity", "compute_rhythmicity", "compute_survival", "run_contrast",
    "window_tradeoff",
}
DESTRUCTIVE_TOOLS = {"apply_exclusions", "render_report"}


@pytest.mark.asyncio
async def test_tool_annotations_make_boundary_visible():
    """#8 — read-only tools advertise readOnlyHint; the state-changing gates
    advertise destructiveHint, so a client can tell them apart at the protocol."""
    from dam_mcp import server
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in READ_ONLY_TOOLS:
        assert tools[name].annotations.readOnlyHint is True, name
    for name in DESTRUCTIVE_TOOLS:
        assert tools[name].annotations.readOnlyHint is False, name
        assert tools[name].annotations.destructiveHint is True, name
    # load_experiment creates state but is not a read-only or destructive op
    assert tools["load_experiment"].annotations.readOnlyHint is False


def test_channel_spec_expansion():
    from dam_mcp.server import _expand_channels
    assert _expand_channels([1, 16]) == list(range(1, 17))     # pair = range
    assert _expand_channels("1,3,5-7") == [1, 3, 5, 6, 7]
    assert _expand_channels([2, 4, 6, 8]) == [2, 4, 6, 8]      # >2 = explicit set


def test_channel_spec_guards_are_actionable():
    """#6 — a malformed spec is an actionable ToolError, not a raw ValueError."""
    from dam_mcp.server import _expand_channels, _parse_exclusion, _to_int
    from dam_mcp.errors import ToolError
    for bad in ("1-x", "abc"):
        with pytest.raises(ToolError):
            _expand_channels(bad)
    with pytest.raises(ToolError):
        _expand_channels([1, 2, 99])        # out of 1..32
    with pytest.raises(ToolError):
        _parse_exclusion("no-colon-here")
    with pytest.raises(ToolError):
        _parse_exclusion("Monitor1.txt:xyz")
    with pytest.raises(ToolError):
        _to_int("nope", "channel")
