"""CLI to drive the agent end-to-end from a natural-language request.

    ANTHROPIC_API_KEY=... python -m agent.run "QC /data/exp_000 and compute night sleep"
    GOOGLE_API_KEY=...    python -m agent.run --provider google "…"   # free Gemini tier

This is the Phase-2 entry point. It needs the agent dependencies (`pip install -e
".[agent]"`) and a provider key. The MCP server it talks to needs nothing but the
analysis engine.
"""

from __future__ import annotations

import argparse
import asyncio

from evals.limits import RECURSION_LIMIT

from .graph import build_agent


async def _main(query: str, provider: str, model: str | None) -> int:
    agent = await build_agent(model=model, provider=provider)
    result = await agent.ainvoke(
        {"messages": [("user", query)]},
        config={"recursion_limit": RECURSION_LIMIT},
    )
    for message in result["messages"]:
        role = getattr(message, "type", "?")
        content = getattr(message, "content", message)
        print(f"\n[{role}]\n{content}")
    return 0


def main() -> int:
    from .graph import load_env
    load_env()          # .env -> environment before any provider client is built
    ap = argparse.ArgumentParser(description="Drive the DAM agent")
    ap.add_argument("request", help="natural-language task for the agent")
    ap.add_argument("--provider", default="anthropic",
                    choices=["anthropic", "google", "ollama"])
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    return asyncio.run(_main(args.request, args.provider, args.model))


if __name__ == "__main__":
    raise SystemExit(main())
