# Published eval runs

One directory per run, named by its run id (`eval-<UTC stamp>-<task>-r<n>`),
written by `python -m evals.run_agent_eval ... --publish`:

- `audit.jsonl` — the MCP server's audit stream for that run only (the eval
  points `DAM_MCP_AUDIT_LOG` here for that run's server subprocess);
- `trace.json` — every tool call's name, arguments, `is_error` and full result
  text, plus model id, latency, tokens and outcome. Written as the run ends,
  whether it completed, crashed or aborted.

Commit a run here only when it is cited. Exploratory runs (no `--publish`) go to
the gitignored `evals/.traces/` instead. A hand-started server is unaffected
and logs to its default path. Background: GitHub issue #2, HANDOFF-7 H18-4.
