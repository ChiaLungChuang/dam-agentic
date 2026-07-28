# HANDOFF-9 — Pre-registration hardening & run attribution

**Written:** 2026-07-27 · **Base:** `main` at `fcfce4b` (HANDOFF-8 / Phase 2 merged)

Read `CLAUDE.md` first, then [`HANDOFF-8`](HANDOFF-8-phase2-observability.md) for
the state this continues from. This handoff covers two independent pieces of work,
delivered as **two pull requests**, and records one process failure worth keeping.

**Phase 3 was not started.** It remains gated (below).

---

## Status in one paragraph

Two concerns, split into two PRs because they share no files. **PR A —
pre-registration infrastructure**: the contrast set is now selected by
`DAM_PREREG_PATH` with *no default*, the filename must name the experiment the
file declares, and `phase` / `metric` / `test` are closed vocabularies validated
at load. **PR B — run attribution**: closes the gap HANDOFF-8 flagged, by stamping
a `run_id` on every audit record and `dam.tool.*` span, handed to the server
through its launch spec. Both are keyless. Neither touches Phase 3.

---

## PR A — pre-registration infrastructure

### What was wrong

The suite was pinned to the live `config/contrasts.yaml`. Six test modules assign
the stub's group labels, and `server._check_contrast_labels` read the repo file
with no way to redirect it — so **any lab writing any real pre-registration turned
CI red.** The contrast set is the investigator's scientific artifact, not a test
fixture. This was found the hard way; see *Process* below.

Two further defects surfaced while fixing it, both of the shape this repo keeps
finding — an operation that does the wrong thing and reports success:

* `config/contrasts.yaml` was a template that **loaded by default**, so an
  unregistered comparison could run and look registered.
* `engine._per_animal_metric` resolved any phase outside `(light, l, day)` to
  `Dark` via a bare `else`, so `dusk`, `night`, or a typo returned a
  clean-looking result **for the wrong half of the day**.

Template-by-default *plus* silent-fallback is a path to a confident wrong number.
Both halves are gone.

### What landed

| Commit | Contents |
|---|---|
| `Make the contrast set path configurable, and unpin the suite from it` | `config.config_path()` reads `DAM_PREREG_PATH`, resolved per call. `list_contrasts` returns `config_path`. Autouse conftest fixture pins the suite to `tests/fixtures/contrasts-testfixture.yaml`. |
| `Mark config/contrasts.yaml as a template, not a pre-registration` | `TEMPLATE — NOT A PRE-REGISTRATION` header, `experiment: TEMPLATE_replace_me`, illustrative primary-endpoint rationale. |
| `Require DAM_PREREG_PATH; enforce filename and closed vocabularies` | No default; filename ↔ `experiment:`; `PHASES` / `METRICS` / `TESTS` validated at load; `engine._phase_label`; `docs/running.md`. |

### Decisions worth knowing (do not silently reverse)

1. **No default is the point, not an oversight.** Unset `DAM_PREREG_PATH`
   refuses. It breaks the stdio path loudly, once, at setup — the cheaper failure
   against "an unregistered comparison looked registered". `docs/running.md`
   carries the `claude mcp add --env` and `claude_desktop_config.json` fix, because
   a client launches the server itself and a shell `export` never reaches it.
2. **The label check no longer swallows `ToolError`.** It was best-effort, so an
   unreadable config never blocked assignment. With no default, "unreadable" now
   includes "nothing is declared" — swallowing that would reopen the hole the rule
   closes. *(Since renamed `_check_contrast_labels` → `_check_group_labels` and
   pointed at `groups:`; see the reversal below. This decision still stands — only
   what it checks against changed.)*
3. **The filename must contain `experiment:`.** The layout's whole value is that
   the commit introducing `config/contrasts-<experiment>.yaml` is that
   experiment's pre-registration timestamp. A file named `-young` declaring `-old`
   destroys that silently, and the documented workflow is copy-and-edit — exactly
   how it happens. `experiment:` was unparsed before and is now required, or the
   check has nothing to check against.
4. **The template cannot load, by construction.** `TEMPLATE_replace_me` is not in
   the stem `contrasts`. Pointing at it deliberately still refuses. Its structure
   is still tested, by reading it directly rather than through `load_config`.
5. **Validation moved from run time to load time** for metric and test. A bad
   metric used to surface only when *that* contrast ran, so a typo in the twelfth
   stayed invisible through eleven successful runs.
6. **The suite never reads the live contrast file.** One test does, and it asserts
   only that the template refuses to load. A test that goes red because a
   scientist declared their actual experiment is testing the wrong thing.

### ⚠ REVERSED — grouping no longer requires a declared contrast set

**Superseded 2026-07-27, after PR #4 and #5 merged.** The section below is kept
verbatim because its reasoning is still correct about *what the check is for*; it
was wrong about *what the check should hang off*. Read it, then read this.

**What changed.** `groups:` is now authoritative and required: it declares the
legal group labels, and `assign_groups` is checked against it. `contrasts:` is
optional. A file with `groups:` and no contrasts is a complete declaration that
permits the whole load → window → group → compute pipeline. `list_contrasts`
returns an empty list on such a file rather than erroring; `run_contrast` still
refuses any id not declared.

**What prompted it.** The investigator's actual analysis workflow has no in-tool
statistics step: Rtivity produces metrics, they are exported, and all testing
happens in Prism. So the contrast set was gating a path the work does not travel —
grouping was blocked on declaring *tests* that would never be declared, for an
experiment that is perfectly well specified without them. This project's value is
the harness engineering, not the analysis; statistics are deprioritised
accordingly.

**This is a move, not a removal — the distinction the section below insisted on.**
It said: *"the thing to preserve is the check, not the timing: it can move later,
but it cannot become optional without reopening the config/session mismatch."*
That constraint is honoured. An undeclared label is still refused, one layer
earlier, at the point the human types it in `assign_groups`. What changed is what
it is checked against.

**The rule changed shape too, and got stricter in one direction.** The old rule was
a *union*: every label the contrasts named had to be assigned, and any label a
contrast named was thereby legal. That silently accepted a typo'd contrast label
as a new legal group. The new rule is a *subset*, enforced twice:

* contrast labels ⊆ `groups:`, checked **at load** — a contrast typo now surfaces
  before any run, not as an empty arm much later;
* assigned labels ⊆ `groups:`, checked **at assign time** — an assignment typo is
  refused where it is made.

**One thing genuinely relaxed, and the warning is unbacked on the default path.**
Declaring a group and not assigning it used to be a refusal (via the union rule);
it is now a **warning** on `assign_groups`. A partial load — one monitor of a
four-arm design — is legitimate, and refusing it would block real work. Keeping
the warning was decided deliberately.

The justification first written here was wrong and is corrected: it claimed a
contrast naming an unassigned arm "still fails loudly in `run_contrast`", implying
a backstop behind the warning. **That backstop exists only for files that declare
contrasts — precisely the case this change makes optional, and a contrast-free
file is now the intended default path.** On that path nothing downstream ever
looks at group membership again, so **the warning is the only signal there will
ever be.** If a caller ignores it, a declared arm silently carries n = 0 through
to the end.

That was accepted knowingly, on this reasoning: a missing arm is visible
downstream in the exported metrics — a group with no rows is not a subtle defect
in a table someone is about to put into Prism. The warning is a convenience, not
the last line of defence, and the last line of defence is the human reading their
own export.

Two consequences follow. Do not weaken the warning's text or drop it from
`assign_groups`'s return; on the contrast-free path it is load-bearing in a way it
would not be if a contrast existed. And if this trade proves wrong, tighten the
warning to a refusal — do not restore the union rule.

**Compatibility decision, made explicitly** (a file with `contrasts:` and no
`groups:`): **refuse, do not derive.** Deriving `groups:` from the contrast labels
*is* the union rule, so it would reintroduce exactly the defect the subset rule
removes, and it would make a file's meaning depend on whether a key happened to be
present — one loader with two silent semantics. The refusal names the labels the
contrasts reference, so fixing it is a copy-paste. Blast radius in this repo is
zero: both files carrying contrasts already declare `groups:`.

**Ignored values under `groups:` are now loud.** A `groups:` mapping carries values
(`mut: {Monitor1.txt: [1, 16]}`) that the loader never reads — channel ranges reach
the server only through `assign_groups`. Ignoring them silently is the same shape
as the phase fallback this repo already found: an input that looks like it does
something and does not. Someone will write ranges there and assume they applied.
`config.declaration_warnings()` now reports them, surfaced by both `list_contrasts`
and `assign_groups`. A mapping whose entries carry *no* value warns about nothing —
warning where there is no ignored input just trains the reader to skip warnings.

The shared test fixture was converted from the mapping shape to a plain list, so
the suite is not permanently carrying a warning it has to filter out. The mapping
form keeps dedicated tests with their own files.

**`list_contrasts` now returns `groups` and `warnings` as well as `contrasts` and
`config_path`.** With contrasts optional, an empty `contrasts` list is the *normal*
response, and a reply that says only "nothing" answers a question nobody asked. The
useful answer is what the experiment *does* declare: its legal group labels. The
docstring says so explicitly, so a model reading an empty list computes the metrics
and reports them rather than inventing a comparison to fill the gap.

**`DAM_CONTRASTS_PATH` was renamed to `DAM_PREREG_PATH`.** The old name described
the file's old job. Now that the file's primary content is the design and contrasts
are optional, it was actively misleading — it told the reader the file was about
tests.

No back-compatibility shim, deliberately. Normally renaming a published env var
demands one, but the no-default rule makes the shim unnecessary: an unset variable
already refuses loudly and names itself, so a stale client config *fails visibly*
rather than silently loading the wrong declaration. A shim would convert that loud
failure into a silent success on a deprecated path, which is the exact defect class
this repo keeps removing.

**The `list_contrasts` tool was NOT renamed, and the name is now misleading.** It
returns a design declaration of which contrasts are one optional part. It was left
alone because tool names are pinned by the eval layer in four places — the task
prompts in `default_tasks`, `EvalTask.requires`, the tool-sequence properties in
`evals/properties.py`, and the tool-surface smoke test — so renaming it is a
separate change with its own blast radius and its own re-baselining question. It
is a real wart; it is recorded rather than hidden, and it should be decided on its
own.

---

### Why grouping requires a declared contrast set

*(Historical — see the reversal above. Retained for the reasoning, which explains
what the check protects and why it could move but not vanish.)*

The one refusal that looks like overreach, so it is worth writing down before
someone "fixes" it.

With no contrast set in effect, `assign_groups` refuses — and grouping does not
depend on contrasts in any conceptual sense. You could reasonably assign channels
to genotypes and never run a comparison at all.

It refuses because `_check_contrast_labels` validates the labels you assign against
the union of labels the declared contrasts name, and **that check is the thing that
stops a pre-registered contrast from pointing at a group with no animals in it.**
Without a contrast set there is nothing to check against, and the check is the
whole reason the coupling exists: a contrast id that is legal at load time but
resolves to an empty arm at run time is a config/session mismatch that surfaces as
a confusing "no animals with a value" error deep in the engine, long after the
grouping decision that caused it.

Two consequences follow, both intended:

* **The gate is upstream of everything.** Every `compute_*` tool requires groups,
  so in practice the pipeline runs as far as QC and then halts until a real
  contrast file exists. Loading and QC still work — see the table in
  `docs/running.md`.
* **The refusal had to stop being best-effort.** `_check_contrast_labels` used to
  swallow a `ToolError` so an unreadable config never blocked assignment. That was
  defensible when the config always existed; with no default it would mean the gate
  silently disappears in exactly the case where nothing is pre-registered.

If this coupling is ever loosened, the thing to preserve is the check, not the
timing: it can move later, but it cannot become optional without reopening the
config/session mismatch it was written for.

### The layout this enables

```
config/
  contrasts.yaml                          # marked template. Cannot load.
  contrasts-<experiment>.yaml             # real sets, one per experiment,
                                          # each its own commit = its timestamp
```

Selected per run: `DAM_PREREG_PATH=config/contrasts-<experiment>.yaml`.

**No real contrast file is included in this PR.** Genotype labels and the choice
of primary endpoint are a human, pre-registration decision; the investigator
commits that file separately. This PR ships only the machinery and the template.

---

## PR B — run attribution

### The gap (HANDOFF-8)

> a reviewer can read *what each tool call did* from `audit.jsonl`, but tying a
> block of audit lines to a specific eval run/task still wants the session-naming
> fix.

### Why it is stamped server-side, not reconstructed caller-side

This was the load-bearing design decision, and it is forced rather than preferred.
Three line classes defeat a session-keyed join:

* `run_task`'s **crash** branch appends a `Trace` with **no tool calls at all**;
* its **abort** branch raises `EvalAborted` **before any `Trace` exists**;
* `load_experiment`'s `session_id` is **null by construction** (that call mints
  it), and refused calls can carry stale handles.

So anything harvested from the agent's output is empty for precisely the runs
someone opens `audit.jsonl` to investigate.

### What landed

| Commit | Contents |
|---|---|
| `audit: carry a run id, resolved like the principal` | `DEFAULT_RUN_ID` / `default_run_id()`; `run_id` on `AuditRecord`, defaulted to the *constant*. Behaviour-neutral, no new import. |
| `Stamp the run id on every span and audit record at dispatch` | Three lines in the existing wrapper: resolve, `dam.run_id` on the span, `run_id` on the record. |
| `Stamp the eval's run id into the server it launches` | `_server_spec(env_extra=…)`, `build_agent(env_extra=…)`, id minted per run *before* `build_agent`. |

### Decisions worth knowing (do not silently reverse)

1. **`AuditRecord.run_id` defaults to the CONSTANT, never to `default_run_id()`.**
   This is the single most important line. A record that read the environment
   itself would keep producing correct-looking output after the dispatch wiring
   was reverted — the defect `HANDOFF-6-amendment-1` §D.3 records, where a
   contract test passes on a full revert of the change it covers.
2. **The id is minted before `build_agent`, not alongside the `Trace`.** The
   server has it at spawn, so calls made before an abort are already stamped. A
   run that aborts halfway has executed real tool calls against real data.
3. **Transport is the launch spec, not this process's environment.**
   `os.environ["DAM_RUN_ID"] = …` in the parent is shorter and wrong: the suite
   drives the instrumented server in-process elsewhere, so a leaked id would stamp
   unrelated lines in whichever test ran next. Order-dependent and invisible until
   it bites. Pinned by a test.
4. **Never a tool argument.** The model can neither see nor set it, so it cannot
   label its own audit trail. It also keeps instrumentation off the signatures
   FastMCP introspects for its JSON schemas — the reason the seam is
   `_tool_manager.call_tool` and not per-tool decorators.
5. **Read per call, not cached.** A cached resolver is indistinguishable from
   correct in a subprocess, whose environment never changes after exec.
   `test_run_id_is_read_per_call_not_cached` is the only test that discriminates it.
6. **One stamp per eval invocation**, shared across tasks, so every id from one
   run selects with a single `startswith`. tz-aware UTC, with a test — a naive
   `datetime.now()` would label tz-aware records with a local-offset hour.

### Known limit, stated

`run_id` and `session_id` correlate the eval process and the server subprocess;
they still do **not** share a W3C trace context. Unchanged from HANDOFF-8, and out
of scope for the same reason: no client/adapter hook exposes `traceparent`.

---

## Process — one failure worth keeping

The commit that replaced the contrast stub **broke ten tests and was pushed red**,
and was reported as clean. What had actually been run was one test file; the engine
dependencies were not installed, so the full suite could not run at all — and that
was not stated until after the fact.

Two rules came out of it, and they are cheap:

* **Say what you actually ran, before committing, and flag it as partial.** A pass
  count from an environment missing dependencies is a partial view, not a result.
* **A pass count is only meaningful with a collection count.** `114 passed + 10
  skipped` looked fine and concealed thirteen tests that were never collected —
  `langchain`/`langgraph` were absent, so whole modules vanished rather than
  skipping visibly. Report `passed / skipped / collected`.

The engine arm (`requires_rtivity`, 10 tests) cannot run in a container without
`rtivity-python`. Anything depending on it — including whether a real contrast file
produces numbers, as opposed to parsing and validating — is **unverified** there and
must be confirmed locally or in CI before merge.

---

## Verification

In a container with `.[dev,telemetry,agent]` installed and **no** analysis engine.
Each branch was verified standalone, on top of `main`, not only in combination:

| | passed | skipped | collected |
|---|---|---|---|
| `main` (baseline) | 117 | 10 | 127 |
| PR A — pre-registration | 149 | 10 | 159 |
| PR B — run attribution | 138 | 10 | 148 |
| both applied | 170 | 10 | 180 |

`ruff 0.16.0` clean on every one. The two PRs touch no file in common, so
159 + 148 − 127 = 180 reconciles; the arithmetic is a check that neither branch
silently depends on the other.

**Partial, and this matters.** The 10 skips are `requires_rtivity` — the analysis
engine will not install in that container. `run_contrast` against real data is
**not exercised**, so a contrast file is shown to parse and validate, never to
produce numbers. The engine arm is covered only by CI and by a local run.

---

## Exact next steps

- **Commit a real `config/contrasts-<experiment>.yaml`.** Human, pre-registration
  task. Until one exists, no contrast can run at all — which is now the intended
  behaviour, not a bug.
- **Phase 1 (Ollama)**, in an environment with model-registry egress. Seam ready;
  the agent proxy 403s `ollama.com` / `registry.ollama.ai` / `huggingface.co` here.
- **`load_experiment`'s audit `session_id` is still null.** `run_id` makes the line
  attributable to a run; it does not make it joinable to the session that call
  created. Orthogonal, small, worth its own commit.
- **Phase 3 remains gated** on a real contrast file existing
  (`HANDOFF-6-amendment-1` §E). PR A builds the machinery the gate needs; it does
  not open the phase. When it does open, `AuditRecord.principal` is the seam —
  Phase 3.3 changes `default_principal()`'s body, never the field, and `run_id` is
  deliberately a separate axis so the two do not collide.

## Working agreements

TDD; scoped commits; `pytest -q` **and** `ruff check .` before declaring done; CI
green on 3.11/3.12/3.13; network- and key-dependent work is keyless-tested or
skipped, never left to fail intermittently.

**The verification-reporting rules that came out of the failure above now live in
`CLAUDE.md`, under "Reporting verification".** They were written here first, which
was the wrong home: a handoff is read once, when someone picks up the thread, and
these need to bind every session. The account of *why* stays here; the rule itself
is in `CLAUDE.md`. In short: say what you actually ran before committing and flag
it partial, report `passed / skipped / collected` because uncollected modules are
silent where skips are loud, say "parses and validates" rather than "works" for
anything the engine arm cannot reach, and ask before widening scope — the one
exception granted here was repair of self-inflicted breakage where the repair was
the correct fix anyway (`DAM_PREREG_PATH`).
