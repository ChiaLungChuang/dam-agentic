"""The trace is the eval substrate.

Layer 2's scores are computed *over the trace*, not from a judge's opinion. A trace
is the ordered list of tool calls the agent made, each with its arguments and
whether it errored, plus the final answer and the token/latency cost.

`from_messages` extracts a trace from a LangGraph agent's message history. It is
deliberately duck-typed — it reads attributes off whatever message objects it is
given — so the property assertions in properties.py can be tested on hand-built
traces without LangChain or an API key in the loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)
    is_error: bool = False
    result_text: str = ""

    @property
    def result(self) -> dict:
        try:
            return json.loads(self.result_text)
        except (ValueError, TypeError):
            return {}


@dataclass
class Trace:
    task: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    final_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.calls]

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def first_index(self, name: str) -> int | None:
        for i, c in enumerate(self.calls):
            if c.name == name:
                return i
        return None

    def indices(self, predicate) -> list[int]:
        return [i for i, c in enumerate(self.calls) if predicate(c)]


def from_messages(task: str, messages: list, latency_s: float = 0.0) -> Trace:
    """Build a Trace from LangChain/LangGraph messages.

    Pairs each AI tool_call with its ToolMessage by id, reads error status and
    content, and sums token usage. Written against attributes (`.tool_calls`,
    `.content`, `.tool_call_id`, `.status`, `.usage_metadata`) so it does not
    import LangChain and does not care about the exact message classes.
    """
    pending: dict[str, dict] = {}          # tool_call_id -> {name, args}
    results: dict[str, dict] = {}          # tool_call_id -> {text, is_error}
    order: list[str] = []
    in_tok = out_tok = 0
    final_text = ""

    for msg in messages:
        usage = getattr(msg, "usage_metadata", None) or {}
        in_tok += int(usage.get("input_tokens", 0) or 0)
        out_tok += int(usage.get("output_tokens", 0) or 0)

        for tc in getattr(msg, "tool_calls", None) or []:
            tc_id = tc.get("id") or f"call-{len(order)}"
            pending[tc_id] = {"name": tc.get("name", "?"), "args": tc.get("args", {})}
            order.append(tc_id)

        tc_id = getattr(msg, "tool_call_id", None)
        if tc_id is not None:
            content = _as_text(getattr(msg, "content", ""))
            status = getattr(msg, "status", None)
            is_error = status == "error" or "Error executing tool" in content
            results[tc_id] = {"text": content, "is_error": is_error}

        if _is_final_ai(msg):
            final_text = _as_text(getattr(msg, "content", ""))

    calls = []
    for tc_id in order:
        p = pending[tc_id]
        r = results.get(tc_id, {"text": "", "is_error": False})
        calls.append(ToolCall(name=p["name"], args=p["args"],
                              is_error=r["is_error"], result_text=r["text"]))

    return Trace(task=task, calls=calls, final_text=final_text,
                 input_tokens=in_tok, output_tokens=out_tok, latency_s=latency_s)


def _as_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def _is_final_ai(msg) -> bool:
    is_ai = getattr(msg, "type", None) == "ai" or msg.__class__.__name__ == "AIMessage"
    return is_ai and not (getattr(msg, "tool_calls", None) or [])
