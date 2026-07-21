"""CLI to drive the agent end-to-end from a natural-language request.

    RTIVITY_PYTHON_PATH=/path/to/Rtivity-Python ANTHROPIC_API_KEY=... \
        python -m agent.run "QC the files in /data/exp_000 and compute night sleep by genotype"

This is the Phase-2 entry point. It needs the agent dependencies and an API key
(see graph.py). The MCP server it talks to needs nothing but the analysis engine.
"""

from __future__ import annotations

import asyncio
import sys

from .graph import build_agent


async def _main(query: str) -> int:
    agent = await build_agent()
    result = await agent.ainvoke({"messages": [("user", query)]})
    for message in result["messages"]:
        role = getattr(message, "type", "?")
        content = getattr(message, "content", message)
        print(f"\n[{role}]\n{content}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python -m agent.run "<request>"', file=sys.stderr)
        return 2
    return asyncio.run(_main(" ".join(sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
