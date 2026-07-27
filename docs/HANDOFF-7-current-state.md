# HANDOFF-7 — current state

**Written:** 2026-07-25 · **At commit:** `9d7f561` · **Branch:** `main` (pushed to
`origin`, https://github.com/ChiaLungChuang/dam-agentic)

For a session with no memory of the work that produced this. Read `CLAUDE.md`
first — it holds the architectural rails and is the document that must not be
contradicted.

Unlike its predecessors this handoff is not a plan: HANDOFF-3 through
HANDOFF-6 each set out work to do, and this one records where that work
actually ended up. The earlier documents (`HANDOFF-3` … `HANDOFF-6` +
`HANDOFF-6-amendment-1`) remain worth reading for the reasoning behind
decisions only summarised here.

---

## Status in one paragraph

The MCP server (`dam_mcp/`) is complete and operated: 14 tools, 4 resources,
driven end-to-end by MCP Inspector, by Claude Code, by a scripted fake model, and
by a real LLM. The eval harness (`evals/`) is the differentiated part of this
project — Layer 1 drives the server over the protocol, Layer 2 scores an agent's
behaviour over its trace. **HANDOFF-6 Phase 0 is closed** (evidence:
`docs/phase0-eval-report.md`). Phases 1–4 of HANDOFF-6 have not been started.
**103 tests pass, `ruff==0.16.0` clean, CI green on 3.11/3.12/3.13.**

---

## Environment — read before running anything

| | |
|---|---|
| Repo | `~/projects/dam-agentic` |
| Python | **use `.venv/bin/python`** — the system `python3` is 3.9 and too old |
| Analysis engine | `rtivity-python`, installed **editable** from `~/Rtivity-Python` (currently clean at tag `v0.12.0`) |
| Engine version string | `pip show rtivity-python` reports **0.11.0**. This is a known, still-open mismatch: the `v0.12.0` tag was created without bumping `version=`. Not a stale install. |
| Session state | `DAM_MCP_STATE_DIR` overrides the default `~/.dam_mcp/sessions`. **Set it to a temp dir when running evals or tests**, or you will pollute real state. |
| Model credentials | repo-root `.env` (gitignored), loaded by `agent.graph.load_env()`. `GOOGLE_API_KEY` is set. |
| TLS | This machine sits behind institutional TLS inspection: Python HTTPS fails `CERTIFICATE_VERIFY_FAILED` while `curl` succeeds. `truststore` fixes it and is already wired into `build_agent`. **Never** use `verify=False`. |
| Lint | `ruff==0.16.0` pinned, `select = ["E4","E7","E9","F"]`. Do not widen opportunistically. `DTZ001/DTZ007` are **rejected** for analysis code on domain grounds (DAM data carries no timezone); telemetry timestamps are a different case and should be tz-aware UTC. |

Full suite takes ~4 minutes (many tests spawn a real MCP server subprocess).

```bash
cd ~/projects/dam-agentic
export DAM_MCP_STATE_DIR=/tmp/dam-scratch
.venv/bin/python -m pytest -q          # 103 pass
.venv/bin/python -m ruff check .       # clean
```

---

## What was completed in the last session

Ten commits, `88098b9..9d7f561`. Three threads.

### 1. Unblocking the real-model path (HANDOFF-6 Phase 0.1/0.2/0.4)

- `ec3dd6b` — `DEFAULT_GOOGLE_MODEL = "gemini-3.6-flash"`. `gemini-2.5-flash`
  **404s** with "no longer available to new users" *even though `ListModels`
  still lists it*. Catalog membership does not imply callability.
- `2145577` — the `401 ACCESS_TOKEN_TYPE_UNSUPPORTED` was **not** an SDK bug. The
  key lived only in `.env`, nothing loaded it, and the SDK fell through to
  ambient credential resolution and sent a Bearer token. `load_env()` at both
  entry points fixed it; `python-dotenv` is now declared (it had been arriving
  transitively via `pydantic-settings`).
- `78e963b` — `Trace.reasoning_tokens`. `gemini-3.6-flash` is a thinking model;
  a 2-character answer cost 91 tokens, 85 of them thoughts. Verified against raw
  `usageMetadata`: **LangChain folds thought tokens into `output_tokens`**, so
  the existing `input+output` sum was already correct. `reasoning_tokens` is a
  *subset* field for visibility — never add it to the total.

### 2. Tool-contract hardening (unplanned, upstream of eval validity)

- `b1b447b` — `load_experiment` returns `monitor_keys`; `assign_groups`
  normalises a monitor key with `basename()`; the three accepted channel-spec
  forms are stated in the tool description. **Why:** task prompts supply full
  paths, `assign_groups` required basenames, nothing declared which — the model
  was coin-flipping between two equally-supported readings.
- `38de225` — `apply_exclusions` refuses an unresolvable key (same shape as the
  `assign_groups` refusal) and reports `n_before`/`n_after`/`n_excluded`.
  **Why:** an unresolved key was previously recorded as a successful exclusion
  that excluded nobody — a wrong *n*, silently.
- `dc6715d` — `set_analysis_window` reports `monitors_dropped`. **Why:** a window
  could silently drop an entire monitor; the tally listed only survivors and
  everything downstream ran on truncated data. `validate_dam.py` now tracks
  window drops separately from genuinely unparseable files.

### 3. Closing Phase 0 under the revised criterion

- `e1fe252` — `evals/limits.py`. `RECURSION_LIMIT = MEASURED_STEP_FLOOR(10) × 3`.
  The floor was *measured* by running the positive-control `ScriptedModel`
  trajectory at successive limits (9 raises, 10 completes). LangGraph costs
  `2n+2` super-steps for `n` tool calls, so the **previous literal `12` was
  exactly the floor for the 5-call trajectory the first real run attempted** —
  no margin for a single retry. The eval had been scoring our own leash as agent
  behaviour.
- `9cc4fcc` — task completion as a **fourth aggregate state**
  (`n_task_completed`, `task_completion_rate`) alongside
  attempted/completed/crashed. Deliberately **not** an eighth property: averaged
  with the rails, a failed task would still read 6/7. Falsifiable via
  `EvalTask.requires` (tools whose *success* is the deliverable) plus the
  `STEP_LIMIT_SENTINEL`.
- `fb11844` — `docs/phase0-eval-report.md`, the acceptance evidence, with
  provenance.
- `9d7f561` — `create_react_agent` → `langchain.agents.create_agent`
  (`prompt=` → `system_prompt=`); `langchain>=1.0` declared.

---

## Files touched (and what lives where)

| File | Role |
|---|---|
| `dam_mcp/server.py` | 14 MCP tools + 4 resources. Tool docstrings are dispatch logic — treat them as API. |
| `dam_mcp/schemas.py` | Typed returns. `MetricValue` admits no bare numeric list, so a raw series is a **validation error**, not something caught by eyeballing. |
| `dam_mcp/engine.py` | The only place raw counts are touched. Wraps tested Rtivity functions; returns aggregates. |
| `dam_mcp/defaults.py` | `DEFAULT_DEATH_HOURS = 12.0`. **Never hardcode a death-hours literal.** |
| `skills/dam-qc/scripts/validate_dam.py` | The QC detector (subprocess, single source of truth for classification). |
| `evals/limits.py` | `MEASURED_STEP_FLOOR`, `RECURSION_MULTIPLIER`, `RECURSION_LIMIT`. |
| `evals/trace.py` | Trace model, `is_scorable`, `completed_task()`, `STEP_LIMIT_SENTINEL`. |
| `evals/properties.py` | The seven rail properties + two heuristics. |
| `evals/scoring.py` | Aggregation, crash accounting, report rendering. |
| `evals/fake.py` | `ScriptedModel` / `RaisingModel` — keyless controls. |
| `evals/run_agent_eval.py` | Layer 2 runner, `EvalAborted`, `AGENT_FAILURES` allowlist. |
| `agent/graph.py` | `build_agent(model, provider, llm)`; provider switch; `load_env`; truststore. |
| `tests/test_tool_contract.py` | Monitor-key + channel-spec contract over the real protocol. |
| `tests/test_fake_agent.py` | Positive + negative controls; abort/crash paths; step-floor pinning. |

---

## Decisions worth knowing (do not silently reverse)

1. **Infrastructure failures are not measurements.** A 429/401/TLS/timeout/any
   unrecognised exception raises `EvalAborted` and stops the eval. Only
   `AGENT_FAILURES = (GraphRecursionError, ToolException, OutputParserException)`
   — **signed off by the human on 2026-07-24** — count as agent-behaviour
   crashes. Do not widen this list to make a run complete.
2. **Vacuous truth is not a pass.** A zero-tool-call trace is a crash, not a
   score. Zero completed runs prints `NO DATA`, never a number.
3. **Scorers are never loosened to make a run pass.** If a property fails on a
   real trace, the failure is the finding.
4. **`groups_before_metrics` counts *attempted* out-of-order calls, not
   successful ones.** This is intentional. Scoring only successful violations
   would measure how well the server defends itself, and a perfectly defensive
   server would pin the property at 1.0 forever — a property that cannot fail.
5. **Contrasts are pre-registered.** Never add a code path that lets the agent
   write `config/contrasts.yaml`.
6. **Window before exclusions.** `set_analysis_window` refuses once exclusions
   exist. Preserve this in any refactor of the analysis path.
7. **Flag, don't fix.** QC surfaces decisions; it never auto-excludes.

## Dead ends and corrections (so they are not repeated)

- **The `.env`/401 story.** Two sessions treated "SDK sends a Bearer token" as
  the bug. It was an unloaded `.env`. Check credential *plumbing* before
  suspecting a provider SDK.
- **`ListModels` proves nothing.** `gemini-2.5-flash` was listed and uncallable.
- **A green report is not a good result.** The first real run scored 1.000 on all
  seven properties *while failing its task*. That is what produced the
  fourth-state work and `HANDOFF-6-amendment-1`.
- **Forensics that do not exist.** Two failures (an `assign_groups` error, and
  the `grounded_n` failure in the closing eval) could not be diagnosed after the
  fact because per-run traces are not persisted. Session JSONs survive but record
  **end state only** and are named by whatever the agent chose (`exp_000`,
  `Experiment 000`, …), so they cannot be attributed to a run or task. Do not
  claim a cause from them — an earlier session did, and was wrong to.
- **The measured floor mattered.** Do not set `recursion_limit` by feel; a leash
  chosen by feel silently becomes part of the measurement.

---

## What is left

### Open GitHub issues

- **#1 — per-property not-applicable.** Properties return `True` vacuously when
  their precondition never occurred. A comment on the issue proposes splitting
  `groups_before_metrics` into attempt-vs-effect. **Land both together and
  re-baseline once**; do not move the property set piecemeal now that a baseline
  exists.
- **#2 — persist run traces (args + error text per tool call).** Now has two
  motivating cases. **Recommend building this before HANDOFF-6 Phase 2**, not
  inside it: Phase 2's audit record would subsume it, but Phase 2 is far off and
  every eval run until then is unreconstructable.

### Blocked on the human (do not attempt)

- **`config/contrasts.yaml` is still the `EXAMPLE_replace_me` stub.** Deciding
  the real pre-registered comparisons is a scientific task. Per
  `HANDOFF-6-amendment-1` §E, **Phase 3 should not open until this is real** —
  `dam:contrasts:amend`, the scope the agent cannot hold, is meaningless while
  the file it guards is a placeholder.
- **Monitor/treatment confound analysis** — needs the human's account of how
  treatments were assigned across monitors.

### Carried forward, small

- `Rtivity-Python` version-string bump to `v0.12.0` (separate repo: edit, test,
  commit, push, re-tag, update the pin here).
- `HANDOFF-6-amendment-1` D.2: a short analysis window (e.g. 30 min) succeeds
  with a clean-looking tally despite being far below the 12 h death window and
  48 h decline window. It manufactures well-formed numbers from a window that
  cannot support the computation. Fix is a capability declaration in the result,
  the same move as `monitor_keys`. Filed, not built.
- Session naming: sessions should carry the eval task + run index rather than the
  agent's improvised label, or traces cannot be attributed. (Noticed while
  investigating #2; not filed yet.)

---

## Exact next steps

**HANDOFF-6 Phase 1 — private inference path (Ollama).** This is next and starts
fresh. From `docs/HANDOFF-6-identity-security-deployment.md`:

1. `build_agent(provider="ollama")` already exists and has **never been exercised
   end-to-end**. Run the eval through it:
   ```bash
   export DAM_MCP_STATE_DIR=/tmp/dam-eval
   .venv/bin/python -m evals.run_agent_eval --synthetic --runs 1 --provider ollama
   ```
   `langchain-ollama` is **not currently installed** and is not in the `agent`
   extra — adding it is part of this phase.
2. Pick a local model that can actually drive a tool-calling ReAct loop. If the
   first choice cannot, **record which models were tried and why they failed** —
   that negative result is part of the deliverable.
3. Expect provider-shaped bugs (tool-call formatting, stop conditions). Fix them
   **in the provider seam in `agent/graph.py`**, not by special-casing the eval.
4. Write `docs/private-inference.md`: how to run the whole system with no
   external network egress, which knobs matter, and what is lost (capability,
   latency) versus a hosted model.
5. Keep the SJ AI Foundry endpoint in mind as a fourth provider behind the same
   seam. Do not build it — access is not granted — but do not close the seam.

**Acceptance:** `--provider ollama` produces a scorable eval report with the
network disabled, and `docs/private-inference.md` explains the tradeoff.

**Working agreements:** TDD (write the failing test first — it has caught real
defects repeatedly). Scoped commits, ~four per phase, each independently
reviewable. Run `pytest -q` **and** `ruff check .` before declaring anything
done, and report the actual counts. CI must stay green on 3.11/3.12/3.13;
network-dependent work is keyless-tested or skipped, never left to fail
intermittently.

**Comparability note:** the Phase 0 baseline in `docs/phase0-eval-report.md` was
measured on `gemini-3.6-flash` at `RECURSION_LIMIT=30`. If Phase 1 changes the
property set, the limit, or the task set, the Ollama numbers are **not**
comparable to it — say so explicitly rather than presenting them side by side.
