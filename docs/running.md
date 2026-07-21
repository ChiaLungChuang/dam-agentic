# Running the ecosystem

How to run what was built on top of the tested analysis engine: the MCP server
(`dam_mcp/`) and the LangGraph agent (`agent/`). This document is operational; the
design rationale is in [`mcp-spec.md`](mcp-spec.md) and `CLAUDE.md`.

## What is built

| Layer | Location | State |
|---|---|---|
| MCP server — 12 tools, 4 resources | `dam_mcp/` | Implemented, tested |
| Session state + disk persistence | `dam_mcp/sessions.py` | Implemented, tested |
| Compute layer over Rtivity-Python | `dam_mcp/engine.py` | Implemented, tested |
| Typed returns | `dam_mcp/schemas.py` | Implemented, tested |
| Pre-declared contrasts (read-only) | `dam_mcp/config.py` | Implemented |
| Report renderer | `dam_mcp/report.py` | Implemented, tested |
| LangGraph agent (Phase 2) | `agent/` | Implemented, not yet run |

The tools return **summaries and a `session_id` handle** — never activity counts.
The compute layer is the only code that touches raw data, and it hands back
aggregate rows (n, mean, SD, effect size). The verified boundary: fewer than a few
hundred numbers cross back per compute call, against the ~270k activity samples
behind them.

## Install

The maths lives in Rtivity-Python (Silva et al., *Sci Rep* 2022; this repo did the
Python rewrite and the agentic layer, not the original methods). It is a proper
package now — install it, don't point a path env var at it:

```bash
pip install -e /path/to/Rtivity-Python        # the analysis engine (editable)
pip install -e ".[dev]"                       # this repo + test deps
# or, to pull the engine from git in one step:
pip install -e ".[dev,engine]"
```

Structural tools (`load_experiment`, `describe_experiment`, `run_qc`) wrap
`validate_dam.py` and work even without the engine; `compute_*` and `run_contrast`
need it. If it is missing, those tools return an actionable message telling you to
install it — no path env var stands in for packaging.

Session state is written under `~/.dam_mcp/sessions` by default; override with
`DAM_MCP_STATE_DIR`.

## Run the MCP server

```bash
python -m dam_mcp.server
```

It speaks stdio (no OAuth — that is for remote servers). Point a client at that
command. The Phase-1 gate — passed — is a client you did not write driving a full
analysis:

```
MCP Inspector → command: python  args: -m dam_mcp.server
```

then ask it to QC an experiment folder and compute night sleep by genotype.

## Run the agent (Phase 2)

```bash
pip install -e ".[agent]"
ANTHROPIC_API_KEY=... python -m agent.run \
    "QC the files in /data/exp_000 and compute night sleep by genotype"
```

## Eval harness (Phase 3.5)

`evals/` is the agentic eval harness — the Inspector session made re-runnable. Layer
1 (`tests/test_contract.py`) drives the server over stdio and asserts the P1
contract; Layer 2 (`evals/`, `tests/test_properties.py`) scores the agent's
behaviour over its trace. See [`../evals/README.md`](../evals/README.md).

```bash
pytest tests/test_contract.py tests/test_properties.py -q     # Layer 1 + scorers
python -m evals.run_agent_eval --data /path/to/experiment --runs 5   # Layer 2 (needs a key)
```

## Tests

```bash
pytest                       # MCP-layer tests; mcp/yaml/engine tests skip if absent
```

The session, schema, QC-wrapping, and contrast-math tests run against a synthetic
corpus generated once per session. The server and tool tests need `mcp`; the
config test needs `pyyaml`; the compute tests need the analysis engine installed.
Each skips cleanly rather than failing when its dependency is absent.
