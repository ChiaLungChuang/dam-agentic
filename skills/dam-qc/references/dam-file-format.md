# TriKinetics DAM file format

Reference for parsing `MonitorNN.txt` files from DAMSystem3.

> **Verify this against your own monitor output before relying on it.** DAMSystem
> versions differ in the number of unused columns, and this table is the common DAM2
> layout, not a guarantee. Parse one of your real files, check the column count, and
> correct this file once. After that it is settled.

## Layout

Tab-delimited, no header row.

| Col | Field | Notes |
|---|---|---|
| 1 | Reading index | Sequential; resets are a red flag |
| 2 | Date | `DD MMM YY` |
| 3 | Time | `HH:MM:SS` |
| 4 | Status | `1` = valid read; anything else is suspect |
| 5–9 | Monitor metadata | Extras, monitor number, tube number (unused in DAM2), data type, unused |
| 10 | Light status | `0` = lights off, `1` = lights on |
| 11–42 | Channels 1–32 | Beam-break counts per bin |

Confirm columns 5–10 against a real file — the light status column position is the one
most worth checking, since misreading it silently inverts every ZT assignment.

## Bin width

Set on the monitor, typically 1 minute. Derive it from consecutive timestamps rather
than assuming it; a 5-minute run parsed as 1-minute data produces sleep metrics that
are wrong by a factor of five and look entirely plausible.

## Channel semantics

A count is the number of times the fly crossed the infrared beam at the tube midpoint
in that bin. Zero means no midline crossing — which is not the same as no movement. A
fly can be awake and active at one end of the tube and score zero. This is a known
limitation of single-beam DAM and the reason multi-beam and video systems exist; it
matters here because it sets a floor on what QC can detect.

## Common file-level defects

- **Truncated files** — monitor stopped mid-write
- **Concatenated runs** — two experiments in one file; look for index resets
- **Clock drift** — monitors drift apart over multi-week runs
- **Missing minutes** — dropped reads leave gaps in the timestamp sequence, not NA rows
