"""Phase 3.5 — the agentic eval harness.

Layer 1 (`harness.py` + tests/test_contract.py): deterministic protocol contract
tests. A real MCP client drives the running server over stdio and asserts on the
responses — the Inspector session written down and re-runnable. No model in the
loop; this is the P1 acceptance suite and it belongs in CI.

Layer 2 (`trace.py`, `properties.py`, `run_agent_eval.py`): agentic behaviour
eval. The real agent runs on the real server; property assertions run *over the
trace* (tool order, boundary respect, recovery, grounding), scored across repeated
runs with variance, plus a cost/latency distribution. The LLM runs need an API key.
"""
