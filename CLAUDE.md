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
