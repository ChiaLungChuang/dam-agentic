# HANDOFF-11 — the first real run, and what it exposed

**Written:** 2026-07-29 · **Base:** `main` at `650e1a2`

Read `CLAUDE.md` first, then `HANDOFF-10` for the format. **Findings are stated as
found.** Nothing here is fixed except one docstring note, flagged inline, because a
handoff that reads as though the code was already right destroys the evidence that
the run found anything.

> **Read this before using anything below. Added 2026-07-31; see
> `HANDOFF-12-monitor-topology.md`.**
>
> Every finding in this document was computed **per beam**. On this rig that is not
> the same as per animal: Monitor1, Monitor2 and Monitor3 are three IR beams at
> three heights on one apparatus of 32 tubes, watching the **same 32 flies**. So
> the 384 channels of this session are 128 animals, not 384, and the four groups
> are 32 animals each, not 96.
>
> **Per-beam death detection and per-beam window analysis are not valid for this
> rig.** Trailing zeros on one beam mean the fly stopped visiting that height, not
> that it died — which is upstream of Findings 1, 2, 5 and 6, and of the exclusion
> record they are read from. Question 7 is answered by the same fact and is closed
> below.
>
> The findings are kept **verbatim, as found**. They are not rewritten and not
> withdrawn: they are accurate descriptions of what the tooling did, and the
> tooling did it. What changes is what they license you to conclude about the
> flies.

---

## Status in one paragraph

The server was driven end-to-end against a real experiment for the first time: 12
raw TriKinetics monitor files, 384 channels, 161.8 h (6.74 days), session
`dam-7010fc5ebdc9`, declaration `config/tau-geneswitch-young-2026-07.yaml`. The
pipeline executed — `load_experiment` → `run_qc` → `window_tradeoff` →
`assign_groups` → `compute_sleep` — then 13 deaths were excluded and sleep
recomputed. **It ran. That is not the same as it being right.** Six defects and two
unresolved questions came out of a single session, and none of them were reachable
by the synthetic corpus: every one needs a real light-dark cycle, real mortality, or
real per-monitor clocks.

The pattern worth naming up front: **four of the six defects are a tool reporting a
number without the context that makes it interpretable.** Not wrong arithmetic —
missing denominators, missing preconditions, missing definitions. The synthetic eval
cannot catch that class, because a generator that plants the ground truth also
supplies the context.

---

## The run

| | |
|---|---|
| Session | `dam-7010fc5ebdc9` |
| Declaration | `config/tau-geneswitch-young-2026-07.yaml` |
| Monitors | 12 raw TriKinetics files, confirmed raw downloads |
| Channels | 384 (12 × 32) |
| Reads per channel | 9,708 |
| Bin width | 60 s |
| Span | 161.8 h ≈ 6.74 days |
| Groups | 4 × 96 channels |
| QC | 63 `decisions_required`, flags by group 26 / 10 / 18 / 9 |
| Exclusions | 13, all under a death reason |

Reads per channel and bin width were measured directly from the twelve raw monitor
files on the source machine; those files are not committed to this repo.

---

## Tool findings

### FINDING 1 — `window_tradeoff` is non-monotonic, and its own note said it could not be ⚠

The six rows returned:

| hours | n_alive | n_died | n_empty | sum |
|---|---|---|---|---|
| ~27 | 354 | 30 | 0 | 384 |
| ~54 | 142 | 153 | 89 | 384 |
| ~81 | 105 | 144 | 135 | 384 |
| ~108 | 188 | 76 | 120 | 384 |
| ~135 | 293 | 29 | 62 | 384 |
| ~162 | 325 | 13 | 46 | 384 |

`n_alive` collapses to **105** at 81 h and climbs back to **325** at 162 h.

**Every row sums to exactly 384.** That is the mechanism, not a coincidence: each
row re-classifies the entire inventory independently over `[start, end]` rather
than carrying deaths forward. So `n_died` in this table means *"would be classified
dead if the recording stopped here"*, **not** *"dead by this time"*. It is not a
survival curve and does not behave like one.

**The intermediate rows cannot be used to choose a window.** They answer a question
nobody asked. The first and last rows remain meaningful.

**The note was fixed; the computation was not.** `TradeoffResult.note` asserted
"n_alive falls as the window extends and more flies have died by the cutoff", and
the tool and engine docstrings said the same. That statement is false and was known
to be false, so leaving it in place would have been the worst of the options — a
tool that lies about its own output in the field a caller is most likely to read.
The wording now describes what the table reports. **`engine.window_tradeoff` is
unchanged.**

> **⚠ Related, found while correcting the note, NOT changed — needs a decision.**
> Two tests assert the property the real data falsifies:
>
> * `tests/test_window.py::test_window_tradeoff_curve_is_non_increasing` —
>   `assert alive == sorted(alive, reverse=True)`
> * `tests/test_contract.py` — `assert rows[0]["n_alive"] >= rows[-1]["n_alive"]`
>
> They pass, because the synthetic corpus happens to produce a monotone curve. The
> repository is therefore now internally contradictory: the docstrings say the
> curve is not monotonic, and the suite asserts that it is. That contradiction is
> deliberate and visible rather than resolved unilaterally — changing an assertion
> to match a defect is exactly the move this project forbids, and changing it to
> match the *fix* requires the fix. **Whoever fixes the computation resolves both
> tests in that commit.** Until then the tests are a standing reminder that the
> synthetic corpus does not exercise this path.

### FINDING 2 — the death rule cannot distinguish sleep from death ⚠

`death_hours` defaults to **12.0**. The light-dark cycle is **12:12**. The
trailing-zero threshold and the dark phase are therefore the same length, and **a
fly quiescent through a single night meets the death rule exactly.**

This is the mechanism behind Finding 1. The low-`n_alive` rows all end deep in
dark; the high-`n_alive` rows end in daylight. The window tool is reporting the
classifier faithfully — the defect is in the classifier, not in the tool that
displays it.

Recorded here as a defect in **the classifier**. It is not a tuning question: any
`death_hours` at or below the dark-phase length collapses "asleep" into "dead", and
a longer one loses real late deaths (the known limitation `CLAUDE.md` already
states). The rule needs a discriminator that is not duration alone — phase
alignment is the obvious candidate, and the existing `decline_ratio` work already
established that phase-matching is the move that makes a self-referenced check
work.

`DEFAULT_DEATH_HOURS = 12.0` is described in `defaults.py` as a lab convention.
On a 12:12 cycle it is also, exactly, the worst available value.

### FINDING 3 — `compute_*` runs silently with QC decisions outstanding ⚠

63 `decisions_required` were raised. None were applied. `compute_sleep` then
computed over all **384** channels without a word.

Two consequences, both quiet:

* **Dead flies score as perfect sleepers** across their trailing zeros. Nothing in
  the returned summary indicates that any of the animals in the mean were dead for
  part of the interval.
* **The flags are lopsided by group** — 26 / 10 / 18 / 9. A group difference in
  sleep can therefore be a difference in *how many animals were dying*, and the
  tool gives the reader nothing to notice that with.

The rails that exist require QC to have *run* (`_guard_ready` checks `session.qc`)
and groups to be assigned. Neither checks whether the decisions QC raised were ever
resolved. "Flag, don't fix" is honoured at the QC step and then dropped one step
later.

**Proposed, not built** (per instruction). A warning on every `compute_*` return
when `decisions_required` is non-empty and unresolved, carrying the count and the
per-group breakdown — because the breakdown is the part that turns "63 outstanding"
into "your groups are not comparable". A refusal is the wrong shape: computing with
outstanding decisions is legitimate exploratory work, and the exclusions are a
human decision by design. This is the same warning-not-refusal call HANDOFF-9 made
for unassigned groups, and it inherits the same caveat: **on a path with no
backstop, a warning is the only signal there will ever be.**

### FINDING 4 — latency is returned without its denominator ⚠

Sleep latency was defined for **52 / 71 / 21 / 18** flies of 96 per group. The
tool reported the group means without stating that the denominators differ, and
without stating the rule that decides when a fly has no latency to report.

The largest effect in the dataset — **tau_RU 8.1 min vs control_EtOH 53.3 min** —
sits on the **smallest** n. A 6.6× difference computed over 18–21 animals in one
arm and 52–71 in another is not a result yet, and nothing in the return says so.

**The agent caught this; the tool did not.** That distinction matters more than the
finding. A metric whose denominator varies silently by group is precisely the
"operation that does something wrong and reports success" shape this repo keeps
finding — and here it survived to the point where only a careful reader stood
between it and a reported effect.

---

## Record integrity

### FINDING 5 — an empty tube was excluded as mortality ⚠

**M9 ch16** was excluded under a death reason. Its last movement is **six minutes
after the run started**.

Six minutes of activity at the very beginning is an empty tube, or a fly that never
established — not a death. **Excluding it is correct. Classifying it as mortality
is not.** The distinction is one `CLAUDE.md` already draws in the channel-state
table: *"an empty tube was never an n; a dead fly must be censored at death"*.

The cost is downstream and specific: **any survival analysis built on these
exclusions counts this channel as a death event**, at t ≈ 0. That is one
event of thirteen, at the extreme end of the time axis, in an n of 96 per group.

### FINDING 6 — the exclusion reason says "through full run" and means something else ⚠

The recorded reason string reads *"through full run"*. That is literally true of
**one** channel. The other twelve have trailing-zero runs to the **end** of the
run, which is a different claim — a fly that died on day 4 is not silent "through
the full run".

Recorded so the wording is corrected **before any report is rendered**, because
`render_report` copies exclusion reasons into the manifest verbatim, and at that
point the wrong claim is in the artifact a human reads and cites.

---

## Open questions — recorded as questions

These are **not** findings. They are things the run surfaced that the run cannot
settle, written down so the next session does not rediscover them or, worse, guess.

### QUESTION 7 — channel indices repeat across monitors, to the minute

| Pair | Observation |
|---|---|
| Monitor2 ch10 / Monitor3 ch10 | last movement 12-28 **09:15** and **09:14** |
| Monitor11 ch32 / Monitor12 ch32 | identical at **17:07** |
| suspect flags | align on the same channel indices within each block |

The files are **confirmed raw downloads** — not merges, not copies. Bin counts
differ by exactly **1, 1 and 0** across the three pairs.

Two readings are live and the data so far does not separate them:

* a **one-minute clock offset between monitors**, which the ±1 bin counts fit
  neatly, and which would make the "coincidence" an artefact of alignment;
* something shared across monitors at the same channel index — a hardware,
  wiring, or position effect — which would make channel index a confounder that
  no current QC step looks at.

**Unresolved.** Worth stating that the second reading, if true, is the more
serious: it would mean channel index is a lurking variable in a design that
assigns groups *by* channel range.

> **ANSWERED — see `HANDOFF-12-monitor-topology.md`.** The second reading was
> right in substance and understated in degree. It is not a hardware, wiring or
> position effect at a shared channel index: Monitor1, Monitor2 and Monitor3 are
> three IR beams at three heights on the **same 32 tubes and the same 32 flies**.
> Channel index is not a confounder shared across monitors — it is the *same
> animal*, seen three times. One fly breaking three beams explains the aligned
> last-movement times, the ±1 bin differences, and the suspect flags landing on
> matching indices. It is not duplication, leakage, or a hardware fault, and it
> does not need a second experiment. This question is closed.

### QUESTION 8 — total sleep units are unverified

Reported total sleep is **4–5 h**. That is far too little across 6.7 days, and
still low for *Drosophila* as a daily mean — the species sleeps on the order of
half the day. So the reported figure is either mis-scaled or mis-labelled, and it
is not currently possible to tell which from the return alone.

Related arithmetic, checked:

```
light-phase bout duration 107.6 min × 82.9 bouts  ≈ 8,919 min of sleep per fly
light phase available: 6.74 days × 12 h × 60      ≈ 4,854 min
```

**8,919 minutes of sleep in 4,854 minutes of light phase is impossible.** So at
least one of the following holds:

* count and duration are not computed over the same interval (e.g. bouts counted
  across the whole run, duration averaged within the light phase);
* the mean is inflated by long trailing bouts from dying flies — **the SD exceeding
  the mean points at this one**, and it connects directly to Finding 3.

Not answered here. The two candidates require different fixes and picking between
them by argument rather than by measurement is how the wrong one gets fixed.

---

## What was NOT verified

* **Everything here is one run, one experiment, one lab's cycle.** The 12:12
  coincidence in Finding 2 is exact for this design; a 14:10 cycle would show a
  different curve and might have hidden it entirely.
* **No fix was validated**, because no fix was made beyond the note. The three
  corrected docstrings are wording; `engine.window_tradeoff` returns exactly what
  it returned before.
* **Findings 5 and 6 are read off the exclusion record**, not re-derived from the
  raw files in this session.
* **Question 8's arithmetic is verified; its diagnosis is not.** The impossibility
  is arithmetic. Which of the two causes produced it is untested.

---

## For HANDOFF-7's open list

All eight are added there with one-line summaries pointing here. Priority, stated
as an opinion and not as a finding:

1. **Finding 2** first. It is upstream of Finding 1 and of part of Question 8, and
   it silently converts sleep into death on any 12:12 design — which is most of
   them.
2. **Finding 4** next. It is small, it is contained in one return shape, and it is
   the one currently sitting under the largest reported effect.
3. **Finding 3** with it, since both are "state the denominator / state the
   precondition" and both land in the same summaries.
4. **Findings 5 and 6** before any report is rendered or any survival analysis is
   run on these exclusions.
5. **Question 8** before any sleep number leaves this system.
6. **Question 7** needs a second experiment, not a code change.

## Exact next steps

* Do **not** use `window_tradeoff`'s intermediate rows to pick a window until the
  computation is decided. The note now says so; the tests still say the opposite.
* Whoever fixes `window_tradeoff` resolves both monotonicity assertions in the same
  commit — and adds a corpus case with a real light-dark cycle, because the
  synthetic generator currently produces a monotone curve and would not have caught
  this.
* Re-run the pipeline with the 13 exclusions applied *and* the 63 decisions
  resolved, and compare. That comparison is the direct test of Finding 3's severity
  and nobody has run it.
