# HANDOFF-13 — wide topology: a proposal, not an implementation

**Written:** 2026-08-03 · **Base:** `main` at `e3054d9`

Read `HANDOFF-12-monitor-topology.md` first — this document assumes the finding
and does not restate the evidence for it.

**No code, no schema, no test changes.** This is a recommendation on four
decisions that gate everything downstream. Each carries the alternatives and the
reason for the choice, so disagreeing with a recommendation does not mean
re-deriving the question.

---

## What the rig is

One **machine** is a rack of 32 vertical glass tubes passing through **three
stacked detector boards**. Three monitor files per machine are three IR beams at
three heights on the same 32 tubes and the same 32 flies — plug, middle, food.
The 20251229 dataset is **four machines × three monitors = twelve files, 384
channels, 128 animals**.

`load_experiment` and `assign_groups` have no concept of this. So n inflates
threefold, and per-beam death detection is invalid: trailing zeros on one beam
mean the fly stopped visiting that height, not that it died.

## What the reference implementation does

dam-shiny's stage 1 reader returns `$plug`, `$middle`, `$food` and `$summed`.
**The three beams are named by role, not by index.** Dead-channel detection and
sleep analysis run on `$summed`. Per-beam data is retained for a separate stage
that infers position and zone.

Two facts in that worth separating, because they are independent decisions:

1. **The combination point.** Sum the three beams per tube, then run death
   detection and sleep on the summed signal.
2. **Retention.** Per-beam data is not discarded at the point of summing; a later
   stage consumes it.

---

## One thing already works, and it is evidence rather than argument

Before any of this is built, the declared-n checksum shipped in PR #13 already
refuses the multi-beam shape. Driven over the real stdio boundary against
synthetic files — one group label, three monitor files at channels 1–32 each, a
declared n of 32:

```
is_error: True
Declared-n mismatch: 'control_EtOH' declares n=32 but this mapping assigns
96 channel(s). ... Nothing was assigned.
```

A single-beam control on the same session proceeds and reports `n = 32`, so this
is not a guard that refuses everything.

**A checksum with no model of the apparatus catches the apparatus defect.** That
is worth stating precisely, because it bounds what topology support has to
achieve:

* it catches the case **where an n is declared**, and the declaration is optional;
* it catches it **at the point a human is already looking**, with a message naming
  both numbers;
* it does **not** fix the analysis. Overriding, or declaring 96, both proceed —
  and per-beam death detection stays invalid either way, because that defect has
  nothing to do with n.

So the checksum is a tripwire, not a solution. It buys the time to make the
decisions below rather than removing the need for them.

---

## (a) Where topology is declared

**Recommendation: the pre-registration declaration, as an optional `machines:`
block.**

| Option | For | Against |
|---|---|---|
| **Declaration** | Reviewable and timestamped by the same commit that pre-registers the design; the loader already has a refusal mechanism and a containment rule; one file per experiment already exists | Couples experimental design to hardware layout in one file; a lab that changes rigs mid-series must edit a pre-registration |
| `load_experiment` argument | Honest that this is a property of *this load*; no coupling to design | Not reviewable — nothing records what was passed; no refusal mechanism, so a wrong grouping is a typo with no guard; the model would be supplying a physical fact |
| Inferred from the data | Automatic, needs no human | **The tool guessing at a physical fact.** The signature (same channel indices, correlated last-movement times) is real but it is also what genuine replicates with a shared cabinet clock would look like. A wrong guess is silent and changes n |

The decisive argument is the one HANDOFF-9 already made for `groups:`. The check
that matters is not "did the caller pass the right argument" but "does the
declared design match what was assigned", and that check needs a declaration to
check *against*. An argument to `load_experiment` has nothing to be wrong
relative to.

The coupling objection is real and I do not think it is fatal: the machine layout
*is* part of the design, in the sense that it determines n, and n is
pre-registered. A design whose n depends on hardware the pre-registration does not
mention is already under-specified.

Inference deserves one more sentence because it is tempting: HANDOFF-12's evidence
chain has five links, four of them data-side, and **none of the four settles it.**
The photographs did. A tool with access only to the four would be guessing, and
this repo's rule is that it flags rather than decides.

**Shape sketch, illustrative only:**

```yaml
experiment: tau-geneswitch-young-2026-07
machines:
  M1: {plug: Monitor1.txt, middle: Monitor2.txt, food: Monitor3.txt}
  M2: {plug: Monitor4.txt, middle: Monitor5.txt, food: Monitor6.txt}
groups:
  control_EtOH: 32
```

Every monitor file appears exactly once across all machines; a file named twice,
or a machine missing a role, is refused at load. With `machines:` present,
`groups:` integers mean animals and the checksum compares against tubes rather
than channels — which is what the declared n was always supposed to mean.

## (b) What an undeclared topology defaults to

**Recommendation: default to independent, and warn loudly when the multi-beam
signature is present. Do not refuse.**

This is the recommendation I hold least confidently, and the precedent cuts
against it, so the reasoning matters more than the answer.

`DAM_PREREG_PATH` went to no-default-and-refuse on the argument that a stale
config should fail visibly rather than silently load the wrong declaration. **That
reasoning does not transfer**, for one reason: there, a default existed and was
*wrong for everyone* — loading a template made an unregistered comparison look
registered, in every case, with no correct reading. Here, "these files are
independent" is **correct** for every single-beam rig, which is the ordinary DAM
setup and most of the installed base. Refusing until declared would break every
existing declaration to protect against a layout that most users do not have.

That is not a comfortable answer, because HANDOFF-12 says plainly that the failure
is silent in both directions. So the default must not be silent:

* When a session's files show the multi-beam signature — repeated channel indices
  with correlated last-movement times across files — `load_experiment` **warns**,
  naming the files, saying what the two readings are, and pointing at `machines:`.
  Detection is used to *ask*, never to decide, which is the same line
  `decline_ratio` already draws.
* When `groups:` declares an integer n, the checksum in PR #13 already refuses the
  3× case outright. A lab that declares n gets a hard stop today.

The pair — hard refusal where an n is declared, loud warning where it is not — gets
most of the safety of refuse-until-declared without breaking a repo full of valid
single-beam declarations.

**If you would rather have the loud version:** refuse-until-declared, with
`machines:` required in every declaration and `machines: independent` as the
explicit opt-out. It is defensible, it is more in keeping with the
`DAM_PREREG_PATH` precedent than what I have recommended, and its cost is a
one-line edit to every existing declaration plus every test fixture. If the
installed base is one lab, that cost is near zero and I would switch.

## (c) Vocabulary

**Recommendation: role names — `plug` / `middle` / `food` — as a closed set.**

Indices are portable and generic; roles carry meaning and are checkable against
the rig. Three arguments for roles:

1. **They can be wrong, and wrongness is the point.** `beam: 2` is unfalsifiable —
   any file can be beam 2. `middle: Monitor7.txt` is a claim someone can check
   against the apparatus. This repo's whole method is making claims checkable.
2. **The reference implementation names them**, and a Python layer whose vocabulary
   diverges from the R stage-1 reader's makes every cross-check a translation.
3. **Downstream analysis needs the role, not the position.** Position and zone
   inference — dam-shiny's stage 2 — is *about* which height, so an index would be
   renamed to a role the moment that work started.

The portability objection is answerable: this is a closed vocabulary like `PHASES`
and `METRICS`, so a rig with a different arrangement (two beams, five beams) adds
names to the set in one place, with the same refusal machinery. A rig whose beams
genuinely have no roles can use `beam_1`… as labels — the loader does not care
what the strings are, only that they are declared and unique.

Not recommended: making role names load-bearing in the *analysis*. Summing does
not need to know which beam is which; only stage-2 position inference does. Roles
should be labels the loader validates, not switches the maths reads.

## (d) Where summing happens, and what happens to per-beam data

**Recommendation: sum in the engine, at read time, behind the existing session
boundary. Retain per-beam data on disk, which costs nothing because it is never
copied.**

Where, concretely:

* `load_experiment` reads `machines:` and records, per session, which files form
  which machine and which role each plays. It does **not** sum. The manifest still
  reports twelve files, because twelve files is what was loaded and the record
  should say so.
* The engine's read path — the one place that touches raw counts — sums the three
  beams per tube before anything else runs. Everything downstream (`run_qc`,
  `window_tradeoff`, `compute_*`) then operates on 32 channels per machine without
  knowing summing happened.
* `assign_groups` maps groups to **machine + tube**, not monitor + channel. This is
  the visible interface change and the one that fixes n: three beams can no longer
  be assigned as three groups' worth of animals because the beam is not addressable
  at that layer any more.

Why the engine and not `load_experiment`: the architectural rule is that raw counts
never leave the engine. Summing is an operation on raw counts. Doing it at load
would either drag counts into the session layer or force a second read path, and
the session's job is to hold decisions, not data.

**Per-beam data: retained, not dropped.** It is retained by default because the
files on disk *are* the retention — nothing is copied, cached or serialised, and
the engine can re-read any single beam on demand. dam-shiny keeps it for stage-2
position inference and dam-agentic should not foreclose that. What it must not do
is expose per-beam counts through a tool return; a future `compute_position` would
return a summary and a handle like everything else.

**One consequence to accept explicitly.** Summing changes what "zero" means. A
summed channel reads zero only when the fly crossed no beam at any height, which
is a *stronger* death signal than any single beam — so it does not merely fix the
inflated n, it makes the death rule mean what it was always documented to mean.
It does not, however, fix HANDOFF-11's Finding 2: `death_hours=12` against a 12:12
cycle still collapses "asleep" into "dead" on the summed signal. **These are two
defects and topology only closes one.**

---

## What this does to the existing record

HANDOFF-11's findings were computed per beam, and HANDOFF-11 already carries a top
note saying so. That note stands and this document does not change it. What
changes, per finding, if topology is implemented:

| Finding | Status once summing exists |
|---|---|
| **F1 — `window_tradeoff` non-monotonic** | **Must be recomputed.** The curve was over 384 per-beam channels. The mechanism (threshold = dark phase) is independent of beams, so the shape will likely survive — but the numbers in the table are not the numbers a summed run produces, and the finding must not be quoted as though they are |
| **F2 — `death_hours` 12 vs LD 12:12** | **Unaffected and still open.** Nothing about summing changes the coincidence. This remains the first thing to fix and topology does not touch it |
| **F3 — `compute_*` runs with QC decisions outstanding** | **Unaffected in kind, changed in degree.** The 63 `decisions_required` and the 26/10/18/9 split are per-beam counts and would be recomputed; the defect — a precondition nothing enforces — is untouched |
| **F4 — latency without its denominator** | **Unaffected in kind.** 52/71/21/18 of 96 are per-beam; the denominators change, the missing-denominator defect does not |
| **F5, F6 — the exclusion record** | **Must be revisited.** Both are read off exclusions taken per beam, and M9 ch16 is a beam, not a tube. Whether that channel is an empty tube depends on all three beams |
| **Q7 — repeated channel indices** | Already closed by HANDOFF-12 |
| **Q8 — sleep units unverified** | **Unaffected.** Arithmetically impossible over one interval regardless of how many beams fed it |

The pattern: **topology changes the numbers in HANDOFF-11 and changes almost none
of its findings.** Four of six are about a tool reporting a number without the
context that makes it interpretable, and that defect class is orthogonal to how
many beams produced the number.

Session `dam-7010fc5ebdc9` stays as it is — it is the record of the run that
produced the finding, and it is not re-analysed. No contrast can run on it in any
case: its declaration carries `groups:` and no `contrasts:`, so `run_contrast` has
nothing to select. That guard is structural, not procedural.

---

## What is NOT decided here

* **The migration.** If `machines:` lands, every existing declaration and test
  fixture keeps working under recommendation (b) and breaks under the alternative.
  Which of those two is chosen determines whether this is additive or a migration,
  and that is decision (b), not a separate one.
* **Whether other rigs in this lab share the topology.** Unknown, and HANDOFF-12
  says not to assume the 3:1 ratio generalises. `machines:` should not hardcode
  three.
* **Stage-2 position inference.** Out of scope entirely. Named only so the
  retention decision in (d) does not foreclose it.
* **What the corrected numbers are.** No re-analysis was run and none is proposed
  here. Every "must be recomputed" above is a statement about validity, not a
  prediction about direction or size.

## Exact next steps

1. **Answer (b) first.** It is the only one that decides whether this is additive
   or a migration, and it changes the cost of everything else.
2. Then (a) and (c) together — they are one file-format decision.
3. (d) is implementation and can follow, but the `assign_groups` interface change
   (machine + tube rather than monitor + channel) should be agreed before it
   starts, because it is the visible one.
4. Add a `damsim` corpus case that plants three-beams-on-one-population. The
   generator can now emit declarations (H13-2), so a machine block is expressible;
   without a planted case the eval will keep scoring this class as clean.
