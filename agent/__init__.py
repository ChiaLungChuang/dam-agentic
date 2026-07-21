"""LangGraph agent (Phase 2) — orchestrates the dam_mcp tools; never computes.

Import is intentionally lazy: nothing at module load requires langgraph or an API
key, so this package imports cleanly in an environment that only has the MCP
server installed. The heavy dependencies are pulled in when you actually build the
graph. See build_agent() in graph.py.
"""

from .graph import build_agent  # noqa: F401
