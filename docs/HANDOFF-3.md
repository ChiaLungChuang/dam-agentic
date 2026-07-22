# HANDOFF-3 — death-threshold change, dependency pin, verification

**Date:** 2026-07-22
**Reader:** the next agent session working in `dam-agentic`.
**Prior context:** HANDOFF-2 (window-before-exclusions rationale, backlog through #12).

This session was a human verification pass over #12, not new feature work. It
found and fixed two reproducibility gaps and changed one biological default.
Read "Constraints" before planning anything — two of them will silently block you.

---

## Current state (all verified this session)

- `dam-agentic`: **69 tests pass, ruff clean.**
- `Rtivity-Python`: **279 tests pass**, on `main`, pushed, tagged **`v0.12.0`**
  (tag dereferences to `48818b8`).
- Death-window default is now **12 hours**, previously 24, in both repos.
- `dam-agentic` declares the engine dependency pinned:
  `rtivity-python @ git+https://github.com/ChiaLungChuang/Rtivity-Python@v0.12.0`
  under `[project.optional-dependencies]` → `engine`.

### The 12h change

Two places, deliberately kept in sync:

| Repo | Location | What |
|---|---|---|
| Rtivity-Python | `modules/analysis/survival.py` L69, L119 | library default `dead_window_h: float = 12.0` |
| dam-agentic | `dam_mcp/defaults.py` | `DEFAULT_DEATH_HOURS = 12.0`, imported by `server.py` and `engine.py` |

The override parameter is unchanged everywhere — callers can still pass an
explicit `death_hours` / `dead_window_h`. `flag_ambiguous_deaths` is untouched.

**Do not hardcode a death-hours number.** Import `DEFAULT_DEATH_HOURS` from
`dam_mcp.defaults`. Seven previously-hardcoded sites were consolidated into it
this session; re-introducing a literal re-opens the drift this fixed.

Note: `ld_period_h = 24.0` in `server.py` and `engine.py` is the **light/dark
cycle length**, unrelated to death. Leave it alone.

---

## Constraints — read before planning

1. **`~/Rtivity-Python` is a separate repo outside this working directory.**
   The survival/death logic (`modules.analysis.survival`) lives there, not here.
   You cannot edit it from a `dam-agentic` session. Any change to the death rule
   requires: separate session in that repo → test → commit → push → new tag →
   update the pin here → re-test here. Budget for that; don't propose a
   "quick fix" to survival logic.

2. **The pin is the declaration of record, not what is currently running.**
   The local install is still editable (`pip show rtivity-python` → points at
   `~/Rtivity-Python`). A clean install would fetch `v0.12.0` from GitHub; this
   machine runs the local working tree. Treat behavioural differences between
   the two as possible, and never assume the pin has been exercised.

3. **Real DAM data lives at `~/Desktop/Rtivity_main`** — outside both repos.
   Rtivity-Python's real-data tests skip when `metadata.xlsx` is absent. The
   synthetic death-detection tests always run.

4. **`~/Rtivity-Python/Rtivity-Python/` is a stale nested clone** with its own
   `.git` and a **different death algorithm** ("last N hours silent at end of
   record", vs. the live "first silence run reaching N hours"). It is untracked
   by the outer repo and is not what Python imports. Never import from it,
   never cite it as the death rule.

5. **Layer 2 agent eval needs `ANTHROPIC_API_KEY`.** Not set. Anything under
   `evals/run_agent_eval.py` is blocked until the human sets it.

---

## Verified this session (don't re-litigate)

- **Window clamp is correct.** `engine.py` `build_conditions` clamps per file:
  `start = max(file_start, window_start)`, `stop = min(file_stop, window_end)`,
  falling through to the file's own range when no window is set. Both ends,
  applied before rows are built.
- **Ordering rail holds.** Exclusions are applied *after* the window clamp, so
  the HANDOFF-2 invariant (window first, then exclusions) is enforced in the
  compute path, not just at the tool boundary.
- **`flag_ambiguous_deaths` semantics.** It flags a fly that has an inferred
  death *and* recorded activity after it. `build_survival_data` deliberately
  keeps the earlier death time and treats the later movement as artifact; the
  flag exists so a human can sanity-check those cases (monitor glitch,
  dislodged fly, threshold too short). This is a reviewable default, not a bug.

**Consequence of 12h worth knowing:** a shorter window makes death calls more
aggressive — a long quiescent bout is likelier to read as death. Expect the
`flag_ambiguous_deaths` list to grow on real data relative to 24h. That list is
a diagnostic to run on every real dataset, not a one-off check.

---

## Open items

### Blocked on the human — do not attempt to author these

- **`config/contrasts.yaml` is still the `EXAMPLE_replace_me` stub.** Real
  genotypes and pre-registered comparisons require the experimental design.
  The whole point of the pre-registration rail is that the model cannot invent
  contrasts; writing the file for the human defeats it. Ask, don't draft.
- **Monitor/treatment confound analysis.** Needs the human's account of how
  treatments were assigned across monitors.

### Available

- **Doc sync.** Check `CLAUDE.md` and `docs/mcp-spec.md` for any surviving
  reference to a 24-hour death default and update to 12h. Outdated docs are
  worse than none.
- **Exercise the pin.** Install into a scratch venv from the pinned spec to
  prove `@v0.12.0` actually resolves and imports. Currently unproven.
- **Suite runtime.** `dam-agentic` takes ~168s, dominated by contract tests
  spinning up the real server per test. Rtivity-Python solved its equivalent
  problem with a session-scoped fixture (`tests/conftest.py`); the same pattern
  may apply here. Nuisance, not urgent.

### Human's call, not yours

- Relocating the stale nested clone in `~/Rtivity-Python/Rtivity-Python/`.
  Flagged, not actioned. Don't delete anything.

---

## Working agreements

- Run `pytest -q` **and** `ruff check .` before declaring anything done, and
  report the actual counts.
- Keep commits scoped to one concern. This session's history is a usable model:
  test refactor, then the 12h default, then the pin, then the constant.
- When a change spans both repos, say so explicitly up front — the cross-repo
  cost in Constraint 1 is the single easiest thing to under-scope here.
