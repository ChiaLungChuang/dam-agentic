"""Layer 2 runner — score the real agent on the real server.

This is the part no unit test can reach: it depends on what the model *chooses* to
do. Each task runs n>=5 times (the agent is stochastic); the property assertions in
properties.py run over each trace; scoring.py aggregates into a distribution. A
constrained LLM judge grades only the final report's prose, against an explicit
rubric — everything structural is decided deterministically.

Needs the agent dependencies and a provider (this is the "permit" part). No paid
Anthropic key is required — the free Gemini tier works:

    pip install -e ".[agent]"
    export GOOGLE_API_KEY=...     # free Google AI Studio tier
    python -m evals.run_agent_eval --synthetic --runs 5 --provider google
    python -m evals.run_agent_eval --data /path/to/experiment --runs 5 --provider google

    # or, with a paid key:
    export ANTHROPIC_API_KEY=... && python -m evals.run_agent_eval --data ... --runs 5

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

from .scoring import aggregate, format_report
from .trace import from_messages

REPO = Path(__file__).resolve().parent.parent


@dataclass
class EvalTask:
    name: str
    prompt: str
    red_team: bool = False


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
        ),
        EvalTask(
            "declared_contrast",
            f"Using the files {files}: QC, assign CG8093_mut to Monitor1.txt "
            "channels 1-16 and w1118_ctrl to 17-32, list the declared contrasts, run "
            "the night-sleep one, and report whether it was significant.",
        ),
        EvalTask(
            "grounded_n",
            f"Load and QC {files}, assign CG8093_mut (1-16) and w1118_ctrl (17-32) on "
            "Monitor1.txt, apply any empty-tube exclusions the QC flags (with my "
            "confirmation), then tell me the n for each group and exactly why.",
        ),
        EvalTask(
            "red_team_malformed",
            f"Load these files, one of which may be corrupt: {files}. QC them and "
            "compute sleep. If a file will not parse, tell me what is wrong.",
            red_team=True,
        ),
    ]


async def run_task(task: EvalTask, runs: int, model: str | None, provider: str):
    from agent import build_agent

    traces = []
    for _ in range(runs):
        agent = await build_agent(model=model, provider=provider)
        start = time.perf_counter()
        try:
            result = await _ainvoke_with_backoff(
                agent, {"messages": [("user", task.prompt)]},
                {"recursion_limit": 12})             # first-run leash (HANDOFF-4 §8)
            messages = result["messages"]
        except Exception as exc:                     # a crashed run is still a datapoint
            messages = [_ErrMsg(str(exc))]
        latency = time.perf_counter() - start
        traces.append(from_messages(task.name, messages, latency_s=latency))
    return aggregate(task.name, traces), traces


async def _ainvoke_with_backoff(agent, payload, config, waits=(1, 2, 4, 8)):
    """Retry on a provider rate-limit (429) with exponential backoff. Free tiers
    (Gemini) allow only a few requests/minute and a ReAct loop is several calls per
    run, so 429s are expected — back off rather than hardcode a limit that Google
    changes without notice."""
    last = None
    for wait in (0, *waits):
        if wait:
            await asyncio.sleep(wait)
        try:
            return await agent.ainvoke(payload, config=config)
        except Exception as exc:
            if any(k in str(exc).lower()
                   for k in ("429", "resourceexhausted", "rate limit", "quota")):
                last = exc
                continue
            raise
    raise last


class _ErrMsg:
    def __init__(self, text):
        self.type = "ai"
        self.content = f"[run crashed] {text}"
        self.tool_calls = []


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
    return asyncio.run(_main(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
