"""MCPHarness — drive the DAM MCP server over stdio as a real client.

This is the mechanism behind Layer 1: every call goes over JSON-RPC to a
`python -m dam_mcp.server` subprocess, so the harness exercises the *protocol*
surface (isError, structured content, annotations, resources) exactly as MCP
Inspector or Claude Desktop would — not the Python functions directly. A contract
that holds here holds for any client.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl

REPO = Path(__file__).resolve().parent.parent


@dataclass
class ToolResult:
    """A tool response as a client sees it over the protocol."""
    is_error: bool
    structured: dict | None
    text: str

    @property
    def data(self) -> dict:
        """The tool's payload: structured content if present, else parsed text."""
        if self.structured is not None:
            return self.structured
        try:
            return json.loads(self.text)
        except (ValueError, TypeError):
            return {}


class MCPHarness:
    """Async context manager that launches the server and speaks to it as a client.

        async with MCPHarness(state_dir) as h:
            r = await h.call("run_qc", session_id=sid)
            assert not r.is_error
    """

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self._stdio_cm = None
        self._session_cm = None
        self.session: ClientSession | None = None

    async def __aenter__(self) -> "MCPHarness":
        env = dict(os.environ)
        env["DAM_MCP_STATE_DIR"] = str(self.state_dir)
        # The server resolves config/ and the QC script relative to its own file,
        # so cwd is irrelevant; dam_mcp is importable because it is installed.
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "dam_mcp.server"], env=env,
        )
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self.session = await self._session_cm.__aenter__()
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        with contextlib.suppress(Exception):
            await self._session_cm.__aexit__(*exc)
        with contextlib.suppress(Exception):
            await self._stdio_cm.__aexit__(*exc)

    # ── protocol calls ─────────────────────────────────────────────────────────

    async def call(self, name: str, /, **arguments) -> ToolResult:
        # `name` is positional-only so a tool argument literally called "name"
        # (load_experiment has one) lands in **arguments, not here.
        res = await self.session.call_tool(name, arguments)
        text = " ".join(
            c.text for c in res.content if getattr(c, "text", None)
        )
        return ToolResult(
            is_error=bool(res.isError),
            structured=res.structuredContent,
            text=text,
        )

    async def list_tools(self) -> list:
        return (await self.session.list_tools()).tools

    async def annotations(self) -> dict:
        """tool name -> its annotations model (or None)."""
        return {t.name: t.annotations for t in await self.list_tools()}

    async def read_resource(self, uri: str) -> str:
        res = await self.session.read_resource(AnyUrl(uri))
        return " ".join(
            c.text for c in res.contents if getattr(c, "text", None)
        )
