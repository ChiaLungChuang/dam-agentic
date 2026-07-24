"""Build a LangGraph ReAct agent bound to the dam_mcp server over stdio.

Dependencies (Phase 2, not required for the MCP server itself):
    pip install -e ".[agent]"
    export ANTHROPIC_API_KEY=...   # or GOOGLE_API_KEY for --provider google

All heavy imports are inside build_agent(), so `import agent` stays cheap and
side-effect-free in an environment that only has the server.

Three execution lanes share this one builder (see docs/HANDOFF-4):
  * a real provider (anthropic / google / ollama) — model behaviour;
  * an injected `llm=` (a scripted fake) — tests of *our* harness, deterministic
    and offline, with the real MCP server still running over stdio.
The `llm=` escape hatch bypasses all provider logic, which is what makes the
fake-model controls possible.
"""

from __future__ import annotations

import os
import sys

from .prompts import SYSTEM_PROMPT

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "google": "gemini-2.5-flash",
    "ollama": "qwen3",
}


def resolved_model(provider: str, model: str | None) -> str:
    """The model id that will actually be used — recorded in every eval report."""
    return model or DEFAULT_MODELS.get(provider, "unknown")


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


def _inject_truststore() -> None:
    """Verify TLS against the OS trust store rather than certifi's bundle.

    On a machine behind institutional TLS inspection, the internal root CA is in
    the OS keychain but not in certifi, so any provider SDK's outbound HTTPS fails
    with CERTIFICATE_VERIFY_FAILED. truststore fixes it WITHOUT weakening
    verification (never verify=False). No-op if truststore is absent — the fake
    path makes no network call, so it does not need it.
    """
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


def make_llm(provider: str, model: str | None):
    """Instantiate a chat model for a provider. Imports are local so a server-only
    environment never needs the provider SDKs."""
    name = resolved_model(provider, model)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=name, temperature=0)
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=name, temperature=0)
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=name, temperature=0)
    raise ValueError(
        f"Unknown provider '{provider}'. Use 'anthropic', 'google', or 'ollama', "
        "or pass llm= to inject a model directly (e.g. a scripted fake for tests)."
    )


async def build_agent(model: str | None = None, provider: str = "anthropic", llm=None):
    """Construct the ReAct agent and its MCP tools over stdio.

    `provider` selects the model family; `model` overrides the default id; `llm`
    injects a ready-made model and bypasses provider logic entirely (used by the
    fake-model tests). temperature=0 on every real provider.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langgraph.prebuilt import create_react_agent

    if llm is None:
        _inject_truststore()
        llm = make_llm(provider, model)

    client = MultiServerMCPClient({"dam": _server_spec()})
    tools = await client.get_tools()
    return create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
