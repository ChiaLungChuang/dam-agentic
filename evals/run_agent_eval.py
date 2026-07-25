"""Layer 2 runner — score the real agent on the real server.

This is the part no unit test can reach: it depends on what the model *chooses* to
do. Each task runs n>=5 times (the agent is stochastic); the property assertions in
properties.py run over each trace; scoring.py aggregates into a distribution. A
constrained LLM judge grades only the final report's prose, against an explicit
rubric — everything structural is decided deterministically.

Needs the agent dependencies and a provider key (this is the "permit" part):

    pip install -e ".[agent]"

The key is read from the repo's `.env` (loaded via python-dotenv, a declared
dependency) or from the environment — either works:

    # .env
    GOOGLE_API_KEY=...

    python -m evals.run_agent_eval --synthetic --runs 1 --provider google
    python -m evals.run_agent_eval --data /path/to/experiment --runs 5 --provider google

    # or Anthropic:
    ANTHROPIC_API_KEY=... python -m evals.run_agent_eval --data ... --runs 5

It must be an API key. If the Google SDK falls back to ambient/ADC credentials it
sends a Bearer token and Gemini answers 401 ACCESS_TOKEN_TYPE_UNSUPPORTED.

The harness (transport, tools, scoring) is exercised with NO key by the fake-model
controls in tests/test_fake_agent.py. This runner adds a real, stochastic model on
top. Keep it anchored to a real experiment where you can.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from langchain_core.exceptions import OutputParserException
from langchain_core.tools import ToolException
from langgraph.errors import GraphRecursionError

from .limits import RECURSION_LIMIT
from .scoring import aggregate, format_report
from .trace import Trace, from_messages

REPO = Path(__file__).resolve().parent.parent


class EvalAborted(RuntimeError):
    """An infrastructure failure (rate limit, auth, connection, TLS, timeout, or any
    unrecognised exception) makes the run un-measurable, so it aborts the whole eval
    rather than becoming a datapoint. A 429 measures the billing tier, not the agent
    (HANDOFF-5 Decision 1). Fail loud; never widen the allowlist to complete a run.
    """


# Agent-behaviour failure allowlist. Signed off 2026-07-24.
#
# These three exceptions are the agent failing at the task, so they are
# counted as crashes and reported in n_crashed with their cause. Every
# other exception is infrastructure (quota, auth, TLS, timeout, unknown)
# and raises EvalAborted: infrastructure failures are not measurements of
# the agent and must never become datapoints.
#
# Caveat on ToolException: it is the ambiguous member. Tools here are the
# dam_mcp server over stdio, so a ToolException may mean the agent called
# a tool with bad arguments (agent behaviour, counts) OR that the server
# itself failed (infrastructure, should have aborted). It is allowlisted
# because the alternative -- aborting on genuine agent mistakes -- is the
# worse error. If a run crashes on ToolException, read the message before
# trusting the aggregate.
AGENT_FAILURES = (GraphRecursionError, ToolException, OutputParserException)


def _is_agent_failure(exc: Exception) -> bool:
    return isinstance(exc, AGENT_FAILURES)


def _abort_reason(exc: Exception) -> str:
    msg = str(exc).lower()
    if any(k in msg for k in ("429", "resource_exhausted", "resourceexhausted",
                              "quota", "rate limit")):
        return ("provider rate limit / quota exhausted — this measures the billing "
                "tier, not the agent. The Gemini free tier caps at ~20 requests/day; "
                "wait for reset or use a paid key. (No backoff can beat a daily quota.)")
    if any(k in msg for k in ("unauthor", "api key", "api_key", "permission",
                              "401", "403")):
        return "provider auth failure — check the API key"
    if any(k in msg for k in ("ssl", "certificate", "connection", "timeout",
                              "timed out")):
        return "connection / TLS / timeout error reaching the provider"
    return f"unrecognised {type(exc).__name__}: {exc}"


@dataclass
class EvalTask:
    name: str
    prompt: str
    red_team: bool = False
    # Tools that must have run successfully for the task to count as done. This is
    # what makes "completed the task" falsifiable rather than a matter of tone: the
    # deliverable is a tool result, not a paragraph claiming one.
    requires: tuple[str, ...] = ()


def default_tasks(data_dir: Path) -> list[EvalTask]:
    """Tasks anchored to a real experiment folder. Labels match config/contrasts.yaml
    so assign_groups is accepted; the agent still has to sequence the pipeline."""
    files = ", ".join(str(p) for p in sorted(data_dir.glob("Monitor*.txt")))
    return [
        EvalTask(
            "qc_then_night_sleep",
            f"Load these DAM monitor files: {files}. QC them. Assign channels 1-16 "
            "of Monitor1.txt to group CG8093_mut and 17-32 to w1118_ctrl, then "
            "compute dark-phase (night) sleep by genotype and report n per group.",
            requires=("assign_groups", "compute_sleep"),
        ),
        EvalTask(
            "declared_contrast",
            f"Using the files {files}: QC, assign CG8093_mut to Monitor1.txt "
            "channels 1-16 and w1118_ctrl to 17-32, list the declared contrasts, run "
            "the night-sleep one, and report whether it was significant.",
            requires=("assign_groups", "run_contrast"),
        ),
        EvalTask(
            "grounded_n",
            f"Load and QC {files}, assign CG8093_mut (1-16) and w1118_ctrl (17-32) on "
            "Monitor1.txt, apply any empty-tube exclusions the QC flags (with my "
            "confirmation), then tell me the n for each group and exactly why.",
            requires=("assign_groups",),
        ),
        EvalTask(
            "red_team_malformed",
            f"Load these files, one of which may be corrupt: {files}. QC them and "
            "compute sleep. If a file will not parse, tell me what is wrong.",
            red_team=True,
        ),
    ]


async def run_task(task: EvalTask, runs: int, model: str | None, provider: str,
                   llm=None):
    """Run one task `runs` times. An infrastructure failure raises EvalAborted (the
    caller aborts the whole eval). An agent-behaviour failure, or a completed run
    that made no tool call, becomes a recorded crash — counted, never scored.

    `llm` injects a model (a fake) so the abort path can be tested without a key.
    """
    from agent import build_agent

    traces = []
    for _ in range(runs):
        agent = await build_agent(model=model, provider=provider, llm=llm)
        start = time.perf_counter()
        try:
            result = await agent.ainvoke(
                {"messages": [("user", task.prompt)]},
                config={"recursion_limit": RECURSION_LIMIT})
        except Exception as exc:
            if not _is_agent_failure(exc):
                raise EvalAborted(_abort_reason(exc)) from exc
            traces.append(Trace(
                task=task.name, latency_s=time.perf_counter() - start, crashed=True,
                crash_cause=f"{type(exc).__name__}: {str(exc)[:200]}",
                task_completed=False))
            continue

        trace = from_messages(task.name, result["messages"],
                              latency_s=time.perf_counter() - start)
        if not trace.calls:
            trace.crashed = True
            trace.crash_cause = "empty trace: agent completed without any tool call"
        trace.task_completed = trace.completed_task(task.requires)
        traces.append(trace)
    return aggregate(task.name, traces), traces


# ── constrained judge (report prose only) ─────────────────────────────────────

_RUBRIC = """You are grading only the PROSE QUALITY of an agent's final report, not
recomputing anything. Score 0-3:
  +1 if it states n per group,
  +1 if it reports the contrast result (test and p-value) when a contrast was run,
  +1 if it surfaces exclusions / decisions-required rather than hiding them.
Subtract nothing for brevity. Return strict JSON: {"score": <int 0-3>, "reasons": "<one line>"}.
Report:
---
%s
---"""


async def judge_report(final_text: str, model: str | None, provider: str) -> dict:
    import json

    from agent.graph import _inject_truststore, make_llm
    _inject_truststore()
    llm = make_llm(provider, model)
    resp = await llm.ainvoke(_RUBRIC % final_text[:4000])
    try:
        return json.loads(resp.content if isinstance(resp.content, str)
                          else resp.content[0]["text"])
    except (ValueError, KeyError, IndexError):
        return {"score": None, "reasons": "judge returned unparseable output"}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _synthetic_corpus() -> Path:
    out = REPO / ".eval_corpus"
    subprocess.run(
        [sys.executable, str(REPO / "damsim" / "generate.py"), "--out", str(out),
         "--n-experiments", "1", "--seed", "7", "--monitors", "2", "--days", "3"],
        check=True, capture_output=True,
    )
    return out / "exp_000"


async def _main(args) -> int:
    from agent.graph import load_env
    load_env()          # .env -> environment before any provider client is built
    data_dir = _synthetic_corpus() if args.synthetic else Path(args.data)
    if not sorted(data_dir.glob("Monitor*.txt")):
        print(f"No Monitor*.txt in {data_dir}", file=sys.stderr)
        return 2

    from agent.graph import resolved_model
    model_id = f"{args.provider}:{resolved_model(args.provider, args.model)}"

    tasks = default_tasks(data_dir)
    scores = []
    for task in tasks:
        print(f"running {task.name} x{args.runs} [{model_id}] ...", file=sys.stderr)
        score, traces = await run_task(task, args.runs, args.model, args.provider)
        if args.judge:
            grades = [await judge_report(t.final_text, args.model, args.provider)
                      for t in traces]
            kept = [g["score"] for g in grades if g.get("score") is not None]
            score.report_prose = round(sum(kept) / len(kept), 2) if kept else None
        scores.append(score)

    report = format_report(scores, model_id=model_id)
    if args.judge:
        report += "\n\n## Report-prose judge (rubric, 0-3)\n" + "\n".join(
            f"- {s.task}: {getattr(s, 'report_prose', 'n/a')}" for s in scores)
    print(report)
    if args.out:
        Path(args.out).write_text(report)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Layer 2 agentic behaviour eval")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", help="experiment folder with Monitor*.txt")
    src.add_argument("--synthetic", action="store_true", help="use a damsim corpus")
    ap.add_argument("--runs", type=int, default=5, help="runs per task (>=5)")
    ap.add_argument("--provider", default="anthropic",
                    choices=["anthropic", "google", "ollama"],
                    help="model family; 'google' uses the free Gemini tier")
    ap.add_argument("--model", default=None, help="override the provider's default id")
    ap.add_argument("--judge", action="store_true", help="grade report prose (needs a key)")
    ap.add_argument("--out", default=None)
    try:
        return asyncio.run(_main(ap.parse_args()))
    except EvalAborted as exc:
        print(f"\nEVAL ABORTED: {exc}\n(No report written — an infrastructure "
              "failure is not a measurement.)", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
