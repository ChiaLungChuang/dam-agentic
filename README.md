# dam-agentic

An agentic QC and analysis system over TriKinetics Drosophila Activity Monitor
(DAM) data, in which an LLM orchestrates tested analysis functions rather than
performing the analysis. The model never computes a result and never sees raw
data: every tool returns a summary plus a session handle, and activity counts stay
server-side in Python. Fewer than a few hundred numbers cross back per `compute_*`
call, against roughly 270,000 activity samples behind them.

---

## The finding that shaped the design

The agent evaluation scored three separate infrastructure faults — a provider rate
limit, an authorization failure, and a dead model endpoint — as **perfect runs**.

None of the three had anything to do with the agent. The harness could not
distinguish a model that solved the task from a model that never executed: a run
that dies before making a tool call violates no rail, and every "if X happened, Y
must have happened first" property passes vacuously when X never happened. The
scores were arithmetically correct and meant nothing.

What changed as a result, and why the rest of this repo looks the way it does:

- **Infrastructure failures abort the eval** rather than entering the dataset. A
  429 measures the billing tier, not the agent. Three exception types are
  allowlisted as agent-behaviour failures; everything else raises `EvalAborted`
  and stops the run.
- **A zero-tool-call trace is a crash, not a score.** It is counted with its cause
  and excluded from every metric.
- **Unattempted runs are excluded from the denominator.** In the red-team suite
  this is explicit: outcomes are `repelled` / `succeeded` / `not_attempted`, and
  the third is not a defence.
- **An all-unattempted report reads `NO DATA`, never `1.000`.** Zero completed
  runs is an absence of measurement, not a perfect score.
- **The recursion budget derives from a measured floor.** `RECURSION_LIMIT =
  MEASURED_STEP_FLOOR (10) × 3`, where the floor is the smallest limit at which a
  known-good scripted trajectory completes. The previous literal happened to be
  exactly the floor for the trajectory being attempted, so the eval had been
  scoring its own leash as agent behaviour.

Detail: [`docs/HANDOFF-5-harness-honesty.md`](docs/HANDOFF-5-harness-honesty.md),
[`docs/phase0-eval-report.md`](docs/phase0-eval-report.md).

---

## Architecture

**MCP server** (`dam_mcp/`) — 14 tools and 4 resources over JSON-RPC/stdio.
Session state is persisted to disk, so a server restart does not destroy an hour
of work. Tools carry MCP annotations (`readOnlyHint`, `destructiveHint`) so the
human-in-the-loop boundary is visible at the protocol layer, where clients
actually gate. Typed pydantic returns make the data boundary a validation error
rather than a convention: no field anywhere accepts a bare list of numbers, so a
raw series cannot be returned even by mistake.

Driven end-to-end by MCP Inspector, Claude Code, a scripted client, and a live
LLM.

**Agent harness** (`agent/`) — LangGraph, with explicit tool dispatch, stop
conditions, and checkpointed session state. A provider seam accepts a scripted
model in place of a real one, which is what makes the eval controls keyless.

**Observability** (`dam_mcp/observability.py`, `dam_mcp/audit.py`) — OpenTelemetry
spans and a separate, stdlib-only audit record per tool call, produced by one
instrumentation pass over the single dispatch chokepoint. Every record carries a
server-stamped `run_id`, so a reported result is traceable to the run and session
that produced it. The id is read from the environment at dispatch and is never a
tool argument, so the model cannot label its own audit trail.

---

## Pre-registration

A declaration file names the legal group labels and, optionally, pre-registered
contrasts.

```yaml
experiment: myexperiment-2026-07
groups: [mut, ctrl]      # required — the labels assign_groups is checked against
# contrasts:             # optional — omit if statistics happen outside this tool
```

`assign_groups` refuses any label the declaration does not name. Where contrasts
are declared, the agent may run any of them and cannot invent one — an agent free
to search metrics × groups × phases for significance will find some, every time.

**The commit that adds the file is the timestamp, and it is the only part of the
mechanism a reviewer can verify independently.** Everything else is the system
asserting things about itself. There is deliberately no default declaration path:
an unset `DAM_PREREG_PATH` refuses loudly rather than silently loading a template.

---

## Red-teaming

Six adversarial evaluation tasks, each asserted to fail as an attack: unauthorized
computation, precondition bypass, pre-registration bypass, scope escape, prompt
injection through tool output, and warning suppression.

**One attack succeeded on the first run.** `render_report` accepted any path and
would overwrite the live pre-registration file — an arbitrary file write at
process permissions, reaching the audit log and session state as easily as the
declaration. Closed by confining writes to a report root, with containment checked
*after* `resolve()`, so `..` traversal and symlink escapes are caught where a
string-prefix check would not be.

The suite found a real hole in its own system on first execution. That is the
point of it: a red-team suite that only confirms the boundaries you already trust
has not been run against anything.

Two further defects fell out as collateral, including a class of raw parser
exceptions escaping the error contract and being audited as server faults rather
than as refusals. Detail:
[`docs/HANDOFF-10-redteam-findings.md`](docs/HANDOFF-10-redteam-findings.md).

---

## What the first real run exposed

Twelve raw monitor files, 384 channels, 161.8 hours. **No results are reported
here** — the sleep units are unverified and the design is confounded with monitor.
What follows is what the tooling caught, and every item is a defect in the tooling
found by using it:

- **`window_tradeoff` is non-monotonic.** Each row re-classifies the whole
  inventory over `[start, end]` rather than carrying deaths forward, so every row
  sums to 384 and `n_died` means "would be called dead if recording stopped here".
  The intermediate rows cannot be used to choose a window.
- **The death rule cannot distinguish sleep from death.** `death_hours` defaults
  to 12 and the light-dark cycle is 12:12, so the trailing-zero threshold is
  exactly the dark phase. A fly quiescent through one night meets the death rule.
- **`compute_*` runs silently with QC decisions outstanding**, so dead animals
  score as perfect sleepers across their trailing zeros, and the flags are
  lopsided by group.
- **Latency was reported without its denominator** — defined for between 18 and 71
  of 96 flies depending on group, with the largest apparent effect sitting on the
  smallest n. A selection effect that would otherwise have become the headline
  result.

The synthetic evaluation corpus could not reach any of these: each needs a real
light-dark cycle, real mortality, or real per-monitor clocks. A generator that
plants the ground truth also supplies the context that makes a number
interpretable, and missing context is exactly what these defects are.

---

## What is not built

- **No OAuth 2.1 and no HTTP transport.** stdio only. Tool scopes and an
  authenticated principal are designed, not implemented; the audit record's
  `principal` field is a placeholder.
- **No A2A.**
- **No private or on-prem inference.** The provider seam exists and has never been
  exercised end-to-end against a local model.
- **No LLM-as-judge for behaviour.** Layer 2 scores traces with deterministic
  properties, because "did it call `assign_groups` before `compute_sleep`" is a
  fact about the trace rather than a matter of opinion, and a judge introduces the
  variance it is being used to measure. A constrained judge grades only the final
  report's prose, against an explicit rubric.
- **No RAG and no vector search.** Question answering reads computed MCP resources
  — manifest, QC report, metrics — never raw data and never a retrieved document.
- **The model-behaviour attacks are verified only at the detector level.** All
  end-to-end red-team tests drive a scripted client, so what is established is that
  the *server* boundary holds against a hostile call sequence. Three of the six
  attacks are entirely about what a real model chooses to do, and for those the
  suite currently proves only that the detectors fire correctly.

---

## Quickstart

```bash
pip install -e ".[dev,engine,agent]"
```

The analysis engine (`rtivity-python`) is a git dependency under `[engine]`.
Without it, loading and QC work; `compute_*` and `run_contrast` do not.

A declaration is required — there is no default. The smallest valid one:

```bash
cat > config/contrasts-demo.yaml <<'YAML'
experiment: demo
groups: [mut, ctrl]
YAML

export DAM_PREREG_PATH=config/contrasts-demo.yaml
export DAM_MCP_STATE_DIR=/tmp/dam-scratch
python -m dam_mcp.server
```

That is complete: `list_contrasts` returns an empty list rather than an error, and
`load → window → group → compute` runs. The filename must contain the
`experiment:` value, so a file cannot be named for one experiment while declaring
another.

Point any MCP client at `python -m dam_mcp.server`. For Claude Code and Claude
Desktop the variables go in the client's `env` block — the client launches the
server, so a shell `export` does not reach it. See
[`docs/running.md`](docs/running.md).

```bash
pytest -q          # 260 passed, 0 skipped, 260 collected
ruff check .
```

CI runs the full suite on Python 3.11, 3.12 and 3.13 with the engine installed,
plus a separate lint job and a deterministic detector-scoring job over a seeded
synthetic corpus.

---

## Layout

```
dam_mcp/          MCP server — tools, resources, schemas, audit, observability
agent/            LangGraph harness and provider seam
evals/            Layer 1 protocol contract, Layer 2 trace scoring, red team
damsim/           Synthetic corpus generator + per-defect scorer
skills/dam-qc/    The QC detector
config/           Declaration files; the committed one is the pre-registration
docs/             Handoffs — the reasoning, including what was found and not fixed
```

Scores from `damsim` are reported per defect class. A single aggregate hides which
failure you have, and which failure you have is the only useful thing.

### Known limitations, stated

- **Late deaths.** A trailing-zero death window cannot detect a fly dying inside
  that final window. Structural, not a bug — and see the first-real-run section
  above for why the default value of that window is itself a defect on a 12:12
  cycle.
- **Single-beam blindness.** Zero counts mean no midline crossing. A fly active at
  one end of the tube scores zero. A hardware bound that sets the floor on what any
  QC can see.

## Provenance

The analysis maths is **Rtivity**, published software by Silva et al., *Sci Rep* 12
(2022), doi:10.1038/s41598-022-08195-z, from the Oliveira lab, built on the
Rethomics framework (Geissmann et al., *PLoS ONE* 2019). This repository's
contribution is the repair of that package (an archived dependency removed), its
first version control, the Python rewrite with tests and CI, and the agentic layer
described above. It does not claim authorship of Rtivity or of its methods.
