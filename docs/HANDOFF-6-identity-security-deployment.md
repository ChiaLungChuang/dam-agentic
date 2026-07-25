# HANDOFF-6 — Identity, Security & Deployment

**Repo:** `~/projects/dam-agentic` (remote: `https://github.com/ChiaLungChuang/dam-agentic.git`)
**Predecessor:** `docs/HANDOFF-5-harness-honesty.md` (closed, commit 88098b9, CI run #5 green)
**State at handoff:** 79 tests pass, ruff 0.16.0 clean, CI green on 3.11/3.12/3.13.

---

## 0. Read this first

This handoff adds authorization, observability, sandboxing, and a private-inference
path to `dam_mcp`. Every control below has a **domain reason**, stated inline. If a
step ever feels like it exists only to tick a box, stop and raise it — the value of
this work is that the controls are real, not that they are present.

Two rails from earlier handoffs still hold and must not be weakened:

- **Window-before-exclusions ordering.** Any refactor that touches the analysis path
  preserves this.
- **Contrasts are pre-registered before data is seen.** `config/contrasts.yaml` is a
  scientific gate. Phase 3 turns it into an enforced one; until then, do not add code
  that lets the agent write to it.

Non-goals for this handoff, explicitly: multi-agent topologies, RAG/retrieval, A2A,
Entra Agent ID (needs a tenant we do not control), and On-Behalf-Of token exchange
(no downstream per-user resource exists yet — revisit if SJ Foundry access lands).

---

## Phase 0 — Unblock the model path

> **Phase 0 CLOSED** (2026-07-25, commit `9cc4fcc`) — acceptance evidence:
> [`phase0-eval-report.md`](phase0-eval-report.md). Closed under the revised
> criterion in [`HANDOFF-6-amendment-1.md`](HANDOFF-6-amendment-1.md) §A, not the
> one below.

**Why first:** the eval is the project's strongest artifact and it currently cannot
complete a single real-model run. Everything downstream is more valuable once real
traces exist.

### 0.1 Replace the retired model id
- `agent/graph.py` hardcodes `gemini-2.5-flash` as the Google default. That id 404s
  with "no longer available to new users" even on the paid tier.
- `gemini-3.6-flash` is confirmed working via curl with the existing credential
  (returned text plus `usageMetadata`, `serviceTier: "standard"`).
- Replace the hardcoded default with a module-level constant, mirroring the
  `DEFAULT_DEATH_HOURS` pattern. Do not scatter model ids across call sites.
- Note in-code: `ListModels` is **not** a list of callable models. Catalog membership
  and callability diverged here; a model id appearing in `ListModels` proves nothing.

### 0.2 Resolve the SDK 401
- Same credential: works via curl with an `x-goog-api-key` header, returns
  `401 ACCESS_TOKEN_TYPE_UNSUPPORTED` through the google-genai Python SDK.
- Working hypothesis: the SDK (or the langchain wrapper above it) is sending the
  credential as a `Bearer` token rather than an API key. Confirm before fixing.
- Fix at the lowest layer that works, and add a comment recording what the failure
  mode looked like — three distinct infrastructure failures (429, 401, 404) have now
  all surfaced identically, and that history is worth preserving in the source.

### 0.3 Verify HANDOFF-5's abort accounting against a real failure
- With 0.1 and 0.2 landed, deliberately induce one infrastructure failure and confirm
  `EvalAborted` fires with a named cause and the report prints `NO DATA` rather than
  all-1.000. HANDOFF-5 proved this with `RaisingModel`; prove it once against the wire.

### 0.4 Account for thinking-model token cost
- `gemini-3.6-flash` is a thinking model: a two-character answer cost 72 total tokens,
  68 of them `thoughtsTokenCount`, and the response carries a `thoughtSignature`.
- Two consequences: per-step token cost is far higher than output length suggests, and
  **thought signatures must round-trip correctly through multi-turn tool calling.** The
  langchain wrapper is a plausible place for that to break. If multi-turn tool calls
  misbehave, check signature round-tripping before suspecting anything else.
- Make sure `usageMetadata` thought tokens are captured in the eval's token accounting,
  not just output tokens.

**Acceptance:** one real Gemini eval run at `--runs 1` completes with non-zero tokens,
plausible latencies, and at least one property genuinely evaluated. Tests still pass.

---

## Phase 1 — Private inference path (Ollama)

**Why:** this is the honest core of "private/secure LLM deployment" — a demonstrable
path where no data leaves the machine. `build_agent(provider="ollama")` already exists;
it has never been exercised end-to-end by the eval.

- Run the full eval through the local Ollama provider. Pick a model that can actually
  drive a tool-calling react loop; if the first choice cannot, record which ones were
  tried and why they failed. That negative result is worth documenting.
- Expect this to surface provider-shaped bugs (tool-call formatting, stop conditions).
  Fix them in the provider seam, not by special-casing the eval.
- Add `docs/private-inference.md`: how to run the whole system with no external network
  egress, which knobs matter, and what is lost (capability, latency) versus a hosted model.
- Keep the SJ AI Foundry endpoint in mind as a fourth provider behind the same seam.
  Do not build it yet — access is not granted — but do not close the seam either.

**Acceptance:** `--provider ollama` produces a scorable eval report with the network
disabled, and `docs/private-inference.md` explains the tradeoff.

---

## Phase 2 — Tracing and audit events (OpenTelemetry)

**Why:** two requirements converge here. Observability tooling wants traces; the
"auditable" requirement in secure-deployment patterns wants a record of who did what
to which data. One instrumentation pass produces both.

- Instrument the agent loop and MCP tool dispatch with OpenTelemetry spans. Export to a
  local collector; Phoenix or Langfuse as the viewing layer, chosen for whichever is
  least intrusive to run locally.
- Emit a **structured audit record per tool invocation**: principal (a placeholder until
  Phase 3 supplies a real one), tool name, parameters, data files touched, timestamp,
  outcome. Keep it a separate stream from the debugging traces — audit records have
  different retention and different readers.
- Reuse the HANDOFF-5 taxonomy. The distinction between infrastructure aborts and
  agent-behaviour crashes is already designed; span status and audit outcome should
  both reflect it rather than inventing a third vocabulary.
- Timestamps: TriKinetics data carries no timezone and `DTZ001`/`DTZ007` are rejected on
  domain grounds for analysis code. **Audit and span timestamps are different** — those
  are wall-clock events, not experimental time, and should be timezone-aware UTC. Do not
  let the domain rule leak into the telemetry layer.

**Acceptance:** a full eval run produces a viewable trace tree, and a separate audit log
in which every tool call and every data file touched is accounted for.

---

## Phase 3 — HTTP transport, OAuth 2.1, and tool scopes

**Why:** this is the centerpiece. A stdio server is one user on one laptop; the moment
the QC server is shared across a lab, "which DAM runs may this person read" stops being
hypothetical. It also makes the pre-registration gate enforceable rather than
convention.

### 3.1 Dual transport
- Add Streamable HTTP alongside stdio. **Keep stdio working** — a server that supports
  both, with auth enforced only on the network path, is the better artifact and keeps
  the Claude Code demo path intact.
- Transport selection belongs in configuration, not in tool implementations.

### 3.2 OAuth 2.1 as an MCP resource server
- Implement the resource-server side: protected resource metadata, authorization server
  discovery, PKCE, token validation, and correct `WWW-Authenticate` challenges.
- Identity provider: local Keycloak by default. It is free, fully inspectable, and
  teaches the whole flow. Auth0's developer tier is an acceptable alternative if a
  hosted IdP is wanted. **Do not claim or emulate Entra Agent ID** — that needs a tenant
  we do not have, and the institution's platform is Microsoft-based, so an inaccurate
  claim there is worse than an absence.
- The `truststore` fix is required anywhere Python makes HTTPS calls on a managed
  endpoint — institutional TLS inspection puts a self-signed cert in the chain. This
  now applies to IdP calls too, not just model providers.

### 3.3 Tool scopes and RBAC
Map the existing tool surface onto scopes along the lines it already splits on:

| Scope | Covers | Domain reason |
|---|---|---|
| `dam:read` | read-only QC / validation tools | ordinary data access |
| `dam:session` | writes under `~/.dam_mcp/sessions` (`DAM_MCP_STATE_DIR`) | mutable state, separable from reads |
| `dam:contrasts:amend` | amendment of `config/contrasts.yaml` | **pre-registration gate** |

- The agent's own token must **not** carry `dam:contrasts:amend`. That is the point:
  contrasts are fixed before data is seen, and a scope the agent cannot hold turns a
  convention enforced by operator discipline into one enforced by the authorization
  layer.
- Enforcement lives in one place, checked before dispatch. Do not scatter scope checks
  through individual tools.
- Wire the authenticated principal into the Phase 2 audit records, replacing the
  placeholder.

### 3.4 Tests
- Keyless where possible, in the spirit of `ScriptedModel` and `RaisingModel`: a fake
  token issuer beats standing up Keycloak inside CI.
- Cover, at minimum: unauthenticated request rejected; valid token with insufficient
  scope rejected; correct scope permitted; **agent token cannot amend contrasts**; stdio
  path unaffected.

**Acceptance:** HTTP transport authenticated end-to-end against a real IdP locally;
scope enforcement covered by keyless tests in CI; stdio still Connected in Claude Code.

---

## Phase 4 — Sandboxed deployment

**Why:** the server reads arbitrary filesystem paths and runs analysis. Containment is
warranted on its own merits.

- Container image: non-root user, read-only root filesystem, dropped capabilities,
  read-only mount of the data directory, writable mount only for the session state dir,
  egress restricted to the configured model provider and IdP.
- `truststore` and the institutional CA situation must be handled inside the image.
- Do the plain-container version first — every line should be explainable. E2B or Modal
  is a reasonable follow-on, not a substitute for understanding the primitive.
- Document the threat model briefly: what the sandbox protects against, and what it does
  not (it does not make a malicious tool call safe; it limits blast radius).

**Acceptance:** the eval runs to completion inside the container against a read-only
data mount, and the container cannot write to the data directory.

---

## Working agreements (carried forward)

- **TDD.** HANDOFF-5 was built this way and it worked. Continue.
- **Scoped commits.** Four-ish per phase, each independently reviewable, as with
  HANDOFF-4 and HANDOFF-5.
- **Lint policy is explicit and pinned.** `ruff==0.16.0`, `[tool.ruff.lint] select =
  ["E4","E7","E9","F"]`. Do not widen the rule set opportunistically. `I001` and
  `PLW1510` are deferred-but-worthwhile; `DTZ001`/`DTZ007` are rejected for analysis
  code on domain grounds (see the Phase 2 note about telemetry being different).
- **Engine dependency.** `Rtivity-Python` is a git dep pinned to `v0.12.0` under the
  `engine` extra; the local editable install shadows it for development. `pip show`
  reporting `0.11.0` is the known version-string mismatch — the bump is still pending
  and is not part of this handoff.
- **CI must stay green** on 3.11/3.12/3.13. New network-dependent work is keyless-tested
  or skipped in CI, never left to fail intermittently.
- Write `docs/HANDOFF-6-identity-security-deployment.md` into the repo and **track it** —
  HANDOFF-5's doc was left untracked and needed a follow-up commit.

## Still open from earlier handoffs

- GitHub issue #1 — Task 4 Phase 2, per-property not-applicable. Not started, not part
  of this handoff.
- Real `config/contrasts.yaml` (still the EXAMPLE stub). This is a **human science
  task**, not a coding task. Phase 3 enforces the gate; it does not decide the contrasts.
- `create_react_agent` deprecation — moved to `langchain.agents` in LangGraph V1.0.
  Migrate opportunistically if Phase 0 or 1 already touches `agent/graph.py`.
- `Rtivity-Python` version-string bump to `v0.12.0`.

## Sequencing note

Phases are ordered by dependency and by value. Phase 0 unblocks everything. Phases 2–4
are the requirement-facing work, but **do not let them displace finishing the eval** —
the harness-honesty work is this project's most differentiated result, and security
controls are table stakes by comparison.
