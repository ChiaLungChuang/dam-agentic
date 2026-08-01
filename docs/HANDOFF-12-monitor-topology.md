# HANDOFF-12 — monitor topology: three files, one population

**Written:** 2026-07-31 · **Base:** `main` at `5afc09f`

Read `CLAUDE.md` first, then `HANDOFF-11` for the run this invalidates part of.
**The finding is stated as found.** Nothing is fixed. `load_experiment`,
`assign_groups`, the schemas and the tests are untouched by deliberate instruction:
the feature this implies needs a design decision that has not been made.

---

## The finding in one paragraph

Each apparatus is **one rack of 32 vertical glass tubes passing through three
stacked detector boards**. `Monitor1`, `Monitor2` and `Monitor3` are not three
experiments, three racks, or three populations. They are **three IR beams at three
heights on the same 32 tubes, watching the same 32 flies** — the plug / middle /
food arrangement used for position tracking. Confirmed from photographs of the
apparatus. Every layer of this system — the loader, the grouping tool, the QC
detector, the metrics, the contrast machinery — treats a monitor file as an
independent set of animals, and on this rig that is false.

---

## What it costs

### 1. n is wrong by 3×, and every group statistic with it

In session `dam-7010fc5ebdc9`, `assign_groups` mapped three files to one group and
reported **96 per group**. Those are **32 animals counted three times**.

| | As reported | Actual |
|---|---|---|
| Channels | 384 | 384 |
| Animals | 384 | **128** |
| Per group | 96 | **32** |

Every group mean, SD and n from that run is affected. Not "slightly inflated" —
each animal contributes three correlated observations to a statistic whose n
assumes independence, which understates every standard error and is the classic
pseudoreplication error. No contrast was run on that session, which is the only
reason this did not reach a p-value.

### 2. Per-beam death detection is invalid on this rig

A channel's trailing zeros mean **the fly stopped visiting that height**, not that
it died. A fly that settles at the food and stops climbing goes silent on the plug
beam while remaining alive and visible on another. The death rule reads that as
mortality.

This is upstream of HANDOFF-11's Findings 1, 2, 5 and 6, and of the exclusion
record they are read from — all four were computed per beam. HANDOFF-11's Finding 2
(`death_hours` = 12 against a 12:12 cycle) remains a real defect in the classifier
on its own terms; what changes is that on this rig the classifier was also being
fed the wrong unit.

### 3. It explains two things the run could not

**`run_qc` reported 0 empty across 384 channels.** That looked like a suspiciously
clean result for twelve real monitor files. It is explained: no beam is silent when
the same animal is watched by three. A genuinely empty tube would have to be empty
on all three beams simultaneously, and the detector never sees the three together.

**The cross-monitor same-index correlation — HANDOFF-11's QUESTION 7 — is one fly
breaking three beams.** That question offered two readings: a one-minute clock
offset, or "something shared across monitors at the same channel index — a
hardware, wiring, or position effect". The second was right in substance and
understated in degree. It is not a shared confounder at a channel index; it is the
same animal. It is **not** duplication, leakage, or a hardware fault. QUESTION 7 is
closed by this document, and it did not need the second experiment it asked for.

---

## Evidence chain

1. **Photographs of the apparatus** show 32 vertical tubes passing through three
   stacked detector boards — the plug / middle / food arrangement. This is the
   primary evidence and it is external to the data.
2. **Channel indices repeat across monitors, to the minute.** Monitor2 ch10 and
   Monitor3 ch10 last move at 09:15 and 09:14; Monitor11 ch32 and Monitor12 ch32 are
   identical at 17:07; suspect flags align on matching channel indices within each
   block of three. Bin counts differ by exactly 1, 1 and 0. Recorded in HANDOFF-11
   QUESTION 7 before the cause was known.
3. **Files are confirmed raw downloads**, so the alignment is not a merge artefact.
4. **0 empty channels across 384** — a rate no real experiment produces, and exactly
   what three-way redundancy predicts.
5. **12 files = 4 apparatus × 3 beams**, and 4 × 32 = 128 animals, which matches the
   four declared groups at 32 each.

Each of 2–5 was visible in the run and none of them individually forces the
conclusion. The photographs do. Worth naming: the data-side evidence had been
sitting in HANDOFF-11 for two days as an open question, and the thing that settled
it was looking at the rig.

---

## What the tooling cannot currently do

**It cannot distinguish three populations from three views of one.** There is no
input anywhere in the system that carries this distinction:

* `load_experiment` takes a flat list of paths and derives `monitor_keys` from
  filenames. Nothing declares which files share a population.
* `assign_groups` maps monitor + channel range to a label. Assigning three beams to
  one group is indistinguishable, at the API, from assigning three racks to it.
* The declaration file (`DAM_PREREG_PATH`) declares group labels and contrasts. It
  has no vocabulary for apparatus, beam, or replicate structure.
* `QCResult.tally` is keyed by monitor. A per-animal tally has nothing to key on.
* Nothing in `SummaryRow` or `ContrastResult` can express a nested or repeated-
  measures design, so even a correct n could not be reported through the current
  return shapes.

Consequently the failure is **silent in both directions**: a rig where three files
really are three independent racks is handled correctly by accident, and this rig is
handled incorrectly by the same code path, with no signal distinguishing them. That
is the defect class this repo exists to remove — a number reported without the
context that makes it interpretable — and it is the largest instance found so far.

---

## What was NOT verified

* **No re-analysis was run.** The corrected n has not been pushed through
  `compute_sleep` or any contrast; nothing here reports what the numbers would
  become. The 3× claim is arithmetic on the design, not a measured result.
* **The beam-to-height mapping is not verified per file.** That Monitor1/2/3 are
  plug/middle/food *in that order* is the stated arrangement; which file is which
  height has not been confirmed from the data, and nothing here depends on it.
* **Whether every block of three in this session follows the same pattern** is
  inferred from 12 = 4 × 3 and from the paired last-movement times in two blocks.
  It has not been checked for all four blocks.
* **The correct statistical treatment is not decided.** Collapsing three beams to
  one animal, modelling beam as a repeated measure, and selecting one beam as
  canonical are all defensible and give different answers. This document does not
  choose.
* **Whether other rigs in this lab share the topology** is unknown. Do not assume
  the 3:1 ratio generalises; it is a property of an apparatus, not of DAM data.

---

## For HANDOFF-7's open list

Added as **H12-1**. Stated as an opinion and not as a finding: this is upstream of
every HANDOFF-11 item that touches n or death, so it outranks H11-2 in the ordering
HANDOFF-11 proposed. Deciding the design — how a session declares that files share
a population — comes before fixing anything downstream of it, because the fix to
each downstream item depends on which representation is chosen.

## Exact next steps

* **Do not run a contrast on session `dam-7010fc5ebdc9`.** Its n is 3× the animal
  count and the contrast machinery has no way to know. The guard here is
  structural rather than procedural: that experiment's declaration carries
  `groups:` and no `contrasts:`, so `run_contrast` has nothing to select and
  refuses whatever id it is given — this line records why, it is not what enforces
  it.
* **Decide the representation before writing code.** The open question is where the
  apparatus↔beam relationship is declared: in `load_experiment`'s arguments, in the
  pre-registration file, or in a new tool. It is a pre-registration-shaped fact — it
  describes the design, not the data — which argues for the declaration, but that is
  a decision, not a conclusion.
* **Whatever is chosen must fail loudly on the ambiguous case**, because both
  topologies are currently accepted in silence.
* **Add a corpus case to `damsim` that plants three-beams-on-one-population**, or
  the eval will keep scoring this class of error as clean.
