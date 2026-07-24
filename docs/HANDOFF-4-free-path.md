# HANDOFF-4 — Phase 2 on the free path

**For:** a Claude Code session working in `~/projects/dam-agentic`
**Read first:** `CLAUDE.md`, `docs/HANDOFF-3.md`
**Goal:** get Phase 2 (agent + Layer 2 eval) from "written" to "executed and
measured" without an Anthropic API key.

---

## 0. Verify before trusting this document

Parts of this brief were written from module names, not from source. Before
acting, confirm:

- `evals/properties.py` vs `evals/test_properties.py` — which holds the property
  assertions and which holds their unit tests
- the exact signature of `evals.trace.from_messages()`
- how `run_agent_eval.py` constructs its agent (does it call `build_agent()`, or
  build its own LLM client?)
- whether the constrained prose judge instantiates a separate model client

If any of these differ from what's assumed below, follow the source and say so.

---

## 1. The three execution lanes

Phase 2 needs a model. There are three, with different jobs. **None requires
purchasing an Anthropic API key.**

| Lane | Model | Purpose | Cost |
|---|---|---|---|
| **Fake** | `GenericFakeChatModel`, scripted | Deterministic tests of *our* code — graph wiring, MCP adapter, trace construction, scoring math. Runs in CI. | free, no network |
| **Gemini** | `gemini-2.5-flash` via Google AI Studio free tier | Real stochastic behaviour, n≥5 runs, Layer 2 scoring | free (rate-limited) |
| **Claude Code** | whatever the session runs | Manual demo of a frontier model choosing tools on real data | covered by existing subscription |

The fake model is not a downgrade of the real one. It tests different things:
the real model tests *model behaviour*, the fake model tests *our harness*. A
failure with a real model is ambiguous — bad code, or just a different sampling
outcome? With a scripted model it is unambiguous.

---

## 2. Task 1 — provider switch in `build_agent()`

`agent/graph.py` currently hardcodes `ChatAnthropic`. Add a `provider` argument.
Keep all provider imports inside the function — the module must stay cheap to
import in a server-only environment.

```python
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "google": "gemini-2.5-flash",
    "ollama": "qwen3",
}

async def build_agent(model: str | None = None, provider: str = "anthropic", llm=None):
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    ...
```

Requirements:

- `llm=` escape hatch so tests can inject a fake model without touching provider
  logic. This is what makes Task 2 possible.
- `provider="google"` → `ChatGoogleGenerativeAI` from `langchain-google-genai`
- `provider="ollama"` → `ChatOllama` from `langchain-ollama`
- `temperature=0` on all providers
- Add `langchain-google-genai` and `truststore` to the `agent` extra in
  `pyproject.toml`
- Thread `--provider` and `--model` through `agent/run.py` and
  `evals/run_agent_eval.py`

**The provider and model ID must be recorded in every eval report.** A property
violation rate without a model identifier is not a result.

---

## 3. Task 2 — fake-model tests, positive *and* negative control

This is the highest-value task in this document. The property scorers have only
ever been run against synthetic traces authored to pass them, which does not
establish that they can fail.

Build two scripted traces:

**Positive control** — a scripted tool-call sequence that is correct by
construction: window applied before exclusions, every reported number sourced
from a tool result, only pre-registered contrasts requested. The scorers must
report clean.

**Negative control** — a scripted sequence that deliberately violates each rail,
one test per violation:

- a number in the final report that appears in no tool result (fabrication)
- exclusions applied before windowing (ordering)
- a contrast not present in `config/contrasts.yaml` (pre-registration)
- an ambiguous death silently resolved rather than surfaced

Each must be *caught*. A scorer that passes the negative control is broken, and
right now we have no evidence either way.

Use `GenericFakeChatModel` (`langchain_core.language_models.fake_chat_models`)
or a minimal `BaseChatModel` subclass returning a fixed `AIMessage` sequence with
`tool_calls` populated. Inject via `build_agent(llm=fake)`.

Note: the MCP server still runs for real in these tests — only the model is
faked. That is deliberate; it exercises the real stdio transport and real tool
schemas.

---

## 4. Task 3 — make CI mean something

`.github/workflows/ci.yml` exists but has never executed (no remote).

- Add the Task 2 tests to the CI job. They need no key and no network beyond the
  local subprocess.
- Keep `truststore` import guarded by `try/except ImportError` so CI runners
  without it still work.
- Fix the two known loose ends in the same commit: the `v0.12.0` tag vs
  `version = "0.11.0"` mismatch, and eval scoring at 24h while production
  defaults to 12h (`DEFAULT_DEATH_HOURS`).
- Then push to a remote so the workflow actually runs. "CI green" is currently a
  claim, not a fact.

---

## 5. Task 4 — Gemini path

Prerequisites the human handles: Google AI Studio key, `GOOGLE_API_KEY` in a
gitignored `.env`.

- Confirm `.env` is in `.gitignore` **before** any key is written to it.
- Add exponential backoff on 429. Free-tier limits are roughly 5–15 RPM with
  daily caps in the hundreds, vary by model, and Google has changed them without
  notice — do not hardcode assumptions, just retry with backoff (1s, 2s, 4s, 8s).
- Budget in *requests*, not runs: a ReAct loop is several model calls per run, so
  5 tasks × 5 runs is well over 100 requests.
- Use a Flash-tier model. Pro's daily cap is too low for repeated eval runs.

Run the smoke corpus first:

```bash
python -m evals.run_agent_eval --synthetic --runs 5 --provider google
```

Expect the scorers to need revision on first contact with a real trace.
That revision *is* a finding — record what changed and why.

---

## 6. Task 5 — Claude Code as MCP client (manual, human-run)

Not a coding task; listed so it is not duplicated in code.

```bash
claude mcp add dam -- python -m dam_mcp.server   # project root, venv active
claude mcp list
```

Ask a question with a known answer against the hTauV337M session. Save the
transcript to `docs/demo/`. This records what *a* frontier model does with the
server — it is not equivalent to an `agent/graph.py` run (different system
prompt, different harness, not scriptable) and must not be reported as one.

---

## 7. Rails — do not violate

These are architectural commitments, not preferences. If a task seems to require
breaking one, stop and ask.

- **The agent never computes.** Do not add a code-execution tool, a data-loading
  path into model context, or any route by which the model could produce a number
  itself. This is the property the whole eval exists to test.
- **Do not populate `config/contrasts.yaml`.** It is a stub on purpose. Real
  contrasts are a scientific decision for the human, not an engineering
  convenience. Tests may use a fixture file elsewhere.
- **Do not disable TLS verification.** No `verify=False`, no permissive
  `SSL_CERT_FILE`. `truststore` is the fix; it keeps verification intact.
- **Do not add features from the deferred list** — LangSmith/Langfuse tracing,
  batch orchestration, session-store locking, Lomb-Scargle rhythmic fraction,
  onset-dating in the QC detector. All deferred deliberately.
- **Do not "improve" the eval by making it easier to pass.** If a property
  assertion is failing against a real trace, the finding is the failure.
- **stdio only.** No OAuth, no remote transport.

---

## 8. Environment notes

```bash
cd ~/projects/dam-agentic
source .venv/bin/activate      # Python 3.13 — /usr/bin/python3 is 3.9, too old
```

- **Engine resolution:** Rtivity-Python is installed editable from
  `~/Rtivity-Python`, which *shadows* the pinned `v0.12.0` git dependency. That
  clone is currently clean and tagged at v0.12.0, so they agree — but verify with
  `git -C ~/Rtivity-Python describe --tags` before attributing any result.
  Ignore the stale nested clone at `~/Rtivity-Python/Rtivity-Python/`.
- **`pip show rtivity-python` reports 0.11.0** — that is the known version-string
  mismatch, not evidence of a stale install.
- **TLS on this machine:** outbound HTTPS from Python fails with
  `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain` while
  `curl` to the same host succeeds. Cause is institutional TLS inspection: the
  internal root CA is in the macOS keychain but not in Python's `certifi` bundle.
  `truststore` resolves it. This affects *any* provider, not just one — record it
  in `HANDOFF-3.md` as a deployment constraint.
- **First run leash:** cap the loop before executing anything with a real model.

  ```python
  result = await agent.ainvoke(
      {"messages": [("user", query)]},
      config={"recursion_limit": 10},
  )
  ```

---

## 9. Definition of done

- [ ] `build_agent()` takes `provider` and `llm`; providers wired; deps declared
- [ ] Positive-control fake-model test passes
- [ ] Negative-control fake-model tests each catch their violation
- [ ] CI runs those tests on a real remote, green
- [ ] Version-string and 24h/12h mismatches fixed
- [ ] `--provider google` eval completes on the synthetic corpus, report written
- [ ] Any scorer revisions documented with rationale
- [ ] Claude Code demo transcript saved (human task)

Not in scope: real-data eval run, cost/latency figures, persistent tracing,
`contrasts.yaml`.
