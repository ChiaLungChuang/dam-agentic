"""A scripted, deterministic chat model for testing the agent harness offline.

It emits a fixed sequence of AIMessages so a ReAct agent's tool-call order is under
test control — no network, no key, no sampling. The real MCP server still runs, so
the stdio transport, the real tool schemas, and trace extraction are all exercised;
only the model is faked. This is what lets the property scorers be tested for their
ability to FAIL, not merely to pass (docs/HANDOFF-4 Task 2).

Inject with build_agent(llm=ScriptedModel(script=[...])).
"""

from __future__ import annotations

import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict

# Placeholder a script writes for the session handle; filled in at run time from
# the latest tool result, since load_experiment mints the real id dynamically.
PENDING_SID = "__PENDING_SID__"


def tool_step(name: str, /, **args) -> AIMessage:
    """One scripted turn that calls `name` with `args`. `name` is positional-only so
    a tool argument literally called "name" (load_experiment has one) lands in args.
    Pass session_id=PENDING_SID for any call after load_experiment; it is
    substituted with the live id."""
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": dict(args), "id": f"call_{name}", "type": "tool_call",
    }])


def final(text: str) -> AIMessage:
    """The closing turn: no tool calls, so the ReAct loop stops."""
    return AIMessage(content=text)


def _content_text(content) -> str:
    """MCP tool results arrive as a list of content blocks, not a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    return str(content)


def _latest_session_id(messages) -> str | None:
    for m in reversed(messages):
        text = _content_text(getattr(m, "content", ""))
        if "session_id" not in text:
            continue
        try:
            data = json.loads(text)
            if isinstance(data, dict) and data.get("session_id"):
                return data["session_id"]
        except ValueError:
            match = re.search(r'"session_id"\s*:\s*"([^"]+)"', text)
            if match:
                return match.group(1)
    return None


def _fill_session_id(msg: AIMessage, sid: str | None) -> AIMessage:
    if not sid or not msg.tool_calls:
        return msg
    new_calls = []
    for tc in msg.tool_calls:
        args = dict(tc["args"])
        if "session_id" in args:
            args["session_id"] = sid
        new_calls.append({**tc, "args": args})
    return AIMessage(content=msg.content, tool_calls=new_calls)


def _unique_ids(msg: AIMessage, idx: int) -> AIMessage:
    """Give each tool call a per-turn-unique id. Two calls to the same tool would
    otherwise share an id and collapse in trace extraction (which keys by id), as a
    real model's uuids never would."""
    if not msg.tool_calls:
        return msg
    new_calls = [{**tc, "id": f"{tc['id']}_{idx}_{j}"}
                 for j, tc in enumerate(msg.tool_calls)]
    return AIMessage(content=msg.content, tool_calls=new_calls)


class ScriptedModel(BaseChatModel):
    """Returns script[k] on the k-th call, where k = AIMessages already in the
    conversation. Stateless, so LangGraph's message threading and any re-invocation
    just work. bind_tools is a no-op — the scripted tool_calls name tools directly.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    script: list

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        idx = sum(1 for m in messages if isinstance(m, AIMessage))
        msg = self.script[idx] if idx < len(self.script) else final("done")
        msg = _fill_session_id(msg, _latest_session_id(messages))
        msg = _unique_ids(msg, idx)
        return ChatResult(generations=[ChatGeneration(message=msg)])


class RaisingModel(BaseChatModel):
    """Raises a fixed exception on first call — a keyless stand-in for a provider
    429/auth/connection failure, so the eval's abort path can be tested without
    burning quota or touching the network (HANDOFF-5 Task 5)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    exc: Exception

    @property
    def _llm_type(self) -> str:
        return "raising"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise self.exc
