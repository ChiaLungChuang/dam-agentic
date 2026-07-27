"""The audit stream: one structured record per tool invocation.

Separate from the debugging traces on purpose (HANDOFF-6 Phase 2). Spans answer
"what did the run do, and how long did each step take" for an operator watching a
collector; the audit log answers "who did what to which data" for a reader who may
open it months later. Different retention, different readers — so it is its own
stream, plain JSONL, and depends on nothing but the standard library. If the whole
OpenTelemetry stack is absent, the audit log still writes.

Each record carries:
  * ``timestamp``   — tz-aware UTC ISO-8601. TriKinetics activity data is naive by
                      domain rule (DTZ001/DTZ007 rejected: DAM files carry no
                      timezone), but an audit event is a wall-clock fact about the
                      *server*, not experimental time. The domain rule must not
                      leak here (HANDOFF-6 Phase 2, stated explicitly).
  * ``principal``   — who made the call. A placeholder in Phase 2; Phase 3 wires the
                      OAuth-authenticated principal in here, replacing it.
  * ``tool``        — the tool name.
  * ``session_id``  — the analysis session the call belongs to (None for the call
                      that creates it, load_experiment).
  * ``params``      — the arguments the model sent. Safe to record: by the one
                      architectural rule (the model never sees raw data) no activity
                      counts ever pass through a tool argument — only handles, paths,
                      and specs do.
  * ``data_files``  — the monitor files the call touched, resolved server-side.
  * ``outcome``     — ok / refused / error (see OUTCOMES).
  * ``error``       — the model-facing message when outcome != ok.
  * ``duration_ms`` — wall-clock cost of the dispatch.

``outcome`` is the tool-layer shadow of the HANDOFF-5 taxonomy, not a third
vocabulary:
  * ``ok``      — the tool did its job.
  * ``refused`` — the tool's own guard rejected the request (an errors-as-prompts
                  ToolError). This is a *successful defensive response*: the shadow
                  of "agent behaviour" (the caller sent something the guard caught),
                  never a server fault.
  * ``error``   — the tool raised an unexpected exception: the shadow of an
                  infrastructure / server fault.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

OUTCOMES = ("ok", "refused", "error")

DEFAULT_PRINCIPAL = "anonymous"


def utc_now_iso() -> str:
    """A timezone-aware UTC ISO-8601 timestamp. See the module docstring for why
    this is tz-aware while the analysis layer is deliberately naive."""
    return datetime.now(timezone.utc).isoformat()


def default_principal() -> str:
    """The recorded principal. Phase 2 has no authenticated identity yet — that is
    Phase 3 — so this is an explicit placeholder (overridable with DAM_PRINCIPAL),
    never a silent blank that would read as 'no one'."""
    return os.environ.get("DAM_PRINCIPAL") or DEFAULT_PRINCIPAL


def _state_dir() -> Path:
    env = os.environ.get("DAM_MCP_STATE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".dam_mcp" / "sessions"


def resolve_audit_path() -> Path:
    """Where the audit log lives: DAM_MCP_AUDIT_LOG if set, else audit.jsonl under
    the session state dir so it travels with the sessions it describes."""
    env = os.environ.get("DAM_MCP_AUDIT_LOG")
    if env:
        return Path(env)
    return _state_dir() / "audit.jsonl"


@dataclass
class AuditRecord:
    timestamp: str
    principal: str
    tool: str
    session_id: str | None
    params: dict = field(default_factory=dict)
    data_files: list[str] = field(default_factory=list)
    outcome: str = "ok"
    error: str | None = None
    duration_ms: float = 0.0

    @classmethod
    def now(cls, *, principal: str, tool: str, session_id: str | None,
            params: dict, data_files: list[str], outcome: str,
            error: str | None = None, duration_ms: float = 0.0) -> AuditRecord:
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
        return cls(
            timestamp=utc_now_iso(), principal=principal, tool=tool,
            session_id=session_id, params=params, data_files=list(data_files),
            outcome=outcome, error=error, duration_ms=duration_ms,
        )

    def to_json(self) -> str:
        # default=str so an un-serialisable param (a set, a Path) is coerced rather
        # than dropping the whole audit line — an imperfect repr of one argument
        # beats losing the record of the call entirely.
        return json.dumps(asdict(self), default=str)


class AuditLog:
    """Append-only JSONL writer. One record per line, flushed on write so a crash
    between tool calls does not lose the record of the last one."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else resolve_audit_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, rec: AuditRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(rec.to_json() + "\n")


def read_audit(path: Path | str) -> list[dict]:
    """Read an audit log back as a list of dicts. For tests and offline review."""
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
