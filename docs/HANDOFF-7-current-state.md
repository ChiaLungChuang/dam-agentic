# HANDOFF-7 — current state

**Rewritten:** 2026-07-27 · **At commit:** `48e4a31` · **Branch:** `main` (pushed to
`origin`, https://github.com/ChiaLungChuang/dam-agentic)

For a session with no memory of the work that produced this. Read `CLAUDE.md`
first — it holds the architectural rails and is the document that must not be
contradicted.

**This file is a record, not a plan.** It says where work ended up. The plan
documents are `HANDOFF-6` (the four-phase security/deployment arc) and
`HANDOFF-9` (this round's reasoning, in full). Where they disagree with this file
about *why*, they win; this file is the index.

> **Superseded branch.** `claude/phase3-observability-review-6knrxi` is dead. Its
> work reached `main` as two separate branches — `claude/phase3-prereg-infrastructure`
> (PR #4) and `claude/phase3-run-attribution` (PR #5), merged in that order with
> merge commits. Do not branch from it or resurrect it.

---

## Status in one paragraph

The MCP server (`dam_mcp/`) is complete and operated: 14 tools, 4 resources,
driven end-to-end by MCP Inspector, Claude Code, a scripted fake model, and a real
LLM. The eval harness (`evals/`) remains the differentiated part. **HANDOFF-6
Phase 0 and Phase 2 are closed and merged.** Since HANDOFF-7's first version, two
more rounds landed: **pre-registration infrastructure** (the contrast set is now
selected, validated and gated) and **run attribution** (every audit line names the
run that produced it). **185 tests pass, 0 skipped with the engine installed,
`ruff==0.16.0` clean, CI green on 3.11/3.12/3.13.** Phase 1 (Ollama) and Phases 3–4
are not started; Phase 3 is still gated.

---

## Merge history — what is actually in `main`

| PR | Merged as | Contents |
|---|---|---|
| #3 | `fcfce4b` | HANDOFF-6 **Phase 2** — OTel spans + audit stream over one dispatch seam |
| #4 | `5254f7f` | Pre-registration infrastructure — `DAM_PREREG_PATH`, filename↔experiment, closed vocabularies |
| #5 | `48e4a31` | Run attribution — `run_id` on every audit record and tool span |

#4 and #5 touch **no file in common** and each passes standalone on `main`;
verified by intersecting their changed-file lists, and by the count arithmetic
(`164 + 148 − 127 = 185`).

---

## Environment — read before running anything

| | |
|---|---|
| Repo | `~/projects/dam-agentic` |
| Python | **use `.venv/bin/python`** — the system `python3` is 3.9 and too old |
| Analysis engine | `rtivity-python`, installed **editable** from `~/Rtivity-Python` (clean at tag `v0.12.0`) |
| Engine version string | `pip show rtivity-python` reports **0.11.0**. Known, still-open mismatch: the `v0.12.0` tag was created without bumping `version=`. Not a stale install. |
| **Contrast set** | **`DAM_PREREG_PATH` is required and has no default.** See below — this is the one setup step that did not exist before. |
| Session state | `DAM_MCP_STATE_DIR` overrides `~/.dam_mcp/sessions`. **Set it to a temp dir for evals/tests**, or you will pollute real state. |
| Audit log | `DAM_MCP_AUDIT_LOG`, else `<state_dir>/audit.jsonl`. `DAM_RUN_ID` labels the lines. |
| Model credentials | repo-root `.env` (gitignored), loaded by `agent.graph.load_env()`. `GOOGLE_API_KEY` is set. |
| TLS | Institutional TLS inspection: Python HTTPS fails `CERTIFICATE_VERIFY_FAILED` while `curl` succeeds. `truststore` fixes it and is wired into `build_agent`. **Never** `verify=False`. |
| Lint | `ruff==0.16.0` pinned, `select = ["E4","E7","E9","F"]`. Do not widen. `DTZ001/DTZ007` **rejected** for analysis code (DAM data carries no timezone); telemetry timestamps are the opposite case and are tz-aware UTC. |

Full suite ~4.5 min (many tests spawn a real MCP server subprocess).

```bash
cd ~/projects/dam-agentic
export DAM_MCP_STATE_DIR=/tmp/dam-scratch
.venv/bin/python -m pytest -q          # 185 passed, 0 skipped, 185 collected
.venv/bin/python -m ruff check .       # clean
```

Without the analysis engine the same suite reports **175 passed / 10 skipped /
185 collected** — the ten `requires_rtivity` tests. Report `passed / skipped /
collected`, always; see `CLAUDE.md`.

### The contrast set is now a required, checked input

The file declares an **experimental design**; statistics are optional.

```yaml
experiment: myexperiment-2026-07
groups: [mut, ctrl]      # REQUIRED — the legal labels assign_groups is checked against
# contrasts:             # OPTIONAL — omit if the statistics happen outside this tool
```

That is complete and valid. It loads, `list_contrasts` returns `[]` (not an error),
and the full load → window → group → compute pipeline runs. `contrasts:` is only
needed if you want pre-registered comparisons executed *here*; where both are
present, contrast labels must be a subset of `groups:`.

The variable was `DAM_CONTRASTS_PATH` until this round. It is now
**`DAM_PREREG_PATH`**, with no back-compat shim: because there is no default, a
stale client config fails loudly and names the variable rather than silently
loading the wrong declaration. The `list_contrasts` **tool** was deliberately not
renamed — tool names are pinned by the eval layer — so its name is now misleading;
see HANDOFF-9.

`list_contrasts` returns `contrasts`, `groups`, `config_path` and `warnings`. An
empty `contrasts` list is the normal case, so `groups` is what makes the reply
useful.

**Second naming wart, same shape, recorded for the same reason:**
`assign_groups`' **`confirm_n_override` overrides the refusal, not the n.** After
an accepted override `group_sizes` still returns the computed count — 96, not the
declared 32 — because the mapping is what was assigned and n is what the mapping
says. That is the correct behaviour and the wrong name: "override the n" is what
the parameter sounds like it does, and a caller who believes it will read a
downstream 96 as a bug in something else.

Blast radius if it is renamed, so the decision is costed rather than guessed:

* **Tool names and parameter names are pinned by the eval layer** — the same
  constraint that stopped `list_contrasts` being renamed. Layer 1 drives the tool
  over stdio by keyword argument.
* The MCP tool schema changes, so **any client `env` block or scripted caller
  passing the old name breaks** with a validation error naming the missing field.
  That failure is loud, which is the good case.
* `tests/test_tools.py` (the override group of tests) and the end-to-end
  transcript in HANDOFF-13 both name it.
* No persisted artifact carries the parameter name — `Session.n_overrides` stores
  `reason` and `confirmed`, not the argument spelling — so **nothing on disk needs
  migrating.** That is the cheap part.

Neither wart is worth a rename on its own. If the eval layer's pinning is ever
revisited, both should move in the same change.

`config/contrasts.yaml` is a **template with placeholder labels and cannot load**
(its `experiment:` value does not match its filename, by design). Real
declarations live beside it as `config/contrasts-<experiment>.yaml`, one per
experiment, each introduced by its own commit — that commit is the
pre-registration timestamp and the only part of this gate a reviewer can check
independently.

```bash
DAM_PREREG_PATH=config/contrasts-myexp-2026-07.yaml python -m dam_mcp.server
```

With it unset the **server still starts and lists all 14 tools** — this is not a
startup failure and must not be read as a broken MCP server. `load_experiment`,
`describe_experiment`, `run_qc`, `window_tradeoff` and `set_analysis_window` work;
`list_contrasts`, `run_contrast` and `assign_groups` refuse, naming the variable.
`assign_groups` needs the file for its `groups:` key, not for any contrast.
Operational detail and the Claude Code / Claude Desktop `env` block:
`docs/running.md`.

---

## Decisions in force (do not silently reverse)

### Carried forward from earlier rounds

1. **Infrastructure failures are not measurements.** 429/401/TLS/timeout/any
   unrecognised exception raises `EvalAborted` and stops the eval. Only
   `AGENT_FAILURES = (GraphRecursionError, ToolException, OutputParserException)`
   — signed off 2026-07-24 — count as agent-behaviour crashes. Do not widen it to
   make a run complete.
2. **Vacuous truth is not a pass.** A zero-tool-call trace is a crash. Zero
   completed runs prints `NO DATA`, never a number.
3. **Scorers are never loosened to make a run pass.** A property failing on a real
   trace *is* the finding.
4. **`groups_before_metrics` counts attempted, not successful, out-of-order calls.**
   Scoring only successful violations would measure how well the server defends
   itself, pinning the property at 1.0 forever.
5. **Contrasts are pre-registered.** Never add a code path letting the agent write
   a contrast file.
6. **Window before exclusions.** `set_analysis_window` refuses once exclusions
   exist.
7. **Flag, don't fix.** QC surfaces decisions; it never auto-excludes.
8. **A refusal is a defensive success** (Phase 2). `outcome=refused` keeps the span
   status OK with a `tool.refused` event. Marking it ERROR would make every guard
   firing — the point of this server — look like a crash in the trace.

### From this round (HANDOFF-9)

9. **No default declaration file.** Unset `DAM_PREREG_PATH` refuses. Falling
   back to the template would let an undeclared design run and look declared.
   `_check_group_labels` does not swallow a `ToolError` either: with no default,
   "unreadable" includes "nothing is declared", and swallowing that reopens the
   hole.
10. **The filename must contain the `experiment:` value.** A file named `-young`
    declaring `-old` makes its own commit useless as a pre-registration record,
    and the documented workflow is copy-and-edit — exactly how it happens.
    `experiment:` is therefore required.
11. **`phase` / `metric` / `test` are closed vocabularies, validated at load.**
    The engine used to resolve any phase outside `(light, l, day)` to `Dark` via a
    bare `else`, returning a clean-looking result for the wrong half of the day.
    `engine._phase_label` refuses too, so a contrast dict arriving by another route
    cannot fall through.
12. **`run_id` is stamped server-side, never reconstructed caller-side.** Forced,
    not preferred: `run_task`'s crash branch appends a `Trace` with no tool calls,
    its abort branch raises before any `Trace` exists, and `load_experiment`'s
    `session_id` is null by construction — so anything harvested from the agent's
    output is empty for precisely the runs worth investigating.
    `AuditRecord.run_id` defaults to the **constant**, never to `default_run_id()`,
    so reverting the dispatch wiring is detectable.

### Reversal since (recorded in HANDOFF-9)

13. **`groups:` is authoritative; `contrasts:` is optional.** `groups:` declares
    the legal labels and `assign_groups` is checked against it. A file with
    `groups:` and no contrasts is complete and permits the whole pipeline;
    `list_contrasts` returns `[]` rather than erroring. **What prompted it:** the
    workflow has no in-tool statistics step — metrics are exported and tested in
    Prism — so gating grouping on declared *tests* gated a path the work does not
    travel. HANDOFF-9 said the check "can move but cannot become optional"; this
    moved it and did not remove it. An undeclared label is still refused, one layer
    earlier. Where both keys are present, contrast labels must be a **subset** of
    `groups:` (the old union rule silently accepted a typo'd contrast label as a
    new legal group). A file with contrasts and no `groups:` is **refused, not
    derived** — deriving is the union rule again. Declaring a group and not
    assigning it is now a warning rather than a refusal, so a partial load is not
    blocked; that is the one thing genuinely relaxed, **and on the contrast-free
    path the warning has no backstop behind it** — nothing downstream looks at
    group membership again, so an ignored warning means a declared arm carries
    n = 0 to the end. Accepted knowingly: a missing arm is visible in the exported
    metrics. Do not weaken that warning or drop it from `assign_groups`'s return.

### Standing verification rules — now in `CLAUDE.md`

Four rules moved out of a handoff (read once) into `CLAUDE.md` (read every
session), under **"Reporting verification"**: say what you actually ran before
committing and flag it partial; report `passed / skipped / collected`, because
uncollected modules are silent where skips are loud; say "parses and validates",
never "works", for anything the engine arm cannot reach; and do not widen scope
without asking. They came from a real failure — a commit that broke ten tests,
pushed and reported clean. The account is in `HANDOFF-9`.

---

## Dead ends and corrections (so they are not repeated)

- **The `.env`/401 story.** Two sessions treated "SDK sends a Bearer token" as the
  bug. It was an unloaded `.env`. Check credential *plumbing* before suspecting a
  provider SDK.
- **`ListModels` proves nothing.** `gemini-2.5-flash` was listed and uncallable.
- **A green report is not a good result.** The first real run scored 1.000 on all
  seven properties *while failing its task*.
- **The test suite was pinned to one lab's science.** Six test modules assigned the
  contrast stub's group labels while `_check_contrast_labels` read the live file,
  so writing a real pre-registration turned CI red. Fixed by
  `DAM_PREREG_PATH` + an autouse fixture pinning the suite to
  `tests/fixtures/contrasts-testfixture.yaml`. The live file now has exactly one
  test, which asserts the template refuses to load and never what it declares.
- **A pass count without a collection count hides tests.** `114 passed, 10 skipped`
  looked healthy while thirteen tests were never collected — `langchain` was
  absent, so whole modules vanished rather than skipping visibly.
- **Skip reasons that guess send you to the wrong place.** `rtivity_available()`
  was a bare `except Exception: return False`; "set `RTIVITY_PYTHON_PATH`" is
  actively misleading when the real cause was a broken dependency.
  `rtivity_status()` now returns `(importable, cause)`.
- **The measured floor mattered.** Do not set `recursion_limit` by feel; a leash
  chosen by feel silently becomes part of the measurement.

---

## What is left

### Open, and named honestly

- **W3C trace context across the stdio boundary.** `dam.agent.run` (eval process)
  and `dam.tool.*` (server subprocess) still do **not** share a trace context. They
  correlate by `dam.run_id` and `dam.session_id`, which a collector can join on —
  `run_id` is the reliable one. True parent/child nesting needs `traceparent`
  propagation through the MCP call, and no client/adapter exposes a hook for it.
  The seam is left open, not built. The audit log has no such limit: it is written
  server-side where the data is touched.
- **`load_experiment`'s audit `session_id` is null.** That call mints the id, so
  there is nothing to record at dispatch time. `run_id` makes the line attributable
  to a run; it does not make it joinable to the session the call created.
  Orthogonal, small, worth its own commit — a hook in `SessionStore.create` (the
  single mint site) rather than in `server.load_experiment`.
- **GitHub #1 — per-property not-applicable.** Properties return `True` vacuously
  when their precondition never occurred. A comment proposes splitting
  `groups_before_metrics` into attempt-vs-effect. **Land both together and
  re-baseline once**; do not move the property set piecemeal now a baseline exists.
- **GitHub #2 — persist run traces.** Largely subsumed: the audit record carries
  args, outcome, timestamp, files and error text per call, and `run_id` now closes
  the attribution half. Re-read the issue against `docs/observability.md` before
  building anything further.

#### From the first real run — all eight in `docs/HANDOFF-11-first-real-run.md`

Session `dam-7010fc5ebdc9`: 12 monitors, 384 channels, 161.8 h. None of these were
reachable by the synthetic corpus — each needs a real light-dark cycle, real
mortality, or real per-monitor clocks.

- **H11-1 — `window_tradeoff` is non-monotonic and its rows are not cumulative.**
  Each row re-classifies all 384 channels independently, so every row sums to 384
  and `n_died` means "would be called dead if recording stopped here". The
  intermediate rows cannot be used to pick a window. *The note and two docstrings
  were corrected; the computation was not.* **Two tests still assert the
  monotonicity the real data falsifies** (`test_window_tradeoff_curve_is_non_increasing`,
  and a `rows[0] >= rows[-1]` assertion in `test_contract.py`) — whoever fixes the
  computation resolves both in that commit.
- **H11-2 — the death rule cannot distinguish sleep from death.** `death_hours`
  defaults to 12.0 and the cycle is 12:12, so the trailing-zero threshold equals
  the dark phase and a fly quiescent through one night meets the death rule
  exactly. Upstream of H11-1. **Fix this one first.**
- **H11-3 — `compute_*` runs silently with QC decisions outstanding.** 63 raised,
  none applied, sleep computed over all 384 channels without a word; flags lopsided
  by group (26/10/18/9), so a group difference can be a difference in how many
  animals were dying. A warning is proposed, not built.
- **H11-4 — latency is reported without its denominator.** Defined for 52/71/21/18
  of 96 per group; the largest effect in the dataset sits on the smallest n, and
  the return says nothing about it. The agent caught this; the tool did not.
- **H11-5 — an empty tube was excluded as mortality.** M9 ch16's last movement is
  six minutes after the run started. Excluding it is right; counting it as a death
  event is not, and any survival analysis on these exclusions would.
- **H11-6 — the exclusion reason says "through full run"** and is literally true of
  one channel of thirteen. Correct the wording *before* any report is rendered —
  `render_report` copies reasons into the manifest verbatim.
- **H11-7 (open question) — channel indices repeat across monitors to the minute.**
  Files confirmed raw. Either a one-minute clock offset between monitors, or
  something shared at the same channel index — which would make channel index a
  confounder in a design that assigns groups by channel range. ~~Needs a second
  experiment, not a code change.~~ **ANSWERED and closed by H12-1: it is one fly
  breaking three beams.** See `docs/HANDOFF-12-monitor-topology.md`.
- **H11-8 (open question) — total sleep units are unverified.** 4–5 h over 6.7 days
  is too low, and light-phase bout duration × bout count is ~8,919 min against
  ~4,854 min of light phase available — arithmetically impossible over one
  interval. Either the two are computed over different intervals, or dying flies'
  trailing bouts inflate the mean (SD > mean points at the second, which ties to
  H11-3). **Settle before any sleep number leaves this system.**
- **H12-1 — three monitor files are three beams on one population, and nothing in
  the system can say so.** Each apparatus is 32 tubes through three stacked
  detector boards; `Monitor1/2/3` are three IR beams at three positions along the *same
  32 flies*. So session `dam-7010fc5ebdc9` is 128 animals, not 384, and 32 per
  group, not 96 — every group mean, SD and n from that run is affected, and
  per-beam death detection is invalid because trailing zeros mean the fly stopped
  visiting that region of the tube. Explains the 0-empty result and answers H11-7. No input
  anywhere — `load_experiment`, `assign_groups`, or the declaration — carries the
  apparatus↔beam relationship, so three racks and three beams are accepted
  identically and in silence. **Upstream of every H11 item that touches n or
  death; decide the representation before fixing anything downstream.**
  `docs/HANDOFF-12-monitor-topology.md`.
- **H13-1 — an accepted n-override is not in the record.** `assign_groups` refuses
  a mapping whose channel count contradicts the declaration's declared n, and the
  override (`n_override_reason` + `confirm_n_override=true`) is surfaced only in
  the result's `warnings`. It is **not persisted to the session**, so it reaches
  neither the rendered report nor the audit record: an overridden n can appear in
  a report with nothing recording that it was overridden, or why. Needs a schema
  field — `GroupResult` and the session both have nowhere to put it — which is why
  it was left out of the checksum's scope rather than bolted on. This is the same
  defect shape HANDOFF-11 documents four times over: an action taken that does not
  appear in the record. **Settle before an overridden n reaches a report.**
- **H13-2 (structural) — `damsim` cannot express a declared n, so the eval cannot
  score this defect class.** The generator emits monitor files only; it has no
  declaration emitter, so a generated corpus cannot state a declared n at all,
  let alone one that disagrees with the channels a mapping assigns.
  `tests/fixtures/contrasts-nmismatch.yaml` is the first declaration in the suite
  able to express a declared n, and it is a hand-written fixture, not corpus
  output. Same structural gap HANDOFF-11 named for the context-shaped findings: a
  generator that plants the ground truth cannot plant a defect it has no
  vocabulary for, so the whole class is invisible to scoring rather than scored
  and missed.

  Ordering, stated as an opinion and not as a finding: **H13-1 is the higher of
  the two** — it can put a wrong number into a report — and H13-2 is the reason
  neither would be caught automatically.

  **PARTIALLY closed, and deliberately not closed.** `damsim --declarations`
  (merged in #14) emits a declaration per experiment, alternating a matching one
  with a planted mismatch, and carries the mapping in `ground_truth.json` so a
  scorer has both halves of the comparison. So the corpus **can now express** the
  defect class.

  **Nothing in the harness scores it.** The "4/4 refused or proceeded exactly as
  planted" result in #14 came from a throwaway script driving the stdio server by
  hand, not from `damsim/score.py` or the Layer 2 runner. `score.py` grades QC
  defect classes off `ground_truth.json`'s monitor fields and does not look at
  `declaration`; no eval task drives `assign_groups` against a generated
  declaration; nothing in CI would go red if the checksum were deleted tomorrow —
  only the unit tests would, and those use the hand-written fixture.

  So the item stands, with its wording narrowed: it was filed as "the eval cannot
  score this class" and the accurate statement is now **"the corpus can express it
  and nothing runs it."** That is the same shape as the original gap and it is
  worth saying plainly rather than banking the capability as a fix — a capability
  nothing exercises is indistinguishable, in CI, from one that does not exist.
  Closing H13-2 needs a scorer that reads `truth["declaration"]`, drives the
  mapping, and reports refused-vs-proceeded per experiment as its own per-class
  precision/recall line.
- **H18-1 — the positive control was itself vacuous, and that is the finding.**
  `test_positive_control_scores_clean` asserted `all(pr.passed)` over seven rails
  while its script exercised **three**: it sets no window, applies no exclusion,
  runs no contrast and hits no error, so four rails returned the old vacuous
  `True` and the assertion passed on them. A second assertion in the same test
  (`answer_grounded`) was vacuous too and was invisible until the first was fixed
  — five vacuous checks, not four. **The test whose job is to prove the harness
  reports honestly was resting on the dishonesty it exists to detect.** Issue #1
  did not introduce this; issue #1 revealed it, and nothing else would have — a
  vacuous pass is indistinguishable from a real one until not-applicable exists as
  a value. Closed in the issue #1 PR by pinning the applicable set exactly.
- **H18-2 (open) — `answer_grounded` has never been observed to pass, anywhere,
  and the reason it is unscored is invisible to a reader.**
  This is the rail closest to the project's central claim — that a number in an
  answer traces to tool output rather than being invented. Three independent
  checks come back empty:

  1. **No positive control at any level.** Its only test is the negative one
     (`tests/test_fake_agent.py:103`, a fabricated `88888`), which proves the rail
     *fires*. Nothing proves it *passes* on a correctly grounded answer.
  2. **It has never been scored.** It appears **zero times** in
     `docs/phase0-eval-report.md`: it is in `HEURISTIC`, and `aggregate` reports
     only `STRUCTURAL`.
  3. **Its one end-to-end assertion is now pinned `is None`** (issue #1), which
     documents the absence honestly and does not fill it.

  **The exclusion is deliberate — checked, not assumed.** It is stated in three
  places: the property's own docstring (*"Advisory — a number can legitimately be
  prose (a threshold), so treat a failure as a flag to inspect, not a hard
  verdict"*), the section header in `properties.py` (*"heuristic; not part of the
  strict structural set"*), and `evals/README.md`, which lists it apart from the
  rails. `evaluate()` defaulting to `STRUCTURAL` is the mechanism, not an
  oversight.

  **So the finding is not that a rail was forgotten — it is that the exclusion
  leaves no trace in the output.** A reader of `phase0-eval-report.md` sees seven
  rails and has no way to learn that an eighth exists and was withheld. Nothing in
  `format_report` names an excluded property; the list it prints is simply the
  scored one.

  **The fix is a line in the report naming the excluded rail and why — not scoring
  it.** Publishing a number from a check whose own docstring says it must not be
  read as a verdict would invert everything else in this round; it is the same
  reasoning that made `is None` better than deleting the assertion. What is owed
  is disclosure, not a figure.

  Closing it therefore needs two things, and only the first is about scoring:
  a positive control that states a number *taken from a tool result* and asserts
  `passed is True` end-to-end, and a line in `format_report` naming `HEURISTIC`
  properties as evaluated-but-not-aggregated, with the reason.

  **The wider audit, which is the lesser finding.** At the unit level all seven
  `STRUCTURAL` rails have a positive control in `tests/test_properties.py`, on
  hand-built traces. At the **end-to-end** level — driving the real stdio server —
  only three of seven do (`load_first`, `qc_before_metrics`,
  `groups_before_metrics`), which is what H18-1 exposed. Read alone, "seven of
  seven at unit level" sounds like adequate coverage; the gaps above are the
  finding.
- **H18-4 (open) — the audit stream has never persisted. Every audit record this
  project has written is gone.** `resolve_audit_path()` defaults to
  `<state_dir>/audit.jsonl`, and `_state_dir()` defaults to
  `~/.dam_mcp/sessions` — **outside the repo**, in a home directory that dies with
  a cloud container. Verified rather than reasoned: the resolved default is
  `/root/.dam_mcp/sessions/audit.jsonl`, `exists() == False`. CI sets neither
  `DAM_MCP_AUDIT_LOG` nor `DAM_MCP_STATE_DIR`, no document instructs anyone to set
  them, and `.gitignore` carries `sessions/`, so the obvious repo-relative
  override would be silently swallowed too.

  **The scale is the point.** PRs #14 and #17 were *entirely* about getting the
  declared-n override into the audit record — a session field, a schema field, a
  dispatch hook, an event field with three-valued back-compatibility, and about
  twenty tests. All of it writes to a file that no longer exists. Phase 2's whole
  instrumentation pass is in the same position.

  **Family one, and probably its oldest instance: machinery that exists and is
  never retained.** H13-2 was expressible-but-unrun; issue #1 was
  run-but-vacuous; H18-3 is a layer with no CI surface; this is a stream that is
  written correctly, tested thoroughly, and discarded. It is the instance with the
  most work layered on top of it, which is exactly why it went unnoticed — every
  round verified that the record was *written*, and none asked whether it was
  *kept*.

  Surfaced while checking a precondition for issue #2, which depends on it: a
  trace that references audit lines by id references something guaranteed absent.

- **H18-3 (open) — the CI `eval` job does not exercise `evals/scoring.py`.** Found
  while ruling the job out as the casualty of issue #1's red run. It runs
  `damsim/generate.py` + `damsim/score.py`, which grade the **corpus defect
  detector** — they never import `evals.scoring` or `evals.properties`. So a change
  to `aggregate` or `evaluate` passes that job untouched, and the property layer's
  only end-to-end exercise anywhere in CI is one engine-gated test in
  `test_fake_agent.py`. The job name promises coverage the job does not provide.

  **These three are one family, and naming the family is worth more than the three
  entries.** H13-2: the corpus could *express* a defect and nothing *ran* it.
  Issue #1: the harness *ran* the rails and scored them *vacuously*. H18-3: a whole
  layer has almost no CI surface at all. Each is a different distance along the
  same axis — **the gap between machinery that exists and machinery that is
  exercised** — and in every case the artifact looked green while measuring less
  than its name implied. When adding to the eval layer, the question to ask is not
  "does this work" but "what turns red if I delete it".
- **The mcp 2.x migration.** `mcp` is capped at `<2` because 2.0.0 removed
  `mcp.server.fastmcp` outright — no `FastMCP` symbol anywhere in the package, no
  top-level `fastmcp` module; `mcp.server` now exposes `mcpserver`, `lowlevel`,
  `apps`, `auth`. **This is not a dependency bump and must not be scheduled as
  one.** It means rewriting `dam_mcp/server.py` against a redesigned server API,
  and with it the `_tool_manager.call_tool` chokepoint — which is where the
  *entire* Phase 2 instrumentation pass lives: one span and one audit record per
  tool call, the ok/refused/error taxonomy, `run_id` stamping, and
  `resolve_data_files`. The `ToolAnnotations` surface goes too. Every rail in
  `docs/observability.md` is downstream of a seam that does not exist in 2.x.
  **It needs its own handoff**, and the first question that handoff has to answer
  is what replaces the single-chokepoint property — if 2.x has no equivalent,
  Phase 2's "one instrumentation pass, no per-tool decorators" decision is
  reopened, not ported.
- **Dependency-pinning policy — now recorded** (see below). The remaining
  uncapped majors are a decision left open, not an oversight: `truststore`,
  `python-dotenv`, the three `opentelemetry-*`, `pytest`, `pytest-asyncio`, and
  the core runtime set (`pydantic`, `pyyaml`, `numpy`, `pandas`). Same exposure,
  varying blast radius. Capping them is a small commit whenever it is wanted.

### Dependency pinning — the policy

Every dependency carries an **upper bound on its major version**. Ranges, not
exact pins: the failure mode is a major-version API removal, not patch drift, so
a cap must still let security and bug-fix releases through.

`ruff` is the single exact pin, for the opposite reason — its *rule set* moves
within patch releases, so "ruff clean" means nothing without a stated version.

The policy was bought twice, both times by the same shape:

| | Declared | CI resolved | Cost |
|---|---|---|---|
| ruff | `>=0.6` | 0.16.0 | 57 findings on unchanged code |
| mcp | `>=1.0` | 2.0.0 | red main; a byte-identical tree green one day, failing the next |

In both cases the claim "the code is fine" was really a claim about a version that
nothing had written down. **Check the cap when a dependency is added.** Diagnosing
it from a red main works, but it costs a session and it looks like a code defect
until the moment it doesn't.

### Blocked on the human (do not attempt)

- **A real `config/contrasts-<gene>-<timepoint>-<yyyy-mm>.yaml`.** Group labels,
  the primary endpoint, and the multiplicity stance are scientific decisions. Until
  one exists no contrast can run — which is now the intended behaviour, not a bug.
  Per `HANDOFF-6-amendment-1` §E, **Phase 3 does not open until this is real**:
  `dam:contrasts:amend`, the scope the agent cannot hold, is meaningless while the
  file it guards is a template. #4 built the machinery the gate needs; it did not
  open the phase.
- **Monitor/treatment confound analysis** — needs the human's account of how
  treatments were assigned across monitors.

### Carried forward, small

- `Rtivity-Python` version-string bump to `v0.12.0` (separate repo: edit, test,
  commit, push, re-tag, update the pin here).
- `HANDOFF-6-amendment-1` D.2: a short analysis window (e.g. 30 min) succeeds with
  a clean-looking tally despite being far below the 12 h death window and 48 h
  decline window. It manufactures well-formed numbers from a window that cannot
  support the computation. Fix is a capability declaration in the result, the same
  move as `monitor_keys`. Filed, not built.
- **HANDOFF-6 Phase 1 (Ollama)** — `build_agent(provider="ollama")` exists and has
  never been exercised end-to-end. Needs an environment with model-registry egress;
  the agent proxy in the cloud container 403s `ollama.com`, `registry.ollama.ai`
  and `huggingface.co`. Plan and acceptance criteria: `HANDOFF-6` Phase 1.

**Comparability note:** the Phase 0 baseline in `docs/phase0-eval-report.md` was
measured on `gemini-3.6-flash` at `RECURSION_LIMIT=30`. If a later round changes
the property set, the limit, or the task set, its numbers are **not** comparable —
say so rather than presenting them side by side.
