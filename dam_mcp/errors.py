"""Actionable errors — written for a model deciding what to do next, not a human
reading a traceback.

Rule 4 of the spec: an error string tells the caller *what happened*, *why it
matters*, and *what to do*. `ToolError` is raised inside tool bodies; the server
converts it to a plain string return so the model reads a sentence, never a
Python stack trace.
"""

from __future__ import annotations


class ToolError(Exception):
    """An error whose message is addressed to the model.

    Keep messages in the errors-as-prompts style:
      "<what happened> — <why it matters>. <what to do>."
    """


def unknown_session(session_id: str, known: list[str]) -> ToolError:
    known_str = ", ".join(known) if known else "none"
    return ToolError(
        f"No session '{session_id}'. It may have expired or the id is wrong — "
        f"open sessions are: {known_str}. Call load_experiment to start a new one."
    )


def needs_groups() -> ToolError:
    return ToolError(
        "No groups assigned yet — metrics computed without group assignment are "
        "not interpretable. Call assign_groups with the human-provided "
        "monitor/channel -> group mapping first."
    )


def needs_qc() -> ToolError:
    return ToolError(
        "run_qc has not been run for this session — metrics computed before QC "
        "are not trustworthy (dead flies score as perfect sleepers, empty tubes "
        "inflate n). Call run_qc, review decisions_required, then apply_exclusions."
    )
