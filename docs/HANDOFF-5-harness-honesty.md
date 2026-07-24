# HANDOFF-5 — Harness honesty

**Status of the repo entering this handoff:** HANDOFF-4 is committed to `main` in four
scoped chunks. CI is green on a real remote (lint, test 3.11/3.12/3.13, eval). 75 tests
pass; `ruff==0.16.0` clean under an explicit rule set.

**Why this handoff exists.** The first real-model eval run
(`--synthetic --runs 5 --provider google`, `gemini-2.5-flash`) produced a report in which
every metric was `1.000` with `0.0` variance across all 4 tasks and all 7 properties —
while `total tokens` was `0.0` and latency was bimodal (min ~0.3s, max ~54s). The cause
was Google's free tier: **20 requests per day per model**, exhausted within the first
task. Every subsequent run 429'd instantly, was caught by the blanket
`except Exception` in `run_task`, became an `_ErrMsg` trace with no tool calls, and
**passed every property vacuously**.

The scorers cannot fail on an empty trace. `ScriptedModel` always emits a well-formed
trace, so the keyless negative controls never probed this. That gap between the fake
distribution and the real one is the finding, and closing it is this handoff.

---

## Decisions already made — do not re-litigate

1. **Infrastructure failures are not measurements.** A 429, an auth error, or a dropped
   connection measures the billing tier, not the agent. These must abort the eval. Only
   *agent-behaviour* failures are datapoints.
2. **Fail loud by default.** An exception that is not on the explicit agent-failure
   allowlist aborts. Do not widen the allowlist to make a run complete.
3. **Vacuous truth is not a pass.** An ordering property of the form "if X happened, Y
   came first" is *not applicable* when X never happened. It must not report `True`.
4. **Zero completed runs means no score.** The report says "no data". It never prints a
   number derived from zero completed runs.
5. **Scorers are never loosened to make a run pass.** If a change causes an existing
   negative control to stop firing, the change is wrong.

---

## Task 1 — Write the failing control first

In `tests/test_fake_agent.py`, add a negative control for the empty trace: a trace with
zero tool calls (the shape `run_task` produces on a caught exception) must be **rejected**
by the scorers.

Run it and confirm it **fails** before changing any production code. That failure is the
evidence the bug is real. Record the observed pre-fix behaviour in the commit message.

Do not proceed to Task 2 until this test exists and fails for the right reason.

## Task 2 — Classify errors in `run_task`

In `evals/run_agent_eval.py`:

- Define an explicit allowlist of agent-behaviour failures that remain datapoints —
  recursion/step-limit exhaustion, tool errors surfaced by the MCP server, malformed tool
  calls. Keep the list short and name each entry.
- Everything else — rate limits (429 / `RESOURCE_EXHAUSTED`), auth failures, connection
  and TLS errors, timeouts, and **any unrecognised exception** — raises and aborts the
  whole eval with a message naming the cause.
- Delete or repurpose the 429 exponential backoff. It waits ~15s total and cannot help
  against a per-day quota; leaving it in place implies a retry strategy that does not
  exist. If it is kept for per-minute limits, it must distinguish the two and give up
  immediately on a daily-quota violation.

## Task 3 — Crash accounting in the report

- `aggregate()` carries `n_attempted`, `n_completed`, `n_crashed`.
- `format_report()` prints run counts as e.g. `n=5 runs (4 crashed)` — never a bare
  `n=5 runs` when crashes occurred.
- If `n_completed == 0`, the section states that no data was collected and prints no
  metrics.
- Keep the existing `model_id` line. Add the provider and the run count to it if not
  already unambiguous.

## Task 4 — Make inapplicability explicit

Phase 1 (required): a trace with zero tool calls invalidates the run. It is counted as a
crash, not scored.

Phase 2 (optional, only if it does not sprawl): let individual property assertions in
`evals/properties.py` return `None` for "not applicable", have the aggregator exclude
`None` from the denominator, and report the not-applicable count alongside the pass rate.

If Phase 2 starts touching more than a handful of call sites, stop and leave a TODO. Phase
1 closes the reported defect on its own.

## Task 5 — Verify

- `pytest -q` — the new control passes, and **all four existing negative controls still
  fire**: fabricated number, exclusion-before-window, undeclared contrast,
  ambiguous-death-not-surfaced.
- `ruff check .` clean under the pinned version.
- Confirm the abort path works without burning quota: inject a simulated 429 (via
  `ScriptedModel` or a monkeypatched provider) and assert the eval aborts rather than
  scoring. No network call required.
  
## Task 6 — Make crashes diagnosable

A crashed run must record and surface the exception type and message, not merely
increment a counter. The report lists distinct failure causes with counts. Rationale:
two eval runs produced four and twenty crashes respectively, and in both cases the
report was indistinguishable from success — the cause had to be recovered by running
`agent/run.py` separately. A crash count without a cause is not diagnostic.
  
  

---

## Definition of done

- [ ] Empty-trace negative control exists, failed before the fix, passes after
- [ ] Unknown and infrastructure errors abort; agent failures remain datapoints via a
      named allowlist
- [ ] Report shows attempted/completed/crashed and refuses to score zero completed runs
- [ ] Backoff either removed or made quota-aware
- [ ] All four prior negative controls still fire
- [ ] `pytest -q` green, `ruff check .` clean
- [ ] 429-abort path covered by a keyless test

## Out of scope

- Do not run the Gemini eval. The daily quota is exhausted and no result from it would be
  interpretable until the above lands.
- Do not add a Foundry/Azure provider. That waits on an access request.
- Do not migrate `create_react_agent` to `langchain.agents`. Known, separate.
- Do not adopt the deferred ruff rules (`I001`, `PLW1510`) or touch the `DTZ` rejections
  recorded in `CLAUDE.md`.

## Leave to the human

- **Final sign-off on the agent-failure allowlist.** Propose it; do not treat it as
  settled. Which failures count as the agent's behaviour is a measurement decision.
- **Whether Phase 2 of Task 4 happens now.**
- **Committing.** Stage in scoped chunks and stop. Show the diff; do not commit or push.
