# dam-agentic

Agentic QC and analysis over TriKinetics Drosophila Activity Monitor (DAM) data.

An LLM agent orchestrates tested analysis functions. It does not analyze anything
itself — every number in every report traces to a deterministic function under test.

## Why the boundary matters

The model never receives raw activity counts. Tools return summaries and handles;
data stays server-side. This is architectural, not advisory: the model cannot invent
a statistic because it never holds the numbers.

Contrasts are pre-declared in `config/contrasts.yaml`. The agent chooses which
declared comparison to run; it cannot invent one. An agent free to search metrics ×
groups × phases for significance will find some, every time.

QC flags, it does not fix. Every exclusion changes n and is surfaced for a human
decision with its evidence attached.

## Layout

```
skills/dam-qc/       Agent Skill — QC SOP, validation script, format reference
dam_mcp/             MCP server — tools, resources, schemas
agent/               LangGraph agent (Phase 2) — orchestrates the tools
damsim/              Eval corpus generator + scorer
config/              Pre-declared contrast set
docs/mcp-spec.md     Tool surface design
docs/running.md      How to run the server, the agent, and the tests
```

## Evaluation

`damsim` synthesizes DAM corpora with exact planted ground truth. Change the seed for
a fresh held-out set; no labelling required.

Perfect scores mean the eval is measuring the generator, not the world. Value comes
from `--adversarial`: defects the detector was not written to catch.

```bash
python damsim/generate.py --out /tmp/corpus --n-experiments 12 --seed 42 --adversarial
python damsim/score.py --corpus /tmp/corpus --qc-cmd "python skills/dam-qc/scripts/validate_dam.py"
```

Scores are reported per defect class. A single aggregate hides which failure you have,
and which failure you have is the only useful thing.

### Known limitations

- **Late deaths.** A trailing-zero death window (this lab's default is 12 h) cannot
  detect a fly dying within that final window. Structural, stated, reported — not a bug.
- **Single-beam blindness.** Zero counts mean no midline crossing. A fly active at one
  end of the tube scores zero. Hardware bound; sets the floor on what any QC can see.

## Provenance

Rtivity is published software by Silva et al., *Sci Rep* 12 (2022),
doi:10.1038/s41598-022-08195-z, from the Oliveira lab, built on the Rethomics
framework (Geissmann et al., *PLoS ONE* 2019). This repository is an agentic layer over
a maintained fork; it does not claim authorship of Rtivity or its methods.

## Status

The MCP server is implemented and passed its Phase 1 gate: an MCP client we did
not write (MCP Inspector, over stdio) discovered all twelve tools and drove a full
QC-and-analysis of a real experiment, with the boundary and the three rails —
metrics gated on groups, exclusions gated on confirmation, contrasts gated on
config — holding over the protocol. See `docs/running.md` to run it, and
`docs/mcp-spec.md` for the tool surface.

The LangGraph agent (`agent/`) is written but not yet run end-to-end — that is the
next milestone (`pip install -e ".[agent]"` plus an API key). The gradual-decline
detector bug remains open; see `CLAUDE.md`.
