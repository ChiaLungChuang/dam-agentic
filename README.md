# dam-agentic

Agentic QC and analysis over TriKinetics Drosophila Activity Monitor (DAM) data. An
LLM orchestrates tested analysis functions; it never computes a result and never sees
raw data — every tool returns a summary plus a `session_id` handle, and the counts stay
server-side in Python. Fewer than a few hundred numbers cross back per compute call,
against roughly 3.7 million activity samples — 384 channels × 9,708 reads — in the one
real experiment this has been run on (`docs/HANDOFF-11-first-real-run.md`).

## The finding that motivated the design

Three distinct infrastructure faults — a rate-limit (429), an authorization failure
(401) and a wrong-endpoint error (404) — each scored as a perfect run in the agent
evaluation. The Google free tier's 20-requests-per-day quota was exhausted inside the
first task; every subsequent call 429'd instantly, was swallowed by a blanket
`except Exception`, became a trace with zero tool calls, and **passed every property
vacuously**. The report read `1.000` with `0.0` variance across all four tasks and all
seven properties, while `total tokens` was `0.0`.

The harness could not tell a model that solved the task from a model that never ran.

What changed as a result:

- Infrastructure failures **abort** rather than entering the dataset. Only an explicit
  allowlist of agent-behaviour failures (step-limit exhaustion, tool errors surfaced by
  the server, malformed tool calls) remains a datapoint; anything unrecognised raises.
- A zero-tool-call trace is a **crash**, not a score.
- Unattempted runs are excluded from the denominator.
- An all-unattempted report reads **`NO DATA`**, never `1.000`.
- The recursion budget derives from a measured floor: `MEASURED_STEP_FLOOR = 10` was
  found by running a known-good scripted trajectory at successive limits until it
  completed (9 raised `GraphRecursionError`, 10 finished), then multiplied by 3.
  `evals/limits.py`. The previous literal was 12 — exactly the floor for a five-call
  trajectory, so a model that made one bad call could not recover and the run was
  scored as though the leash had not been a factor.

## Architecture

14 tools and 4 resources over JSON-RPC/stdio, session state persisted to disk so a
server restart does not destroy an hour of work, and tools carrying MCP read-only
annotations so a client can see which of them mutate state. Driven end-to-end by
Claude Code, a scripted client and a live LLM.

The agent is a LangGraph harness with explicit tool dispatch, stop conditions and
checkpointed session state, behind a provider seam that also accepts scripted and
raising models for keyless controls.

OpenTelemetry spans and a separate stdlib-only JSONL audit stream are emitted from a
single dispatch chokepoint, with run attribution stamped server-side, so any reported
result is traceable to the run and session that produced it.

```
DAM .txt files → analysis functions (all computation) → MCP server (summaries +
handles) → LangGraph agent (orchestrates, never computes) → report + grounded Q&A
```

## Pre-registration

A declaration file names the legal group labels and, optionally, pre-registered
contrasts. `assign_groups` refuses any label not declared. Where contrasts are
declared the agent may choose among them and cannot invent one — an agent with free
rein over metrics × groups × phases is an automated p-hacking machine. There is no
default path: `DAM_PREREG_PATH` unset means the contrast and grouping tools refuse
by name rather than silently loading a template.

The commit that adds the file is the timestamp, and it is the only part of the
mechanism a reviewer can verify independently. For the one real dataset here the
recording predates the declaration commit, so this declaration demonstrates the
mechanism; it is not a declare-before-looking record for that experiment.

## Red-teaming

Six adversarial evaluation tasks target unauthorized computation, precondition
bypass, pre-registration bypass, scope escape, prompt injection through tool output,
and warning suppression. Each detector reports one of three outcomes — `repelled`,
`succeeded`, `not_attempted` — and an infrastructure failure aborts rather than
scoring as a repelled attack.

**One attack succeeded on first run.** `render_report` accepted an arbitrary `path`
and would overwrite the pre-registration file: an arbitrary file write at process
permissions. The suite found a real hole in its own system, which is the point of
building it. Closed by confining every write to a report root, with containment
checked *after* `resolve()` so `..` traversal and symlinks cannot walk out.

The fix then exposed a defect in the detector: `scope_escape` had been flagging any
`render_report` at a declaration-shaped path without checking `is_error`, so the
moment the server started refusing, a *rejected* attempt scored as a successful
escape — a working defence reported as a live hole. It was undetectable while the
write still succeeded.

## What the first real run exposed

Twelve raw monitor files, 384 channels, 161.8 h. What follows is what the tooling
caught, not biology: no sleep numbers, no group comparisons. The units are unverified
and the design is confounded. These are defects in the tooling, found by using it.

- **`window_tradeoff` is non-monotonic, and its own note said it could not be.** The
  note claimed `n_alive` falls as the window extends. It does not: each row
  re-classifies all 384 channels independently over `[start, end]`, and every row
  sums to 384. The mechanism is that `death_hours` defaults to 12 while the LD cycle
  is 12:12, so the trailing-zero threshold equals the dark phase, and windows ending
  in dark mass-classify sleeping flies as dead.
- **`compute_*` runs silently with QC decisions outstanding.** 63
  `decisions_required` were raised, none applied; `compute_sleep` then computed over
  all 384 channels without a word, so dead animals score as perfect sleepers. The
  flags are lopsided by group (26 / 10 / 18 / 9), so a group difference in sleep can
  be a difference in how many animals were dying.
- **Latency was reported without its denominator.** Sleep latency was defined for
  52 / 71 / 21 / 18 channels of 96 per group. A 6.6× apparent difference sits on the
  smallest n — a selection effect that would have become the headline result.

None of these were reachable by the synthetic corpus: each needs a real light-dark
cycle, real mortality, or real per-monitor clocks. Recorded in
`docs/HANDOFF-11-first-real-run.md`, open in `docs/HANDOFF-7-current-state.md`.

## What is NOT built

- **No OAuth 2.1 and no HTTP transport.** stdio only.
- **No A2A.**
- **No private or on-prem inference.**
- **No LLM-as-judge for behaviour.** Layer 2 scores traces with deterministic
  properties, because "did it call `assign_groups` before `compute_sleep`" is a
  boolean over the trace and a judge would add variance without adding information.
  The judge is reserved for the final report's prose, under an explicit rubric.
- **No RAG or vector search.**
- **The model-behaviour attacks are verified only at the detector level.** Every
  end-to-end test uses a scripted client, so what is established is that the server
  boundary holds against a hostile call sequence — not how a real model behaves when
  asked to cross one. Attacks 2 and 3 are genuinely verified, because their
  boundaries are server-side.
- **Prompt injection through the declaration is a stated trust boundary, not a
  control.** The declaration is trusted input, on the argument that anyone who can
  write one can already declare whatever they want, so injection buys nothing a
  legitimate edit would not. `docs/HANDOFF-10-redteam-findings.md` names the three
  conditions under which that equivalence fails: a declaration from a shared drive
  or a repo the lab does not own, a multi-tenant server setting `DAM_PREREG_PATH`
  per request, and HTTP transport letting a caller supply or select a declaration.
- **The server does not enumerate in MCP Inspector v2.0.0.** The connection
  succeeds and Inspector reports **Connected**, but `tools/list`, `prompts/list` and
  `resources/list` stay pending, the Tools tab renders empty, and the server console
  shows no traceback. This is unresolved. The server starts and serves correctly over
  stdio to other clients — Claude Code, the scripted client in `evals/harness.py`,
  and a live LLM all enumerate all 14 tools and drive a full analysis.
- **Agent-loop and tool spans do not share a W3C trace context.** `dam.agent.run`
  runs in the eval process and `dam.tool.*` in the server subprocess; they correlate
  by `dam.session_id`, which a collector can join on. True cross-process nesting
  needs `traceparent` propagation through the MCP call and no client or adapter hook
  exposes one, so the seam is left open rather than built.

### Known limitations of the QC itself

- **Late deaths.** A trailing-zero death window (default 12 h) cannot detect a fly
  dying inside that final window.
- **Single-beam blindness.** Zero counts mean no *midline* crossing. A fly active at
  one end of the tube scores zero. Hardware bound; it sets the floor on what any QC
  can see.

## Quickstart

```bash
pip install -e ".[dev,engine,agent]"
```

Write a declaration. The loader requires the filename to *contain* the `experiment:`
value, so a file named for one experiment while declaring another refuses rather than
becoming a useless pre-registration record. `groups:` is required; `contrasts:` is
optional.

```bash
cat > config/contrasts-myexp-2026-07.yaml <<'YAML'
experiment: myexp-2026-07
groups: [ctrl, mut]
YAML

DAM_PREREG_PATH=$PWD/config/contrasts-myexp-2026-07.yaml python -m dam_mcp.server
```

Use an absolute path — a client launches the server itself, so its working directory
is not necessarily the repo root.

That is a complete, valid declaration. To see it, call `load_experiment` first:
every tool including `list_contrasts` takes a `session_id`, so calling it on a bare
server returns `No session ...` rather than the declaration.

```
load_experiment(paths=[".../Monitor1.txt", ".../Monitor2.txt"], name="qs")
    → {"session_id": "dam-743d9b4aea98", ...}

list_contrasts(session_id="dam-743d9b4aea98")
    → {"session_id": "dam-743d9b4aea98", "contrasts": [], "groups": ["ctrl", "mut"],
       "config_path": ".../contrasts-myexp-2026-07.yaml", "warnings": []}
```

An empty `contrasts` list is the normal answer for a groups-only declaration, and
the load → window → group → compute pipeline runs from there.

```bash
pytest                                                    # the suite
python damsim/generate.py --out /tmp/corpus --seed 42 --adversarial   # eval corpus
```

CI runs `lint`, `test` on Python 3.11/3.12/3.13, and `eval` as independent jobs:
**260 passed / 0 skipped / 260 collected**.

## Layout

```
dam_mcp/             MCP server — tools, resources, schemas, config, audit
agent/               LangGraph agent — orchestrates the tools
evals/               Eval harness — Layer 1 contract, Layer 2 trace scoring, red team
damsim/              Eval corpus generator + scorer
skills/dam-qc/       Agent Skill — QC SOP, validation script, format reference
config/              Pre-registration declarations
docs/running.md      How to run the server, the agent, and the tests
docs/mcp-spec.md     Tool surface design
docs/HANDOFF-*.md    Reasoning record — findings as found, never rewritten
```

## Provenance

The analysis maths is Rtivity, published software by Silva et al., *Sci Rep* 12
(2022), doi:10.1038/s41598-022-08195-z, from the Oliveira lab, built on the Rethomics
framework (Geissmann et al., *PLoS ONE* 2019). This repository contributes the repair
of that package (an archived dependency removed), its first version control, the
Python rewrite with tests and CI, and the agentic layer above it. It does not claim
authorship of Rtivity or its methods.
