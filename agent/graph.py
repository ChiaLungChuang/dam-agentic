"""Build a LangGraph ReAct agent bound to the dam_mcp server over stdio.

Dependencies (Phase 2, not required for the MCP server itself):
    pip install langgraph langchain-anthropic langchain-mcp-adapters
    export ANTHROPIC_API_KEY=...
    export RTIVITY_PYTHON_PATH=/path/to/Rtivity-Python

All imports of those packages are inside build_agent(), so `import agent` stays
cheap and side-effect-free in an environment that only has the server.
"""

from __future__ import annotations

import os
import sys

from .prompts import SYSTEM_PROMPT

DEFAULT_MODEL = "claude-sonnet-4-5"


def _server_spec() -> dict:
    """stdio launch spec for the dam_mcp server. The server inherits
    RTIVITY_PYTHON_PATH and DAM_MCP_STATE_DIR from this process, so agent and
    server share session state and the same analysis engine."""
    return {
        "command": sys.executable,
        "args": ["-m", "dam_mcp.server"],
        "transport": "stdio",
        "env": dict(os.environ),
    }


async def build_agent(model: str | None = None):
    """Construct the agent and its MCP tools.

    Uses langchain-mcp-adapters' MultiServerMCPClient, which owns the stdio
    connection lifecycle for the loaded tools — no manual context juggling here.

    Usage:
        agent = await build_agent()
        result = await agent.ainvoke({"messages": [("user",
                 "QC the files in /data/exp_000 and compute night sleep by genotype")]})
    """
    from langchain_anthropic import ChatAnthropic
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langgraph.prebuilt import create_react_agent

    client = MultiServerMCPClient({"dam": _server_spec()})
    tools = await client.get_tools()

    llm = ChatAnthropic(model=model or DEFAULT_MODEL, temperature=0)
    return create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
