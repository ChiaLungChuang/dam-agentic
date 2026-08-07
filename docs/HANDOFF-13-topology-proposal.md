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

One **machine** is a rack of 32 horizontal plastic tubes passing through **three
stacked detector boards**. Three monitor files per machine are three IR beams at
three positions along the same 32 tubes and the same 32 flies. The 20251229 dataset is
**four machines × three monitors = twelve files, 384 channels, 128 animals**.

`load_experiment` and `assign_groups` have no concept of this. So n inflates
threefold, and per-beam death detection is invalid: trailing zeros on one beam
mean the fly stopped visiting that region of the tube, not that it died.

**The gap this closes is not "we cannot detect beams."** It is that *no input
anywhere carries the relationship between files* — so three racks and three beams
are accepted identically, and in silence. Making both directions **sayable** is
the fix. Detecting one of them is not.

### The 20251229 layout, as recorded

Within a machine the **first** monitor is the food end, the second is central, the
third is the plug end. The pattern repeats every three:

| Machine | Food end | Central | Plug end | Group |
|---|---|---|---|---|
| 1 | Monitor1 | Monitor2 | Monitor3 | `control_EtOH` |
| 2 | Monitor4 | Monitor5 | Monitor6 | `control_RU` |
| 3 | Monitor7 | Monitor8 | Monitor9 | `tau_EtOH` |
| 4 | Monitor10 | Monitor11 | Monitor12 | `tau_RU` |

"Central" and dam-shiny's `middle` are the same slot; this document uses `middle`
as the key because that is what the reference implementation calls it.

**Note the direction: file order runs food → plug, which is the reverse of the
order the `$plug, $middle, $food` object naming invites.** An earlier draft of
this document had it backwards for exactly that reason. The error is left visible
in recommendation (c), where it is the evidence.

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

**The reference implementation does not solve this, and that is the sharpest
argument here.** In dam-shiny the role assignment is correct because the operator
assigns files to named slots correctly, by hand — and **nothing in that app or its
log records which file went into which slot.** The log prints the upload datapath,
not the filename. So the relationship is preserved by a human convention that
leaves no trace: it is right every time until it is wrong once, and when it is
wrong there is no artifact that disagrees.

That is the *same unrecorded-fact shape as the topology gap itself*, one layer up.
dam-shiny is being borrowed here for its **combination step** — sum the beams, run
death detection and sleep on the summed signal — and explicitly **not** as a model
for how the relationship gets recorded. On that question it is the thing being
fixed, not the thing being copied.

Inference deserves more than one sentence because it is tempting, and because
rejecting it *as a decider* is not the same as having no use for it.

HANDOFF-12's evidence chain has five links, four of them data-side, and **none of
the four settles it.** The photographs did. A tool with access only to the four
would be guessing, and this repo's rule is that it flags rather than decides. So
inference must not choose the topology.

**But it makes a good cross-check, and that is a real job — see the follow-on
below.** The declaration decides; the data says whether the declaration is
plausible. That division is exactly the one this repo already draws everywhere
else, and it is what makes a declared topology *checkable* rather than merely
recorded.

**Shape sketch, illustrative only:**

```yaml
experiment: tau-geneswitch-young-2026-07
machines:
  M1: {food: Monitor1.txt,  middle: Monitor2.txt,  plug: Monitor3.txt}
  M2: {food: Monitor4.txt,  middle: Monitor5.txt,  plug: Monitor6.txt}
  M3: {food: Monitor7.txt,  middle: Monitor8.txt,  plug: Monitor9.txt}
  M4: {food: Monitor10.txt, middle: Monitor11.txt, plug: Monitor12.txt}
groups:
  control_EtOH: 32
```

Every monitor file appears exactly once across all machines; a file named twice,
or a machine missing a role, is refused at load. With `machines:` present,
`groups:` integers mean animals and the checksum compares against tubes rather
than channels — which is what the declared n was always supposed to mean.

## (b) What an undeclared topology defaults to

**Recommendation: default to independent, and warn on the *declaration* being
absent — not on the data looking multi-beam. Declaring topology either way
silences it. Do not refuse.**

### Why the warning fires on the declaration, not the shape

A first draft of this section put the warning on the data signature: repeated
channel indices with correlated last-movement times across files. **That was
wrong, and the repo had already decided why.**

PR #6 set the principle. `declaration_warnings()` stays quiet when a `groups:`
mapping carries no values, because there is no ignored input to warn about, and a
warning where nothing is wrong teaches the reader to skip warnings — including the
one that matters. A signature-triggered warning fails that test outright: **it
fires on every legitimate three-rack rig**, because HANDOFF-12's own finding is
that the two shapes are *indistinguishable from the data*. A three-rack lab would
see it on every run forever with no way to silence it, and a warning that correct
usage cannot clear is a warning people learn to ignore.

Warning on the absence of a declaration is a different claim, and a better one:

| | Signature-triggered | Declaration-triggered |
|---|---|---|
| What it asserts | "your hardware looks like three beams" — a guess | "you have not said how these files relate" — a fact the tool knows |
| Can it be wrong? | Yes, on every three-rack rig | No. It is a statement about the declaration |
| Can correct usage silence it? | **No** | Yes, in one line, either direction |
| Existing declarations | unaffected but permanently noisy | unaffected, quiet after one edit |

So: **`load_experiment` warns when a session loads more than one monitor file and
the declaration says nothing about topology.** Two ways to clear it, and both are
true statements someone can be held to:

```yaml
machines:                                   # a beam rig
  M1: {food: Monitor1.txt, middle: Monitor2.txt, plug: Monitor3.txt}
```
```yaml
machines: independent                       # a rack rig — each file its own animals
```

The single-file case is scoped out, and that is not shape-inference sneaking back
in: with one monitor there is no relationship between files to declare, so the
question does not arise.

**This is what actually closes HANDOFF-12's silent-in-both-directions gap.** That
finding was not "we cannot detect beams" — it was that *no input anywhere carries
the relationship*, so three racks and three beams are accepted identically and in
silence. Making both directions **sayable** is the fix. Detecting one of them is
not.

### Why soft rather than refuse: the loud path already exists

The stronger argument for not refusing is not "most rigs are single-beam". It is
that **the design is already two-tier, and the loud tier shipped in PR #13**:

| Declaration | Behaviour on a three-beam mapping | Status |
|---|---|---|
| `groups:` declares an integer n | **Hard refusal**, naming the group, the declared n and the computed n | **Works today** — see the transcript above |
| `groups:` declares no n | Warning that topology is undeclared | Proposed here |

A lab that declares an n gets a hard stop **now**, with no topology code in the
repo. Refuse-until-declared would add a second hard stop for people who already
have one, and impose it on people who declared nothing precisely because they had
nothing to declare. The tier that is missing is the *soft* one, and that is the
one this recommends.

`DAM_PREREG_PATH`'s no-default-and-refuse precedent still does not transfer, for
the reason it never did: there a default existed and was *wrong for everyone* —
loading a template made an unregistered comparison look registered, in every case,
with no correct reading. Here "independent" is **correct** for every single-beam
rig. But that argument is now the second one, not the first.

**If you would rather have the loud version anyway:** require `machines:` in every
declaration, with `machines: independent` as the explicit opt-out. Its cost is a
one-line edit to every existing declaration and every test fixture; if the
installed base is one lab that is near zero, and I would not argue hard against
it. What it buys over the recommendation is the case of a beam rig that declares
no n *and* ignores the warning — which the warning at least names, and which
nothing at all names today.

## (c) Vocabulary

**Recommendation: role names — `plug` / `middle` / `food` — as a closed set.**

Indices are portable and generic; roles carry meaning and are checkable against
the rig. Three arguments for roles:

1. **They can be wrong, and wrongness is the point.** `beam: 2` is unfalsifiable —
   any file can be beam 2. `middle: Monitor8.txt` is a claim someone can check
   against the apparatus. This repo's whole method is making claims checkable.
2. **The reference implementation names them**, and a Python layer whose vocabulary
   diverges from the R stage-1 reader's makes every cross-check a translation.
3. **Downstream analysis needs the role, not the position.** Position and zone
   inference — dam-shiny's stage 2 — is *about* which position, so an index would be
   renamed to a role the moment that work started.

**Argument 1 is not hypothetical, and this document is the instance.** The example
above read `middle: Monitor7.txt` in the first draft. Monitor 7 is a **food end**;
machine 3's middle is Monitor 8. The whole beam order was mirrored, because
`$plug, $middle, $food` was given in listed order and read as file order.

That error was caught **by reading the role against the rig** — which is the only
thing that could have caught it. Written as `beam: 2`, the same mirrored ordering
would have carried through silently and been unfalsifiable on its face: any file
can be beam 2, so there is nothing to check and nothing to be wrong. A vocabulary
whose errors are detectable is worth more than one whose errors do not exist
because it cannot make claims.

**Blast radius of that mirroring, stated so nobody over-scopes the fix: summing is
commutative.** A mirrored role order does not change death detection, sleep, or
any metric computed on the summed signal — those are identical whichever way round
the three beams are read. It changes **zone assignment and position inference
only**, which is stage-2 work that does not exist here yet. The correction matters
for the record and for whoever builds stage 2; it changes no number in this repo.

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

**The order, stated once and explicitly, because it has been described several
times and appears nowhere in this document:**

> Sum the three monitors of a machine first. Run QC and death detection on the
> summed signal. Remove all dead animals. Only then does analysis proceed — either
> on the three monitors together, or on them separately with the position
> labelled.

**Why the summing must come first is sharper than "per-beam death detection is
invalid".** It does not produce an invalid result; **it produces a contradictory
one.** The same animal reads dead at the plug end and alive at the centre and food
end. There is no way to reconcile three verdicts about one fly at the point they
are made, and nothing downstream is told there was a disagreement.

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

**An exclusion set computed on summed data ports back to per-beam data directly,
and no linkage mechanism is needed.** An earlier version of this reasoning said the
opposite — that a summed-data exclusion set has nothing to flow back to. That was
wrong, and it is corrected here so it does not propagate. The exclusion is a list
of **dead channel indices**, and all three monitors of a machine share the same 32
tube indices, so tube 7 is tube 7 on all three files. Nothing has to be built to
connect them.

What a pre-summing approach actually lacks is different, and smaller: **the record
of which three files became which machine.** That is *provenance*, not coupling —
the same unrecorded-fact problem this whole document is about, not a data-linkage
problem. Do not build a linkage mechanism for the exclusions.

**Whatever retains per-beam data must retain the ROLE, not the file order.** This
is not a detail. An ordering convention that lives in how someone happened to
upload or sort the files is precisely the class of unrecorded fact this whole
document exists to remove — it is the dam-shiny slot-assignment problem and the
topology gap, arriving a third time. `Session` must carry `{machine, role, file}`,
and anything downstream must key on `role`; a positional index into a list of
three is the same defect wearing a different name. The mirrored-order error in
(c) is what that failure looks like when the ordering convention is wrong and
nothing records it.

**One consequence to accept explicitly.** Summing changes what "zero" means. A
summed channel reads zero only when the fly crossed no beam at any position, which
is a *stronger* death signal than any single beam — so it does not merely fix the
inflated n, it makes the death rule mean what it was always documented to mean.
It does not, however, fix HANDOFF-11's Finding 2: `death_hours=12` against a 12:12
cycle still collapses "asleep" into "dead" on the summed signal. **These are two
defects and topology only closes one.**

## Follow-on: a `run_qc` check that the declaration matches the data

**In scope for this document, as a follow-on to (a)+(b) rather than part of
either.** It is not needed for topology to work, and it must not gate it — but
without it a declared topology is recorded and never checked, and this repo's
method is making claims checkable.

The division of labour, stated so it cannot drift:

* **The declaration decides.** `machines:` or `machines: independent` is the
  answer, and nothing in the data overrides it.
* **`run_qc` flags disagreement.** Same-index cross-monitor correlation is a
  measurable property. A session declared `independent` whose files show strong
  same-index enrichment between adjacent monitors and none across declared
  boundaries is *worth surfacing* — not because the tool knows better, but because
  one of the two is wrong and only a human can say which.

The shape of the evidence, from HANDOFF-12's Question 7: enrichment of matched
last-movement times at the same channel index, high within a block and at baseline
across blocks. A concrete signature would read as something like **4.6× at
distance 1 and 1.0× across a declared boundary** — the ratio, not either number
alone, is what carries information, because a shared cabinet clock inflates both.

### A second thing for it to check: the activity gradient

Once `machines:` declares a role *order*, the data has an opinion about whether
that order is right.

Within every machine in the 20251229 data, per-channel activity **declines
monotonically from the first monitor to the third** — roughly **120 of the 128
channel-triples strictly descending, against about 21 expected under
independence**. That is consistent with the first beam being the food end, where
flies aggregate. So **a declared role order that runs against the activity
gradient is worth flagging.**

**The tube geometry removes one rival explanation and leaves one standing.** With
**horizontal** tubes there is no gravity axis, so negative geotaxis — which a
reader could otherwise invoke against a vertical rack — cannot account for the
gradient at all. It never applied here. **Food proximity is the only behavioural
account left**, which is what makes the gradient evidence for the first board
being the food end rather than merely consistent with it.

**Stated as a candidate all the same, because one confound survives.** A
detector-sensitivity gradient across the three boards would produce the identical
pattern, and that has never been ruled out.
Board-to-board sensitivity differences are a mundane hardware fact and nothing
here distinguishes them from a genuine behavioural gradient. Ruling it out would
need something like a swap test — the same flies recorded with the boards
reordered — which nobody has run.

So if it ships, it ships as **"the declaration and the data disagree, look at
this"** with both numbers attached, and never as a rule that overrides a
declaration or reorders roles on its own. The failure mode of the strong version
is exact: a rig with an unusual sensitivity profile would have its correct
declaration silently contradicted, and a reader would trust the tool over the
apparatus.

### Where it lives

Why `run_qc` and not `load_experiment`: both checks are statements about the data,
and `run_qc` is where statements about the data are made and surfaced as
`decisions_required`. `load_experiment` reports structure.

Why it is not the same as inference-as-decider, in one line: **a cross-check can
only ever raise a question, and it raises it against a claim someone already
made.** Inference-as-decider would answer the question with nothing to check
against.

Two things it must not become:

* A check that fires when *no* topology is declared. That is (b)'s warning and it
  already covers the case; duplicating it here would put two warnings on one
  situation.
* A threshold that silently reclassifies. It produces a `decisions_required`
  entry with the numbers attached, never a changed n and never a reordered role.

**A third candidate, evaluated and demoted.** On a real multi-beam rig, per-beam
death calls would **concentrate on the plug-end board** while the same channels
read alive on the food-end board. On genuinely independent racks, death is
independent across channels, so a *directional* pattern across boards has no
innocent explanation — which is what makes it look like a strong signal.

**It is not an independent one.** A less sensitive third board produces exactly
the same signature: fewer counts, more trailing zeros, more death calls, all
concentrated on that board. That is the same confound as the activity gradient,
and the directional death pattern is the activity gradient in sharper form rather
than a new observation. It should be reported as an aspect of that check, not as a
second line of evidence — two derived views of one measurement quoted separately
would overstate the case exactly as pseudoreplication does.

Summary of what the cross-check watches, both candidates:

| Signal | Checks | Confounded by |
|---|---|---|
| Same-index cross-monitor enrichment | whether files declared independent are in fact one population | a shared cabinet clock — which is why the *ratio* within vs across boundaries carries the information, not either number |
| Activity gradient down the stack | whether the declared role **order** is right | detector sensitivity varying by board, never ruled out |

Also worth stating: **`damsim` can plant this now.** With declaration emission in
place (H13-2), a corpus can carry a `machines:` block and monitor files whose
same-index structure either matches it or does not — so the check is testable
against planted ground truth rather than only against the one real dataset.

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
5. The `run_qc` declaration-vs-data cross-check, last. It depends on a declaration
   existing to check against, so it cannot precede (a), and it is the piece that
   turns a declared topology from recorded into checkable.
