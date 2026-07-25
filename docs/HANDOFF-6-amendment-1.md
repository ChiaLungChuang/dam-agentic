# HANDOFF-6 — Amendment 1

**Applies to:** `docs/HANDOFF-6-identity-security-deployment.md`
**Reason:** Phase 0 landed, and in landing it invalidated its own acceptance criterion.
Work was also carried out that the original plan does not mention. Both need recording
before the phase is closed.

---

## A. Phase 0 acceptance criterion — replaced

**Removed:**

> **Acceptance:** one real Gemini eval run at `--runs 1` completes with non-zero tokens,
> plausible latencies, and at least one property genuinely evaluated. Tests still pass.

**Why removed:** the first real run met every clause of it — 23,150 tokens, 13.8 s
latency, all seven properties evaluated — while the agent failed the task (two errored
`assign_groups` calls, step-limit exhaustion, final text "Sorry, need more steps to
process this request.") and scored 1.000 on every property. The properties are rail
checks; a run that stops early violates nothing, so they pass truthfully and the score
means nothing. The gate would have signed Phase 0 off on precisely the class of result
this project exists to detect.

"At least one property genuinely evaluated" is the defective clause. Evaluation is not
the bar — **falsifiability** is. A property that cannot fail on the run in front of it
has not been evaluated in any useful sense.

**Replaced with:**

> **Acceptance:** a real Gemini eval run at `--runs 1` completes with non-zero tokens and
> plausible latencies, **and the report visibly distinguishes a run that completed the
> task from one that did not.** A run that fails its task must not score 1.000.
> Specifically:
>
> - Task completion is reported as a distinct state alongside the existing
>   `n_attempted` / `n_completed` / `n_crashed` accounting — **not** as an eighth
>   property averaged in with the rails, which would let a failed run still score 6/7.
> - `recursion_limit` is derived from a measured floor (the super-step count of a
>   known-good `ScriptedModel` trajectory) with a stated multiplier, and recorded as a
>   fixed parameter of the eval rather than a literal at the call site.
> - At least one property is evaluated **non-vacuously** — there exists a control input
>   on which it fails.
>
> Tests still pass.

---

## B. Phase 0.3 — still open, and now cheap

0.1, 0.2 and 0.4 are landed (see §C). **0.3 is the only outstanding Phase 0 item.**

Induce one real infrastructure failure against the wire and confirm `EvalAborted` fires
with a named cause and the report prints `NO DATA` rather than all-1.000. `RaisingModel`
proved this keylessly in HANDOFF-5; 0.3 asks for it once against a real provider.

Now that the credential path resolves correctly, this costs almost nothing: corrupt
`GOOGLE_API_KEY` for a single run and assert the abort path. Fold it into the next chunk
rather than carrying it forward.

---

## C. Unscoped work carried out: tool-contract hardening

The original plan does not mention any change to `dam_mcp`. The following was done
anyway, and §0's standing instruction — raise it when a step feels like box-ticking,
because the value is that the controls are real — is the licence being claimed. Each
item below was upstream of the eval's validity, which is Phase 0's actual purpose.

**Phase 0 as planned (commits `88098b9..78e963b`, CI run 30128216132):**

| Commit | Contents |
|---|---|
| `ec3dd6b` | 0.1 — `DEFAULT_GOOGLE_MODEL` constant; key sent as an API key, not a Bearer token |
| `2145577` | `.env` loaded at both entry points; `python-dotenv` declared (was transitive via `pydantic-settings`) |
| `78e963b` | 0.4 — `Trace.reasoning_tokens`; thought tokens confirmed folded into `output_tokens` by LangChain |

0.2's working hypothesis was wrong in an instructive way. The 401 was not the SDK
sending a Bearer token by choice — `GOOGLE_API_KEY` existed only in `.env`, nothing
loaded it, and the SDK fell through to ambient credential resolution. The `.env` gap was
the 401's root cause, not a separate task.

**Unplanned (commits `78e963b..38de225`, CI run 30132807092):**

| Commit | Contents |
|---|---|
| `b1b447b` | `monitor_keys` in `LoadResult`; basename normalization in `assign_groups`; channel-spec forms stated in the docstring |
| `38de225` | `apply_exclusions` fails loudly on an unresolvable key; reports `n_before` / `n_after` / `n_excluded` |

**Why this was not optional.** The prompt supplies full paths; `assign_groups` required
basenames; nothing declared which. The model was coin-flipping between two readings the
environment made equally plausible. Per HANDOFF-5's own principle — infrastructure
failures are not measurements of the agent — an ambiguous tool schema is the same
category one layer in. Scaling to `--runs 5` with that live would have spent part of the
variance budget measuring our own schema.

`apply_exclusions` was worse and was found while fixing the first: an unresolved key was
recorded as a successful exclusion that excluded nobody, producing a wrong *n* rather
than an error. That is a silent data-integrity defect in a QC server, and it contradicts
the `flag_ambiguous_deaths` / `ambiguous_death_surfaced` design ethos directly.

---

## D. Findings not in the original plan

**D.1 — `set_analysis_window` silently drops monitors.** With Monitor1 ending 08:58 and
Monitor2 08:56, a window of 08:56:30 → 08:58:00 returns `is_error: false` and a tally
listing only Monitor1. The string `Monitor2` appears nowhere in the result.
`WindowResult` has no field to surface it on.

This is the most serious of the three contract defects, and the reason is position: the
window is set *before* the window-before-exclusions rail runs, so grouping, exclusions,
sleep, survival and contrasts all operate on a silently truncated dataset while the tally
looks clean. Where monitors map to genotypes or replicates, an entire arm of a comparison
can vanish and still produce a tidy result.

*Status: fix scoped as reporting only — add the dropped monitors to `WindowResult`,
`is_error` stays false.*

**D.2 — undeclared window preconditions.** A 30-minute window succeeds with a
clean-looking tally despite being far below the 12 h death window and 48 h decline
window. It does not fail; it returns numbers. Zero deaths is a plausible-looking answer.
Losing data silently is bad; manufacturing well-formed numbers from a window that cannot
support the computation is at least as bad, because there is nothing anomalous to notice.

Short windows are legitimate for activity work, so this is not a hard error. The fix is a
capability declaration in the result — the same move as `monitor_keys`: state the
contract instead of making the caller infer it. *Status: filed, not built.*

**D.3 — contract tests had no negative controls.** Both assertions in the
`assign_groups` refusal test were present verbatim in the pre-fix message, so the
`monitor_keys` pointer was entirely unpinned: the test would have passed on a full revert
of the change it was written to cover. Properties got negative controls in HANDOFF-4; the
contract tests did not. *Status: pointer now pinned; a spot-check of the newest
assertions is filed.*

**Note the shape.** D.1, D.2, D.3 and §A are the same defect — an operation that does
nothing, or does something wrong, and reports success. It has now been found at five
layers: the scorer (HANDOFF-5), the property set (issue #1), the tools, the test suite,
and this document's own acceptance criterion. Each was found by a deliberate check, none
by accident. Assume the sixth exists.

---

## E. Consequences for later phases

**Phase 2.** GitHub issue #2 (persist run traces: args and error text per tool call)
overlaps the per-invocation audit record almost entirely — args, outcome, timestamp,
files touched. The instruction to keep audit and debug traces as separate streams stands;
the point is that one instrumentation pass produces both. Cross-reference the issue to
Phase 2 so it is not built twice.

**Phase 3.** `dam:contrasts:amend` — a scope the agent cannot hold — is the strongest
idea in the document. It currently guards `config/contrasts.yaml`, which is still the
EXAMPLE stub. The original doc notes that deciding the contrasts is a human task; the
consequence has now changed. The missing contrasts file no longer only blocks the
science, it hollows out the phase the plan calls the centerpiece. **Phase 3 should not
open until `contrasts.yaml` holds real pre-registered comparisons.**

---

## F. Carried-forward items — status

| Item | Status |
|---|---|
| `create_react_agent` → `langchain.agents` | Trigger fired (Phase 0 touched `agent/graph.py` twice), migration not taken. Still open. |
| Track `docs/HANDOFF-6-*.md` in the repo | Unverified. HANDOFF-5's doc was left untracked; check before closing the phase. |
| Issue #1 — per-property not-applicable | Not started, unchanged. |
| Real `config/contrasts.yaml` | Not started. Now blocking Phase 3 (see §E). |
| `Rtivity-Python` version-string bump to `v0.12.0` | Not started, unchanged. |

---

## G. Immediate sequence

1. `set_analysis_window` drop reporting (D.1) — one bounded chunk.
2. Phase 0.3 against the wire (§B).
3. Step floor via `ScriptedModel`; `recursion_limit` set and recorded.
4. Task completion as the fourth aggregate state.
5. `--runs 5`. **This closes Phase 0 under the revised criterion in §A.**

No further tool work before step 5. The original sequencing note stands and is
load-bearing: Phases 2–4 do not displace finishing the eval.
