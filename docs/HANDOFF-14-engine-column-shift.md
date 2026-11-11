# HANDOFF-14 — the engine read every channel from the wrong column

**Written:** 2026-11-01 · **Base:** `main` at `82ab1c9`

Read `CLAUDE.md` first, then `HANDOFF-10` for the format. **Findings are stated as
found.** One thing here is fixed: the engine itself (Rtivity-Python, merged as
`cc44de0`), and this repo's pin to it. Nothing computed before the fix has been
re-run, and nothing below says the old numbers are right or wrong by any amount —
only that they were produced by a reader that mislabelled every channel.

---

## Status in one paragraph

Rtivity-Python **v0.12.0** read channel *k* of a DAM2 file from tube *k − 1*, and
channel 1 from the **light sensor**. Tube 32 was never read. Every number that came
out of `compute_sleep`, `compute_activity`, `compute_rhythmicity`,
`compute_survival` and `run_contrast` went through that reader. QC did not: it uses
this repo's own parser, which reads the right columns. So since the engine was
first pinned, **QC and the metrics have disagreed about which fly is which**, by
one tube, on every channel — and no test noticed, because no test compared the two.
An invariance test found it (the same flies under renumbered channels gave
different group metrics), a differential test now pins it, and the shift is
confirmed on real rig exports. The fix is merged upstream; this repo's pin moves
to it here. The `v0.12.1` **tag** is not yet on GitHub (see below), so the pin is a
commit sha.

---

## FINDING 1 — the engine's channel k was tube k − 1, and channel 1 was the light sensor ⚠

`modules/data/dam_parser.py:175` at `v0.12.0` (`48818b8`):

```python
df[col] = pd.to_numeric(raw.iloc[:, 9 + i], errors="coerce")   # i = 0..31
```

In a 42-field tab-delimited DAM2 row, 0-indexed field 9 is the light status
(`CLAUDE.md`'s column 10); channels are fields 10–41 (columns 11–42). `9 + i`
starts one field early. The docstrings described two incompatible layouts — the
module docstring a whitespace split (date as three fields, 44 in all), `_read_raw`
a tab split with a non-existent field 41 — and the offset sits in the gap between
them.

Fixed upstream as `10 + i`, with both docstrings rewritten to the tab layout and a
regression test whose fixture puts distinct values in the light field, the field
before it, and every channel: Rtivity-Python PR #1, merged `cc44de0`.

### Evidence 1 — the invariance test (synthetic, how it was found)

`tests/test_invariance.py::test_relabelling_tubes_leaves_every_group_metric_unchanged`
assigns tubes 1–4 and 17–20 of a damsim monitor, then the same flies with channel
*k* moved to channel 33 − *k*. On `v0.12.0` the group means differed — the first
mismatch reported was mean total sleep time **2.111 h vs 0.054 h**, because
"channel 1" in one run was the light sensor and in the other a real fly.

### Evidence 2 — the differential test (synthetic, now in the suite)

`tests/test_differential.py` reads one damsim monitor through QC's
`validate_dam.parse_monitor` and through `engine.build_store` (all 32 channels
assigned, exactly the path every `compute_*` takes) and compares per-channel
totals. On `v0.12.0`, **32 / 32 channels disagree**; the first entries of the
failure, as `channel: (QC total, engine total)`:

```
1: (12035, 2158)   2: (12138, 12035)   3: (12360, 12138)   4: (12002, 12360)
5: (0, 12002)      6: (12218, 0)       ...   16: (57, 12232)   17: (11724, 57)
```

Engine channel *k* carries QC channel *k − 1* throughout, and engine channel 1
carries 2158 — the number of lit reads. Note channel 5: QC sees an **empty tube**
(0), the engine scores channel 6 as the empty one and channel 5 as a fly with
12,002 crossings. That is the shape of the harm: an exclusion QC recommends for
channel 5 is applied, by channel number, to a different animal than the one the
metrics would have dropped.

### Evidence 3 — real data (confirms the shift)

`ChiaLungChuang/fly-movement` @ `4ec08a6`, `data/Monitor7.txt`, `Monitor8.txt`,
`Monitor9.txt` — genuine rig exports, 7,017 reads each, all 42 tab fields in every
row. Column totals by `awk -F'\t'`; engine totals by `read_dam_file`, engine at
`v0.12.0` (`48818b8`):

| File | col 10 (light) | col 11 (raw ch1) | col 12 (raw ch2) | col 41 (raw ch31) | col 42 (raw ch32) | engine ch01 | engine ch02 | engine ch32 |
|---|---|---|---|---|---|---|---|---|
| Monitor7 | 0 | 283 | 1604 | 178 | 3287 | **0** | **283** | **178** |
| Monitor8 | 0 | 148 | 1302 | 115 | 2816 | **0** | **148** | **115** |
| Monitor9 | 3422 | 195 | 2225 | 325 | 6544 | **3422** | **195** | **325** |

Monitor9's light field varies (1 on 3,422 reads, 0 on 3,595); its engine
"channel 1" is exactly the count of lit reads. On Monitor7 and Monitor8 the light
field is 0 throughout, so engine "channel 1" is an all-zero tube — which every sleep
metric scores as a fly asleep for the entire run. With the fix the engine reports
ch01 = 283 / 148 / 195 and ch32 = 3287 / 2816 / 6544, matching columns 11 and 42.

**Verdict: confirmed on real data.** A second, same-experiment confirmation — the
real 20251229 files that HANDOFF-11's run used — needs the source machine; those
files are not in any repo this session can reach.

---

## The boundary — what is affected and what is not

| Path | Reader | Affected? |
|---|---|---|
| `run_qc`, `window_tradeoff`, `set_analysis_window` | `validate_dam.parse_monitor` (`skills/dam-qc/scripts/validate_dam.py:22-24, 39-63`) | **No** — reads columns 11–42 |
| `load_experiment`, `describe_experiment` (structure, bin width, window) | `engine._scan_file` / `_read_rows` (tab split) | **No** — timestamps and row structure only |
| `check_board` / `board_checks`, topology `sum_machine` | `engine._read_rows` (`dam_mcp/engine.py:188, 353`) | **No** — verified equal to QC's parser on all 32 channels before PR #25 merged |
| `compute_sleep`, `compute_activity`, `compute_rhythmicity`, `compute_survival` | `engine.build_store` → Rtivity `DataStore` → `dam_parser.read_dam_file` | **Yes, before `cc44de0`** |
| `run_contrast` | same | **Yes, before `cc44de0`** |
| `render_report`, the `metrics` / `contrasts` resources | copies of the above | **Yes** — they repeat what `compute_*` / `run_contrast` stored |
| Exclusions (`apply_exclusions`) | keyed by channel number, decided from QC | The record is right; **applied to the metrics, excluding channel *k* removed tube *k − 1*'s data**, not the tube QC flagged |

`engine._read_rows` names its slice start `_LIGHT_COL = 10` and uses it as a
0-indexed start; that is field 10, the first channel — correct, and misleadingly
named. Not changed here.

---

## FINDING 2 — the test suite could not have caught it, in either repo ⚠

* **Rtivity-Python:** every test that loads data reads `~/Desktop/Rtivity_main` and
  skips without it — which is CI and every machine but one. A fresh `v0.12.0`
  checkout gives **23 passed / 256 skipped / 279 collected**, and none of the 23 is a
  parser test. Where the parser tests do run, they assert structure (columns
  exist, counts are non-negative integers, timestamps monotonic); a one-column
  shift satisfies all of them, because the light field is a non-negative integer
  too.
* **dam-agentic:** `tests/test_compute.py` checks that only summaries cross the
  boundary — it says so — and leaves correctness to "the 229 engine tests". Those
  were the tests that never ran. No test here compared the engine's reading of a
  file with anything.

The new tests close that gap from both ends: the upstream regression test
(`tests/test_dam_parser_columns.py`, runs in CI with its own fixture) and the
differential test here, which fails if the two readers ever drift apart again for
any reason — not just this one.

---

## Other DAM readers — audit (read-only)

Bin-width column: how time per bin is derived. Some rigs record at 30 s; anything
counting rows as minutes is wrong by 2× there.

| Reader (repo @ sha) | Channels mapped at | Split | Raw col read as ch1 | Raw col read as light | Time per bin | Disagrees with `CLAUDE.md`? |
|---|---|---|---|---|---|---|
| Rtivity-Python `dam_parser._read_raw` @ `48818b8` (v0.12.0) | `modules/data/dam_parser.py:175` | tab | **10 (light)** | not read (phase from ZT0) | timestamps — but bouts are measured last-bin − first-bin, **one bin short** (Rtivity-Python draft PR #2) | **Yes** — fixed at `cc44de0` |
| dam-agentic QC `parse_monitor` @ `82ab1c9` | `skills/dam-qc/scripts/validate_dam.py:22-24, 39-63` | tab | 11 | 10 | modal timestamp gap (`:66-72`); death window hours×3600 ÷ bin (`:287-288`) | no |
| dam-agentic `engine._read_rows` / `sum_machine` @ `82ab1c9` | `dam_mcp/engine.py:188, 353` | tab | 11 | not read | modal gap (`_bin_seconds`, `:259`) | no (but see Finding 3) |
| fly-movement `read_one_monitor` @ `4ec08a6` | `dam-shiny/R/01_read_qc.R:79, 91, 100` (duplicate in `R/01_read_qc.R`) | `fread` auto (tab → Case A) | Case A: 11. **Case B: offset 6** — on a whitespace split that is the status field; channels would start at 13 | not read | `floor_date(…, "minute")` (`:114, :262`) merges 30 s bins; `detect_dead_channels` (`:315-352`) counts zero **rows** as minutes; `02_position_zones.R:158, 244-245` bout length in rows as minutes | **Case B layout; bin width (rows as minutes)** |
| Rtivity-DAM-sleep-analysis standalone @ `a7b75ac` | `Sleep_bout_analysis_standalone.R:111, 136-139` | whitespace (44) | 13 of 44 = col 11 | not read | death: zero-run rows ≥ hours×60 (`:54`); bouts in rows as minutes (~`:186`) | **bin width (rows as minutes)** |
| Rtivity-DAM-sleep-analysis `Bin_converter_server.R` @ `a7b75ac` | `:56` tab, `:197` `[,11:42]` | tab | 11 | not read | `difftime` of timestamps (`:117`) | no |
| Rtivity-DAM-sleep-analysis Shiny analysis @ `a7b75ac` | `Data_selection_server.R:1269-1270` → `damr::load_dam` | delegated | **`damr` — not verified** | `damr` | `min_time_immobile = 60 * sleepTime` s | unverified |
| dam-monitor-combiner `app.R` @ `5253381` | `:82` tab, `:127` V1–V10 from file 1, `:130` V11–V42 summed | tab | 11 | 10 (copied, not summed) | bin-agnostic; requires identical timestamps row for row | no |

None of these were changed. They are separate repositories; the two "rows as
minutes" readers are correct at 60 s and wrong by 2× at 30 s.

## FINDING 3 — "counts per waking minute" is counts per waking bin ⚠

`dam_mcp/engine.py:829-838`, `_cpwm_by_phase`: total counts ÷ number of bins with
movement. At 60 s bins that is per waking minute, as labelled; at 30 s it is per
waking half-minute, and the reported figure is half the true per-minute rate.
Recorded, not fixed — the same family as the row-count readers above, and the
30 s / 60 s metamorphic relation proposed in the step that follows this handoff is
the test that would pin it.

---

## What was NOT verified

* **No result computed before the fix has been re-run.** HANDOFF-11's numbers and
  the README's came through the shifted reader; they are annotated as such on a
  separate docs branch. Nothing here says by how much they move.
* **The same-experiment real-data check** (the 20251229 files) needs the source
  machine.
* **`requires_rtivity` tests ran here against the fixed engine** (installed from
  `cc44de0`). Everything behind the 256 skipped Rtivity-Python tests — which need
  `~/Desktop/Rtivity_main` — is unverified in any environment but one.
* **`damr`'s reader** (used by Rtivity-DAM-sleep-analysis's Shiny analysis) is an
  external package and was not read.
* ~~**The v0.12.1 tag is not on GitHub.**~~ It is now (pushed by the owner,
  resolves to `cc44de0`); the pin moved to it in #31 and on to the 0.13.0 commit
  in the definition-change PR.

---

## Definition changes (2026-11-03) — not free fixes

The owner decided two definitions. Both change what the system reports **on
correct input**, so results before and after are not comparable. They are
recorded here as definition changes, not as bug fixes that make old numbers
"more right" for free.

### Change 1 — a bout of n bins lasts n × bin width (Rtivity-Python 0.13.0)

Rtivity-Python measured every run (sleep, wake, activity) as `t_last − t_first`,
one bin short. That made the 5-minute sleep rule 6 min at 60 s and 5.5 min at
30 s. In 0.13.0 a run ends one bin after its last read, with the bin width taken
from the timestamps (Rtivity-Python PR #2, head `5f3948d`).

**60 s data is affected, not only 30 s.** Measured on a synthetic 60 s damsim
monitor, 0.12.1 → 0.13.0:

| Metric | 0.12.1 | 0.13.0 | Change |
|---|---|---|---|
| asleep rows | 13,172 | 13,827 | +5.0 % |
| sleep bouts | 148 | 279 | +88.5 % |
| mean total sleep, group A | 0.4698 h | 0.5495 h | +17.0 % |
| mean total sleep, group B | 0.4435 h | 0.5383 h | +21.4 % |
| activity bouts | 306 | 175 | −42.8 % |

The asleep rows reconcile exactly: 655 more rows = 131 new five-bin bouts × 5
rows. On real data the size depends on how many quiet runs sit exactly at the
threshold. This is synthetic data, so the figure says the effect is large enough
to matter; it does not say how large it is on any real experiment.

### Change 2 — counts per waking minute is per actual minute (this repo)

`_cpwm_by_phase` divided total counts by the number of moving **bins**: per
minute at 60 s, and half the true rate at 30 s. It now divides by waking
**minutes**, i.e. moving bins × that animal's bin width (median timestamp gap)
÷ 60. The 60 s figures are unchanged by this. Both `compute_activity` and the
`counts_per_waking_minute` contrast metric go through it.

### What pins them

- `tests/test_cpwm.py` (new, needs no engine). 6 tests; 3 fail on the old
  definition (the 30 s and mixed-width cases).
- `tests/test_metamorphic.py` (from #28). The 30 s / 60 s relations now hold
  **exactly**, under a corrected transformation:
  - The first version split the original counts, so a one-count minute became a
    quiet half and a moving half. That split itself halves waking time, which is
    why counts per waking minute still disagreed after the new definition
    (2.165 at 60 s vs 2.822 at 30 s; 4.758 vs 2.564 before it).
  - The pair is now a doubled 60 s file and its split, so counts, waking time
    and quiet time are identical at both widths.
  - Total activity, total sleep and counts per waking minute must all be equal
    to rounding. That is tighter than the 90 s-per-bout and 10 % bounds they
    replace.

### Also changed (2026-11-11), same family

- **The survival death window** (`survival._first_lethal_silence`, Rtivity-Python
  0.13.0) now measures a silent run as n bins × bin width too, so the window
  equals `dead_window_h`: 720 silent 60 s bins meet 12 h, where 721 were needed.
- **`_bin_minutes`** (this repo; scales asleep bins for the `total_sleep`
  contrast metric) raises a ToolError when no positive gap between reads can be
  found, instead of assuming 1.0 min. `tests/test_bin_minutes.py`.

---

## Verification

| Run | Engine | Result |
|---|---|---|
| `tests/test_differential.py` + `tests/test_invariance.py` | `v0.12.0` (`48818b8`) | **2 failed / 6 passed / 8 collected** — the differential test and the relabel test |
| same | `cc44de0` (reports 0.12.1) | **8 passed / 0 skipped / 8 collected** |
| full suite | `cc44de0` | in the PR body |

---

## Exact next steps

1. Push the `v0.12.1` tag from a machine that can (`git tag -a v0.12.1 cc44de0`),
   confirm it resolves to `cc44de0`, then move the pin from the sha to the tag.
2. Update the editable engine at `~/Rtivity-Python` (it shadows the pin): pull
   `main`, reinstall, confirm `git describe --tags` and the reported version are
   0.12.1.
3. Re-run HANDOFF-11's session on the fixed engine before any sleep, activity or
   contrast number from it is used — and run the differential test against the
   real 20251229 files as the same-experiment confirmation.
4. ~~Decide Finding 3 (`_cpwm_by_phase`) and Rtivity-Python draft PR #2 (bout
   fencepost).~~ Decided 2026-11-03 — see "Definition changes" above. What
   remains: merge Rtivity-Python PR #2, tag v0.13.0, move the pin to the tag.
