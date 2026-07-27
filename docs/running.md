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

## Declare your contrasts first — `DAM_CONTRASTS_PATH` is required

**There is no default contrast set, and this is deliberate.**

### What actually happens when it is unset

**The server still starts.** This is not a startup failure, and it must not look
like one — an MCP client showing the server as failed sends you hunting for a
broken server rather than a missing pre-registration. Verified end-to-end against
a real stdio server:

| | `DAM_CONTRASTS_PATH` unset |
|---|---|
| Server start, handshake, `tools/list` | **works** — all 14 tools register |
| `load_experiment`, `describe_experiment`, `run_qc`, `window_tradeoff`, `set_analysis_window` | **work** |
| `list_contrasts`, `run_contrast` | **refuse**, naming the variable and the template path |
| `assign_groups` | **refuses** — it validates group labels against the declared `groups:` |

In Claude Code or Claude Desktop the server shows as **Connected**. You can load
files and run QC. The refusal arrives the first time you try to group or contrast.

`assign_groups` needs the file because `groups:` is where the legal group labels
are declared — an undeclared label is refused rather than accepted as a new group.
It does **not** need any contrast to be declared.

Because `assign_groups` is where it stops, everything downstream of grouping stops
too: `compute_sleep`, `compute_activity`, `compute_rhythmicity`, `compute_survival`
and `run_contrast` all require groups, so in practice **the pipeline runs as far as
QC and then halts** until a real contrast file exists. That is the intended
behaviour, not a bug — but it is worth knowing the shape of it before you see it.

The refusal message names `DAM_CONTRASTS_PATH`, the template path it did *not*
load, and what to set. If you see it, you have a missing pre-registration, not a
broken install.

### What the file declares

The file's primary job is to declare the **experimental design**. Statistics are
optional.

```yaml
experiment: myexperiment-2026-07
groups: [mut, ctrl]           # REQUIRED — the legal group labels
# contrasts:                  # OPTIONAL — omit entirely if you test elsewhere
```

That is a complete, valid declaration. It loads, `list_contrasts` returns an empty
list (not an error), and the full **load → window → group → compute** pipeline
runs. If your statistics happen outside this tool — metrics exported to Prism, say
— this is the whole file you need.

Add `contrasts:` only if you want pre-registered comparisons executed *here*. Where
both are present, every label a contrast names must already be in `groups:`.

> **The `DAM_CONTRASTS_PATH` name is now slightly off** — the file's primary job is
> the design, not the tests. It is deliberately not renamed: an env var is a
> published interface and churning it would break every existing client config for
> a cosmetic gain. See `docs/HANDOFF-9`.

`config/contrasts.yaml` in this repo is a **template** with placeholder group
labels. It is not a pre-registration and it will not load — its `experiment:`
value does not match its filename, on purpose. Copy it:

```bash
cp config/contrasts.yaml config/contrasts-myexperiment-2026-07.yaml
# edit: set `experiment: myexperiment-2026-07` and the real group labels
git add config/contrasts-myexperiment-2026-07.yaml && git commit
```

Rules the loader enforces, all refusing rather than guessing:

* **`groups:` is required.** A file with contrasts but no `groups:` is refused, not
  repaired by deriving the labels from the contrasts — deriving would let a typo'd
  contrast label quietly become a legal group.
* **The filename must contain the `experiment:` value.** A file named `-young`
  declaring `-old` would make its own commit useless as a pre-registration
  record, and the workflow above — copy the previous timepoint and edit it — is
  exactly how that happens.
* **Contrast labels must be a subset of `groups:`**, checked at load.
* **`phase:` is `light` or `dark`; `metric:` and `test:` are closed sets too**,
  checked for every contrast at load. A typo used to resolve to `dark` silently
  and return a clean-looking result for the wrong half of the day.

Why no fallback: the commit that introduces a contrast file is that experiment's
pre-registration timestamp, and it is the only part of this gate a reviewer can
check independently. Silently loading a template would let an unregistered
comparison look registered. Breaking loudly once, at setup, is the cheaper
failure.

Point at the right set per run — one server can serve a whole timepoint series:

```bash
DAM_CONTRASTS_PATH=config/contrasts-myexperiment-2026-07.yaml python -m dam_mcp.server
```

`list_contrasts` returns the resolved `config_path`, so which set was live is
recoverable from the run's own output.

## Run the MCP server

```bash
DAM_CONTRASTS_PATH=config/contrasts-myexperiment-2026-07.yaml python -m dam_mcp.server
```

It speaks stdio (no OAuth — that is for remote servers). Point a client at that
command. The Phase-1 gate — passed — is a client you did not write driving a full
analysis:

```
MCP Inspector → command: python  args: -m dam_mcp.server
                env:     DAM_CONTRASTS_PATH=/abs/path/to/contrasts-myexp.yaml
```

then ask it to QC an experiment folder and compute night sleep by genotype.

### Claude Code / Claude Desktop

The environment matters here, because a client launches the server itself — an
`export` in your shell does not reach it. In Claude Code:

```bash
claude mcp add dam \
    --env DAM_CONTRASTS_PATH=/abs/path/to/config/contrasts-myexp.yaml \
    -- python -m dam_mcp.server
```

or, editing the JSON config directly (Claude Desktop's
`claude_desktop_config.json`, or `.mcp.json` in a project):

```json
{
  "mcpServers": {
    "dam": {
      "command": "python",
      "args": ["-m", "dam_mcp.server"],
      "env": {
        "DAM_CONTRASTS_PATH": "/abs/path/to/config/contrasts-myexp.yaml"
      }
    }
  }
}
```

Use an **absolute** path: the client's working directory is not necessarily the
repo root. If the server connects but every contrast tool refuses, the `env`
block is missing or the path is relative — the refusal message names the variable
and the path it looked at.

Two other variables belong in the same `env` block when you need them:
`DAM_MCP_STATE_DIR` (session state) and `DAM_MCP_AUDIT_LOG` (the audit stream).

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

The suite pins itself to `tests/fixtures/contrasts-testfixture.yaml` via an
autouse fixture, so it never reads whatever real pre-registration you have in
`config/`. That is deliberate: a test that goes red because someone declared
their actual experiment is testing the wrong thing.
