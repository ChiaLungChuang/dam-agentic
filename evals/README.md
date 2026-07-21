# Phase 3.5 — agentic eval harness

The Inspector session that found the P1 bugs, **written down and re-runnable**. It
converts a manual, one-time poke-and-see into an automated regression suite. Two
layers that answer different questions.

## Layer 1 — protocol contract tests (deterministic, no LLM)

A real MCP client drives the running server over stdio and asserts on the
responses. No model in the loop — fast, free, CI-runnable. This is the acceptance
suite for the P1 fixes: each scenario is a bug that thirty unit tests missed and
the Inspector caught.

- `harness.py` — `MCPHarness`, launches `python -m dam_mcp.server` and speaks
  JSON-RPC to it (`call`, `list_tools`, `annotations`, `read_resource`).
- `../tests/test_contract.py` — the 8-scenario table as assertions. Runs in CI.

```bash
pytest tests/test_contract.py -q
```

| Scenario | Contract asserted |
|---|---|
| compute before QC / groups | refuses; error names the fix |
| `apply_exclusions(confirm=false)` | `applied:false`, previews Δn |
| `run_contrast` undeclared id | refuses; enumerates legal ids |
| malformed exclusion | handled error, no traceback |
| read-only tools | `annotations.readOnlyHint == true` |
| every error | `isError` set consistently |
| labels not in config | `assign_groups` refuses |
| any `compute_*` result | no array-shaped field in payload |

## Layer 2 — agentic behaviour eval (LLM in the loop)

The real agent on the real server, scored on what the model *chooses* to do —
tool order, boundary respect, recovery, grounding — computed **over the trace**,
not by a judge. Scored n≥5 times per task; the report is a distribution with
variance, never a point estimate.

- `trace.py` — normalise a trace from the agent's messages (tool calls, errors,
  tokens, latency). Duck-typed, so the scorers test without LangChain.
- `properties.py` — the property assertions (`load_first`, `qc_before_metrics`,
  `groups_before_metrics`, `exclusions_previewed`, `contrasts_within_policy`,
  `recovered_not_looped`, and a grounding heuristic). Pure functions.
- `scoring.py` — aggregate across runs: tool-sequence accuracy, boundary-violation
  rate, recovery rate, cost/latency distribution, each with spread.
- `../tests/test_properties.py` — the scorers tested on hand-built traces (CI, no key).
- `run_agent_eval.py` — runs the agent, extracts traces, scores, and optionally
  grades report prose with a **constrained** judge (explicit rubric).

```bash
pip install -e ".[agent]" && export ANTHROPIC_API_KEY=...
python -m evals.run_agent_eval --data /path/to/experiment --runs 5 --out report.md
python -m evals.run_agent_eval --synthetic --runs 5 --judge     # smoke corpus
```

## Design notes

- **Property assertions, not LLM-as-judge.** "Did it call `assign_groups` before
  `compute_sleep`" is a boolean over the trace. The judge is reserved for the one
  thing that needs it — the final report's prose — with an explicit rubric.
- **The trace is the substrate.** Layer 2's scores are read from the trace, so
  tracing isn't decoration; it's what the numbers are computed from.
- **Anchored to a real system.** The tasks run against the real server with real
  rails. The harness earns its place only if what it measures is real.
