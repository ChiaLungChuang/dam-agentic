# CLAUDE.md — DAM agentic analysis system

Persistent context for this repo. Read fully before the first edit of a session.

## What this is

An agentic QC and analysis system over TriKinetics Drosophila Activity Monitor data.
An LLM agent orchestrates existing, tested analysis functions; it does not analyze
anything itself.

Substrate is deliberate: no PHI, no IRB, publicly shareable. Keep it that way — if a
change would introduce restricted data, stop and ask.

## The one rule that decides whether this works

**The model never sees raw data.**

Every tool returns a *summary plus a handle*. Activity counts stay server-side in Python.
The model gets a `session_id` and a few dozen tokens describing what happened.

This is not a token optimization. It is an architectural guarantee: the model *cannot*
compute or invent a statistic because it never holds the numbers. If you find yourself
returning an array of counts, a full time series, or a dataframe dump to a tool caller,
the design has broken — stop and raise it.

## Architecture

```
DAM .txt files
    ↓  (stay on disk / in memory, server-side)
Rtivity-Python analysis functions   ← all computation happens here
    ↓
MCP server (dam_mcp/)               ← summaries + handles only
    ↓
LangGraph agent                     ← orchestrates; never computes
    ↓
Report + grounded Q&A
```

**Session state:** held server-side, keyed by `session_id`, persisted to disk so a server
restart doesn't destroy an hour of work.

**Q&A grounding:** questions are answered from MCP *resources* (manifest, QC report,
metrics, contrasts) — computed artifacts, never raw data.

## Non-negotiable conventions

**Flag, don't fix.** Every exclusion is a scientific decision affecting n and
interpretation. Surface it with evidence; let the human decide. The one exception is
mechanical alignment (common-window trim, partial-final-bin drop) — deterministic, one
correct answer, but still *reported*.

**Errors are prompts.** Error strings are read by a model deciding what to do next, not
by a human reading a traceback. Bad: `ValueError: invalid literal for int()`. Good:
`Column 10 is not 0/1 in Monitor3.txt — expected light sensor. Found 39 columns, expected 42. Ask the user to confirm DAMSystem version.`

**Contrasts come from config, never from the model.** An agent with free rein over
metrics × groups × phases is an automated p-hacking machine. The contrast set is declared
in `config/contrasts.yaml`, which the model can read and cannot write. It chooses which
declared comparison to run, never what to compare.

**Docstrings are dispatch logic.** The model selects tools by description. State when to
use, when *not* to, and what comes back. Treat it as roxygen aimed at a model.

**Test-first.** This repo's whole premise is that it's tested. Don't let the MCP layer be
the untested part: schema validation, session lifecycle, and error strings all need
coverage.

## DAM file format — verified, do not re-derive

Standard DAM2 monitor files are **42 tab-delimited columns**, no header:

| Col | Field |
|---|---|
| 1–3 | Index, date (`DD MMM YY`), time (`HH:MM:SS`) |
| 4 | Status (`1` = valid) |
| 5–9 | Extras, monitor number, tube (unused), data type, unused |
| 10 | Light status (`0`/`1`) |
| 11–42 | Channels 1–32, beam-break counts |

42 is the **tab-delimited** count. `awk` without `-F'\t'` splits on any whitespace and
reports **44**, because the date column (`22 Dec 25`) holds two internal spaces and
becomes three fields — this has now been raised twice as a format discrepancy and is
not one; use `awk -F'\t'`.

Bin width is typically 1 min — **derive it from consecutive timestamps, never assume**.
A 5-min run parsed as 1-min data yields sleep metrics wrong by 5× that look plausible.

## Channel states

| State | Signature | Action |
|---|---|---|
| `alive` | Activity across the run | Keep |
| `empty` | Zero throughout | Exclude; never an n |
| `died` | Activity, then sustained zero to end | Censor at last movement |
| `suspect` | Implausibly low/high, or ambiguous | Flag for human |

Distinguishing `empty` from `died` matters: an empty tube was never an n; a dead fly must
be censored at death, not scored as a perfect sleeper.

## Known limitations — stated, not bugs

- **Late deaths.** A 12 h trailing-zero window structurally cannot detect a fly dying in
  the final 12 h. Report; don't "fix" by shrinking the window without thinking about
  what that does to false positives.
- **Single-beam blindness.** Zero counts mean no *midline* crossing. A fly active at one
  end of the tube scores zero. This is a hardware bound and sets the floor on what any
  QC can see.

## Gradual decline — fixed (was the open bug)

**Gradual decline used to be silently misclassified.** Flies that taper toward death
without ever fully stopping were called `alive` and never surfaced. Measured baseline:
30/30 silently passed.

This was the important one — exactly the silent corruption the system exists to prevent.
There is no ground-truth answer for when a declining fly died, so the detector is not
scored on correctness; it is scored on **whether it surfaces the ambiguity instead of
silently picking a side.**

Fix (`decline_ratio` in `validate_dam.py`): a phase-normalised trailing-window activity
*rate* check, not a strict-zero test. Each channel's final day is compared against its
own first-day profile at matched clock-hour — self-referenced so genotype cancels,
phase-matched so the light-dark cycle cancels (the critical move; a naive check flags
every healthy fly at lights-off). A collapse below `DECLINE_RATIO` of a channel's own
baseline lands it in `suspect` + `decisions_required`. It surfaces, it does not decide.

Measured after fix: declines surfaced 30/30 (seed 42) and 23/23 on a held-out seed
(1337); empty/died/suspect precision-recall unchanged at 1.000 (no healthy false
positives). The scorer also excludes adversarial channels from the standard PRF so a
surfaced decline is not miscounted as a `suspect` false positive. The principled
onset-dating version (PELT changepoint on the phase-normalised series) is a separate
research question, prototyped in `decline_prototype.py`, out of scope for QC surfacing.

## Evaluation

`damsim/generate.py` synthesizes corpora with exact planted ground truth. `score.py`
grades per defect class.

**Perfect scores mean the eval is measuring the generator, not the world.** The first run
scored 1.000 across every class and was worthless. Value came only from the
`--adversarial` set. When adding a detector capability, add an adversarial case that
attacks it, and change the seed for a fresh held-out set. Never tune against a fixed
corpus.

Report per-defect precision/recall. Never a single aggregate — "87% accurate" hides which
failure you have, and which failure you have is the only useful thing.

## Provenance — be accurate in all generated docs

Rtivity is **published software by Silva et al., *Sci Rep* 2022** (Oliveira lab), built on
Rethomics. This repo's contribution is the repair (archived `longitudinalData` dependency
removed), first-time version control, and the Python rewrite with tests and CI.

Do not write READMEs or docstrings implying original authorship of Rtivity or its
methods. If a claim can't be verified from the repo, don't write it.

## Deployment constraints

- **TLS behind institutional inspection.** On the current dev machine, outbound
  HTTPS from Python fails with `CERTIFICATE_VERIFY_FAILED: self-signed certificate
  in certificate chain` — the internal root CA is in the macOS keychain but not in
  Python's certifi bundle — while `curl` to the same host succeeds. This breaks
  *any* LLM provider SDK (Anthropic, Google, …), not just one. The fix is
  `truststore`: `agent/graph.build_agent` calls `truststore.inject_into_ssl()`
  before instantiating a real model, verifying against the OS trust store without
  weakening verification. Never `verify=False`. `pip` itself is unaffected.
- **Engine resolution.** `rtivity-python` is installed editable from
  `~/Rtivity-Python`, which shadows the pinned `@v0.12.0` git dependency; both are
  at v0.12.0 so they agree — confirm with `git -C ~/Rtivity-Python describe --tags`
  before attributing a result. `pip show rtivity-python` reporting 0.11.0 is the
  known version-string mismatch (tag bumped, `version=` string not), not a stale
  install.

## Layout

```
dam_mcp/          MCP server — tools, resources, schemas
  server.py
  sessions.py
  schemas.py
dam-qc/           Agent Skill: SKILL.md, scripts/, references/
damsim/           Eval corpus generator + scorer
config/           contrasts.yaml — pre-declared contrast set
tests/
```

## Current state

- ✅ `dam-qc/SKILL.md` + `validate_dam.py` — QC detector, works
- ✅ `damsim/` — generator + scorer, works
- ✅ Gradual-decline surfacing — fixed (above), held on seed 1337
- ✅ MCP server (`dam_mcp/`) — 12 tools + 4 resources; Phase 1 Inspector gate passed
- ✅ LangGraph agent (`agent/`) — code exists; Layer 2 eval runner in `evals/` (needs a key)
- ✅ Eval harness (`evals/`) — Layer 1 protocol contract tests in CI; Layer 2 trace scorers

## Next task

Layer 2 agentic eval on real data (`python -m evals.run_agent_eval --data ...`, needs an
API key), and the frailty-marker research question in `changepoint-frailty-note.md`
(needs ≥5-day runs). The HITL-relevant [human] items from HANDOFF-2 remain: the
monitor/treatment confound, and writing the real `config/contrasts.yaml`.

## Reporting verification — say what you ran, before you commit

These are standing rules, not advice. Both come from a real failure: a commit that
broke ten tests was pushed and reported clean, because one test file had been run
and the repo's dependencies were not installed.

**State what you actually ran, before committing, and flag it as partial.** A pass
count from an environment missing dependencies is a partial view, not a result. "I
ran `tests/test_config.py`, not the suite — the engine is not installed here" is a
complete and useful sentence. Reporting the shortfall afterwards is too late; the
commit is already pushed.

**A pass count is meaningless without a collection count.** Report
`passed / skipped / collected`. `114 passed, 10 skipped` looked healthy and
concealed thirteen tests that were never collected at all — `langchain` was
absent, so whole modules vanished rather than skipping visibly. Skips are loud;
uncollected modules are silent. Only the collected total catches them.

**Name what the environment cannot verify.** `requires_rtivity` (10 tests) needs
the analysis engine, which will not install in a bare container. Anything behind
it — including whether a contrast file *produces numbers*, as opposed to parsing
and validating — is unverified there. Say "parses and validates", never "works".

**Never widen scope to fix something you noticed.** Ask first, even when the fix
looks necessary and obvious. The one exception already granted: repair of
breakage you caused yourself, where the repair is the correct fix anyway.

**Commit messages go through `git commit -F <file>`, never inline `-m`.** These
messages quote identifiers in backticks, and an inline message is shell input:
`` `groups` `` ran as a command and left "the way root is" in a pushed commit,
while `` `warnings` `` vanished entirely. Same family as every other
silent-corruption incident here — the output looked plausible, so nothing
flagged it, and the corruption was in the permanent record rather than in a
value some check would catch. Write the message to a file and pass it.

**Whenever you compare two things, name the base — and check you are on it.**
This family has bitten three times in one round, always one step short of a
published wrong claim, and always caught by checking rather than by noticing:

* `comm -12` on `git diff --name-only main..branch` for two PRs, which nearly
  reported `dam_mcp/audit.py` as shared between two PRs that touch one file each.
* A container collection count of 308 set against CI's 316 — **branch head versus
  merge ref.** CI tests the PR merged into current `main`; a local run tests the
  branch alone. They differ by whatever `main` gained in between (here, eight
  tests from a sibling PR).
* `git checkout main` landing on a **stale local `main`** and collecting 208,
  which was nearly reported as the finding.

One shape: *the reference compared against was not the reference intended.* The
mechanical fixes, which are checkable in the moment:

* State the base in the comparison itself; a diff without a named base is not a
  result.
* **`git fetch` does not move local `main`.** Check out `origin/main`, or print
  the sha after checkout and confirm it.
* **Two bases exist and differ silently: CI tests the MERGE REF — the PR head
  merged into current `main` — while a local run tests the BRANCH HEAD alone.**
  They diverge by whatever `main` gained after the branch was cut, and neither
  environment says which it used. This produced the worst instance: 308 against
  316, eight tests apart, and the eight were a sibling PR's. Reproducing CI
  locally means merging `main` into the branch first, or accepting the difference
  and saying which base each figure came from.
* A `cmd_a || cmd_b` fallback that never fires because `cmd_a` *succeeded
  wrongly* is a guard that exists and is not exercised — the same family as the
  rule below, at the shell level. Prefer the unconditional form.

**Never report a number you did not read, and never fill a figure whose run has
not finished.** Two mechanisms, both of which have produced a wrong published
number here:

* **The fetch did not contain it.** `get_job_logs` with `tail_lines=4` does not
  reach pytest's `====== N passed ... ======` line. If the fetch came back
  without that line, refetch with more lines; do not supply the number from
  memory or from a sibling job. Three occurrences, one cause.
* **The figure came from the wrong run.** CI's `316 / 0` was written into a PR
  body while the container run was still going. If a run has not completed, write
  **pending** and report it when it lands. A figure in the wrong environment's
  slot is not a typo; it is an unmeasured claim.

**Compare two branches against their own merge-base, never against current
`main`.** Checking that two PRs touch disjoint files with
`comm -12` on `git diff --name-only main..branch` is wrong the moment either
branch was cut before the other merged: everything the newer `main` gained shows
up as a *reversion* in the older branch's diff, and files neither PR touches
appear in the intersection. This produced a false overlap on `dam_mcp/audit.py`
between PRs #15 and #16, which touch one file each and share none. Use
`git merge-base main <branch>` per branch and diff from there. Same family as the
rules above: a check that runs, produces output, and is wrong.

**An unpinned major is a claim about a version, not about the code. Check the cap
when a dependency is added.** This shape has now happened twice: `ruff>=0.6`
resolved to 0.16.0 and reported 57 findings on unchanged code; `mcp>=1.0` resolved
to 2.0.0, which deleted `mcp.server.fastmcp` and turned a byte-identical tree from
green to red overnight. Both looked like code defects until the diff came back
empty. Every dependency gets an upper bound on its major version — a range, since
the failure mode is API removal rather than patch drift — and `ruff` is the one
exact pin because its rule set moves within patches. Diagnosing this from a red
main works and costs a session; the check at the point of adding costs nothing.
Policy and the current uncapped set: `docs/HANDOFF-7-current-state.md`.

Longer account, with the specific failures: `docs/HANDOFF-9-preregistration-and-run-attribution.md`.

## Lint policy

`[tool.ruff.lint] select = ["E4", "E7", "E9", "F"]` — ruff's historical default,
written down. `ruff` is pinned to an exact version in `[dev]`.

Both are deliberate. Before Jul 2026 the config set only `line-length`, so the
effective rule set was whatever that ruff version happened to default to. CI
resolved `ruff>=0.6` to 0.16.0 against a locally-validated 0.15.22 and reported
57 findings on unchanged code. "ruff clean" was a version-dependent claim with
nothing pinning the version.

If the rule set is ever widened, these are already decided:

- **DTZ001 / DTZ007 — reject.** TriKinetics DAM files carry no timezone. These
  timestamps are naive by nature; forcing tz-aware parsing would introduce a bug,
  not fix one. Needs an explicit ignore carrying this reason, not a fix.
- **BLE001 in `evals/run_agent_eval.py` — reject.** The blind catch is
  deliberate: a crashed run is still a datapoint.
- **I001, PLW1510 — adopt when convenient.** Import sorting is `--fix`-able;
  `check=False` just states intent where returncode is already handled by hand.

Do not run `ruff check --fix --unsafe-fixes` on this repo.

## CI structure

Three independent jobs: `lint`, `test` (3.11/3.12/3.13), `eval`.

- Lint is its own job. It used to be a step inside `test`, so a linter version
  bump wiped out all test signal across three Python versions at once.
- `fail-fast: false` on the test matrix — one version failing must not cancel
  the others.
- Nothing counts as verified until it has been verified somewhere other than a
  laptop. The unpinned-linter problem was invisible locally and only surfaced
  when a second machine ran the same command.
