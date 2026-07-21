# Phase 1 spec — MCP server over the DAM analysis stack

**Goal:** expose your tested analysis functions as MCP tools so an agent can orchestrate
them without ever touching raw data or computing a number itself.

**Non-goal:** the agent. That's Phase 2. This phase ends with a server that any MCP
client can drive — which is also the gate.

---

## The five design rules

This is the whole spec. The tool list below follows from these; if you internalise the
rules you can extend the surface yourself without asking anyone.

### 1. Tools are task-shaped, not API-shaped

The instinct is to expose every public function 1:1. Don't. Rtivity-Python has dozens of
functions; forty tools with forty descriptions gives the model a haystack, and it will
flail, pick wrong, and chain badly. A tool is a *thing a scientist wants done*, not a
*thing your code can do*. Ten well-named tools beat forty faithful ones.

Concretely: `compute_sleep` is a tool. `_rle_encode_bouts` is not — it's an
implementation detail behind it.

### 2. The model never sees the data

**This is the rule that decides whether the project works.** If `load_experiment`
returns 4,320 × 32 counts as JSON, you have blown the context window, spent real money,
and the model still can't do anything useful with an undifferentiated wall of integers.

Every tool returns a **summary plus a handle**. The data stays server-side, in your
Python process, in the array types you already use. The model gets a session ID and a
few dozen tokens describing what happened.

This is also what makes the boundary honest: the model *cannot* compute a statistic
because it never has the numbers. Not "shouldn't" — can't. That's an architectural
guarantee you can state in an interview, and it's far stronger than a promise about
prompting.

### 3. Sessions, not statelessness

DAM analysis is a pipeline: load → align → QC → exclude → metrics → contrast. Each step
depends on the last. Passing state through the model each call is both expensive and
lossy.

Hold a `Session` object server-side keyed by `session_id`. The model passes the handle.
Persist sessions to disk so a server restart doesn't destroy an hour of work — you'll
hit this on day one otherwise.

### 4. Errors are prompts

Your error strings are read by a model deciding what to do next. This is a genuinely
different audience from a human reading a traceback.

- Bad: `ValueError: invalid literal for int() with base 10: ''`
- Good: `Column 10 is not 0/1 in Monitor3.txt — expected the light sensor there. This file may have a non-standard column count (found 39, expected 42). Ask the user to confirm the DAMSystem version.`

The second tells the model what happened, why it matters, and what to do. Write every
error this way. It costs nothing and it's most of the difference between an agent that
recovers and one that spins.

### 5. Descriptions are the API

The docstring is how the model decides whether to call the tool. It is not documentation;
it is dispatch logic. This is `@export` + roxygen, aimed at a model instead of a user —
you already have the instinct, just point it at a new reader.

State when to use it, when *not* to, and what it returns. "Use after `run_qc` and
`apply_exclusions`; results are invalid if exclusions haven't been applied."

---

## Tool surface

Ten tools, four resources. Grouped by what a scientist actually does.

| Tool | Args | Returns | Notes |
|---|---|---|---|
| `load_experiment` | `paths[]`, `name` | `session_id`, monitor count, channel count, time window, bin seconds, warnings[] | Never returns counts |
| `describe_experiment` | `session_id` | Inventory table: reads, expected, missing, bad status, per monitor | Step 1 of the QC skill |
| `run_qc` | `session_id`, `death_hours` | Tally (alive/empty/died/suspect), `decisions_required[]`, `report_uri` | Wraps `validate_dam.py`; respects the session window |
| `window_tradeoff` | `session_id`, `death_hours` | Candidate window-end → n-alive/died/empty/suspect curve | Read-only; informs the window choice |
| `set_analysis_window` | `session_id`, `start`, `end`, `death_hours` | Window + re-run QC tally + `decisions_required[]` | **Window before exclusions.** Refuses once exclusions are applied |
| `assign_groups` | `session_id`, `mapping{monitor:channel → group}` | Group sizes, unassigned channels | **Humans only.** Never let the model infer genotype |
| `apply_exclusions` | `session_id`, `exclusions[]`, `reason` | Updated n per group | The HITL gate. Requires confirmation |
| `list_contrasts` | `session_id` | Predefined contrast set from the design | See below — this one matters |
| `compute_sleep` | `session_id`, `immobility_minutes=5`, `by` | Total sleep, bout number, bout duration, latency — by group × phase, mean ± SD, n | Summaries only |
| `compute_activity` | `session_id`, `by` | Counts/waking-minute, total activity — by group × phase | |
| `compute_rhythmicity` | `session_id`, `method` | Period, power, rhythmic fraction per group | Chi-sq / Lomb-Scargle |
| `compute_survival` | `session_id` | Median survival, n at risk, curve summary | Uses the `died` calls from QC |
| `run_contrast` | `session_id`, `contrast_id` | Effect size, n per arm, test statistic, p, exclusions applied | Runs **one predefined** contrast |
| `render_report` | `session_id`, `path` | Written path | Write tool — confirm first |

### `list_contrasts` / `run_contrast` — the important pair

This is where "tell me what's interesting" gets replaced with something falsifiable, and
it deserves more than a table row.

An agent that can run arbitrary contrasts is an automated p-hacking machine. Give it 8
metrics × 5 groups × 2 phases and let it hunt for significance, and it will find some,
every time, and write a fluent paragraph about it. That system is not just useless —
it's actively harmful, and it's the version a reviewer will assume you built unless you
show otherwise.

So: the contrast set is **declared up front**, as part of the experimental design, in a
config file the model can read but not write.

```yaml
contrasts:
  - id: mut_vs_ctrl_sleep_night
    metric: total_sleep
    phase: dark
    groups: [CG8093_mut, w1118_ctrl]
    test: wilcoxon
  - id: mut_vs_ctrl_bout_night
    metric: mean_bout_duration
    phase: dark
    groups: [CG8093_mut, w1118_ctrl]
    test: wilcoxon
```

The model calls `list_contrasts`, runs each one, and reports which passed. It chooses
*which of your declared comparisons to run*, never *what to compare*. Pre-registration,
enforced by the tool boundary rather than by discipline.

Say this in the interview. "I constrained the agent to a pre-declared contrast set so it
can't p-hack" is a sentence that will land with someone who has deployed LLMs in a cancer
center and written about users being responsible for the veracity of the output.

---

## Resources — where the Q&A layer lives

Tools *do* things. Resources are *readable artifacts*. Your "ask it questions" feature
reads resources, not raw data:

```
dam://session/{id}/manifest      → files, window, groups, exclusions + reasons
dam://session/{id}/qc-report     → the QC report
dam://session/{id}/metrics       → computed metric tables
dam://session/{id}/contrasts     → contrast results
```

*"Why is n=27 for control?"* → the model reads the manifest → "Three channels empty, two
flies died on day 3, censored 21:04 03-03." Grounded, checkable, no inference.

This is the architectural answer to the confabulation problem: questions are answered
from computed artifacts, and the raw counts are not reachable from the model's side of
the boundary.

---

## Schemas

Pydantic models, typed returns. This is your roxygen discipline in a new syntax.

```python
from pydantic import BaseModel, Field

class QCTally(BaseModel):
    alive: int
    empty: int
    died: int
    suspect: int

class ChannelFlag(BaseModel):
    monitor: str
    channel: int = Field(ge=1, le=32)
    state: Literal["empty", "died", "suspect"]
    evidence: str = Field(description="Why this call was made — shown to the user")
    last_movement: datetime | None = None

class QCResult(BaseModel):
    session_id: str
    tally: dict[str, QCTally]          # keyed by monitor
    flags: list[ChannelFlag]
    decisions_required: list[str]
    report_uri: str
```

`evidence` is not decoration. Every flag carries its own justification, so the model can
explain a call without re-deriving it — and so the explanation is generated server-side
by your code, not invented by the model. Same principle as everything else here.

---

## Skeleton

`dam_mcp/server.py` — wire the bodies to your Rtivity-Python functions.

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("dam-tools")
SESSIONS: dict[str, Session] = {}

@mcp.tool()
def load_experiment(paths: list[str], name: str) -> dict:
    """Load DAM monitor files into a new analysis session.

    Use this first. Returns a session_id for all subsequent calls, plus a
    structural summary. Does NOT return activity counts — those stay server-side.
    Call run_qc next; metrics computed before QC are not trustworthy.
    """
    ...

@mcp.resource("dam://session/{session_id}/manifest")
def manifest(session_id: str) -> str:
    """Files, time window, group assignments, exclusions and their reasons."""
    ...
```

Start with **stdio transport**. Skip OAuth entirely — that's for remote servers and you
don't need one. Adding auth now costs a week and buys nothing you can demo.

---

## Build order

1. `load_experiment` + `describe_experiment` — proves the boundary. Nothing else works until the summary-not-data pattern is right.
2. `run_qc` — you already have the logic
3. `assign_groups` + `apply_exclusions` — the human gate
4. The four `compute_*` tools — thin wrappers, fast once the pattern is set
5. `list_contrasts` + `run_contrast`
6. Resources
7. `render_report` last

Steps 1–2 are the real work. After that it's repetition of a pattern you've established.

## Gate

**A client you didn't write discovers your tools and drives a full analysis.** Use MCP
Inspector or Claude Desktop. Point it at a real experiment folder and ask it to QC and
compute night sleep by genotype. If it gets there without you touching the code, Row 2 is
no longer a gap — you authored and operated a server, which is the language the posting
uses.

Second gate, harder and worth doing: **hand it a malformed file.** If the model recovers
using your error message, rule 4 is working. If it spins, your errors are written for
humans and need rewriting.

---

## Two notes

**Test count.** You have 229 tests over the analysis functions. The MCP layer needs its
own thin ones — schema validation, session lifecycle, error strings. Don't let the server
be the untested part of a tested repo; that's the one thing a reviewer of *your* profile
would notice, because your whole pitch is that you don't do that.

**Rtivity provenance is an asset — use it.** Rtivity is published software (Silva et al.,
*Sci Rep* 2022, Oliveira lab) built on Rethomics. You took a tool with an archived
dependency, repaired it, put it under version control for the first time, and rewrote it
in Python with tests and CI. That is research software engineering, described plainly.
It's a better story than "I wrote a package," because the hard part of RSE is
inheritance and maintenance, not greenfield. Make sure the framing is accurate in the
README — your work is the repair, the Python rewrite, and the test suite, not the
original method.

---

## Reconciliation with the implementation (Task 0)

Everything above this line is the original design hypothesis, written before the real
Rtivity-Python surface was on disk. The server was then built against the *actual* tested
functions, not the guessed API. This section records where the implementation differs
from the hypothesis, so the two do not silently diverge under a green test suite.

**The engine functions actually wrapped** (in `dam_mcp/engine.py`), none reimplemented:

| Tool | Rtivity-Python calls |
|---|---|
| `compute_sleep` | `annotate_sleep` → `detect_sleep_bouts` → `tst_mean`, `sleep_bout_duration_by_phase`, `sleep_bout_count_by_phase`, `sleep_latency_mean`, `waso_mean` → `activity.summary_stats` |
| `compute_activity` | `detect_activity_bouts`, `activity_by_phase`, `bout_duration_by_phase`, `bout_activity_by_phase`, a counts-per-waking-minute helper → `summary_stats` |
| `compute_rhythmicity` | `dominant_period_by_animal`, `periodogram_by_animal` (chi-square significance fraction) |
| `compute_survival` | `build_survival_data`, `survival_summary`, `logrank_test`, `flag_ambiguous_deaths` |
| `run_qc` / `describe_experiment` | subprocess to the tested `validate_dam.py` (single source of truth, not a reimplementation) |

Group assignment is built into the `DataStore` conditions table the tested code expects,
via a symlinked per-session working directory, so the whole compute path runs through the
229-test engine rather than around it.

**Contrast metric definitions** (`run_contrast`). The declared metric names map to concrete
per-animal statistics, reported in real units. The Wilcoxon test only sees ranks, so the
choice of scale does not change the p-value — but the reported median is in interpretable
units, not a fraction hiding behind the metric's name:

- `total_sleep` → mean **hours of sleep per day** in the requested phase (asleep bins ×
  bin width ÷ days). Conceptually TST; for the declared night-sleep contrast (phase =
  dark) this is the same endpoint as `compute_sleep`'s `total_sleep_time_h`.
- `mean_bout_duration` → mean sleep-bout duration in **minutes** for the phase.
- `counts_per_waking_minute` → total activity ÷ active bins, **counts per waking minute**.

**Other deltas worth knowing:**

- `death_hours` defaults to **24** everywhere in the code (matching the Skill and
  CLAUDE.md). A shorter window is an operator choice at call time for short runs, with the
  stated tradeoff (more late deaths caught, more deep-quiescence false positives).
- The QC return shapes the detector's JSON into `tally` (per monitor → state counts) and
  `flags` (only non-`alive` channels, each carrying its own `evidence` string).
- Return types are Pydantic with no array-shaped field: a metric summary is a map of
  scalar-only `SummaryRow` tables, so a raw series cannot occupy any field.
