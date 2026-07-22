---
name: dam-qc
description: Quality-control and validate TriKinetics Drosophila Activity Monitor (DAM) data before any downstream sleep or locomotion analysis. Use this skill whenever the user mentions DAM data, TriKinetics, Monitor*.txt files, fly activity/locomotion data, sleep bouts, beam breaks, actograms, or ZT/light-dark phase analysis — and also whenever a user asks to analyze fly behavior data without explicitly asking for QC, because raw DAM data is never trustworthy on arrival and skipping QC silently corrupts every downstream metric.
---

# DAM Data Quality Control

TriKinetics DAM monitors are reliable hardware attached to an unreliable workflow. The
data almost always arrives with defects that are invisible in summary statistics but
fatal to sleep metrics: monitors started minutes apart, a truncated final bin, tubes
that were never loaded, flies that died on day 3 and look like perfect sleepers ever
after.

None of these defects throw errors. They produce plausible-looking numbers. A dead fly
scores as 100% sleep; a staggered start silently shifts one monitor's ZT by 7 minutes;
a partial final minute drags the last bout's duration toward zero. The entire purpose
of this skill is to catch these before they enter an analysis, because once they're in,
they are nearly impossible to see.

Run QC first. Always. Report what you found. Never silently repair.

## Core principle

**Flag, don't fix.** Every exclusion is a scientific decision with consequences for
n and for the interpretation. Surface each one to the user with the evidence and let
them decide. The only exception is deterministic mechanical alignment (see Step 2),
which has one correct answer and no judgment call in it.

When in doubt, report and ask.

## The workflow

### Step 1 — Inventory and parse

Read every monitor file the user points at and report the shape of the data before
touching it:

- File count, monitor numbers, channels per monitor (expect 32 for DAM2)
- First and last timestamp per monitor
- Bin width (expect 1 min; anything else is a red flag worth surfacing immediately)
- Total reads per monitor vs. expected reads from the timespan
- Status column: any read where status != 1 is suspect

Report as a table, one row per monitor. Discrepancies between monitors in this table
are the single most informative early signal — if one monitor has 4,312 reads and its
neighbour has 4,305, something happened, and it is better to know now.

See `references/dam-file-format.md` for the column layout.

### Step 2 — Alignment (staggered starts and the partial final bin)

Monitors are started by hand. They do not start together. Two defects follow:

**Staggered starts.** Monitor A begins at 09:03:00, Monitor B at 09:07:00. Naively
concatenating by row index misaligns them by 4 minutes, which silently smears every
light-transition-anchored metric across monitors.

Align on *wall-clock timestamp*, never on row index. Trim all monitors to the latest
common start and the earliest common end. Report how much data each monitor lost to
trimming — if one monitor loses 40 minutes, the user needs to know that.

**Partial final bin.** The last reading is usually a fraction of a minute — the monitor
was stopped mid-bin. Its count is an undercount of a full minute, but it is recorded
identically to a full bin. Left in, it deflates the final bout.

Drop the final bin unless it can be shown to be complete. Report that you dropped it.

Both of these are mechanical and have exactly one correct answer, so they can be applied
without asking — but always report what was done.

> **Lab-specific:** Confirm whether this lab trims to common start/end or pads with NA.
> The choice affects n per time bin. Fill this in once and it stops being a question.

### Step 3 — Channel status classification

Classify every channel into exactly one of four states. This is where most silent
corruption lives, and the distinction that matters most is **empty vs. died**, because
an empty tube should never have been in the denominator and a dead fly should be
censored at time of death — not excluded from the whole run, and certainly not scored
as a sleeping fly.

| State | Signature | Action |
|---|---|---|
| `alive` | Activity distributed across the run | Keep |
| `empty` | Zero counts for the entire run | Exclude; not an n |
| `died` | Normal activity, then zero to the end of the run | Censor at last movement; report time of death |
| `suspect` | Extremely low but nonzero; or implausibly high | Flag for human review |

To separate `died` from `empty`, look for *any* activity in the run: an empty tube is
zero throughout, a dead fly has a live period first. To find the death point, take the
last nonzero reading and check that everything after it is zero for a sustained window.

> **Lab-specific:** Set the death threshold (this lab uses 12 h of continuous zero— confirm before analysis) and the low-activity suspect cutoff. A 12 h window means flies dying in the final 12 h cannot be distinguished from deep quiescence — state that limitation rather than hiding it.

Report a per-monitor tally: how many alive, empty, died, suspect. If more than a few
channels per monitor are `empty`, the loading sheet probably doesn't match the data,
which is worth raising directly.

### Step 4 — Light schedule and ZT

Every circadian metric depends on the light schedule being right, and the light status
column is a frequent source of quiet error — monitors get started before the incubator
schedule is confirmed, or a DD run is annotated as if it were LD.

- Read the light status column and report the observed schedule (e.g. LD 12:12)
- Check that transitions occur at consistent clock times across the whole run
- Confirm ZT0 is anchored to lights-on
- Flag any run where the observed schedule disagrees with what the user stated

If the monitor's light column disagrees with the stated protocol, stop and ask. Do not
guess which one is correct — this is exactly the kind of thing that is cheap to resolve
now and expensive to discover in review.

> **Lab-specific:** Record the standard schedule and the ZT0 convention here.

### Step 5 — Plausibility checks

Cheap checks that catch expensive mistakes:

- Total activity per channel — flag outliers against the run's own distribution rather
  than against a fixed threshold, since counts vary by genotype, age, and monitor
- Bout structure sanity: a fly with zero bouts, or one continuous 24 h bout, is broken
  data, not biology
- Duplicate timestamps within a monitor
- Time gaps: any missing minutes in the sequence
- Clock drift between monitors over long runs

### Step 6 — Report

Produce a QC report with this structure:

```
# DAM QC Report — <experiment id>

## Files
<inventory table from Step 1>

## Alignment
Common window: <start> to <end>
Trimmed per monitor: <table>
Final bin dropped: yes/no

## Channels
<alive / empty / died / suspect tally per monitor>
<per-channel detail for anything not `alive`>

## Light schedule
Observed: <e.g. LD 12:12, ZT0 = 09:00>
Consistent across run: yes/no

## Plausibility
<flags raised>

## Decisions required
<numbered list of things needing a human call>
```

End every report with the decisions-required list, even when it's empty. It is the part
the user actually needs, and burying it under the tables defeats the purpose.

## Notes on downstream analysis

If the user continues into sleep analysis, carry the QC state forward — exclusions must
propagate, or the QC was theatre. The standard sleep definition is 5 minutes of
continuous inactivity (Hendricks et al. 2000; Shaw et al. 2000); confirm the lab uses
this before applying it.

## Scripts

- `scripts/validate_dam.py` — runs Steps 1–5 and emits the report as JSON

The script is deterministic and does not need to be read into context to be run. Read it
only if it needs modification.
