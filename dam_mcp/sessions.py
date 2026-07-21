"""Server-side session state, keyed by session_id and persisted to disk.

Why sessions (spec rule 3): DAM analysis is a pipeline — load -> QC -> assign ->
exclude -> metrics -> contrast. Each step depends on the last. Threading that
state through the model on every call is expensive and lossy, and it would drag
raw structure toward the boundary. Instead the state lives here; the model holds
only the `session_id` handle.

What is persisted (JSON, all aggregate): file paths, structural summary, the QC
report, group assignments, exclusions + reasons, and computed metric/contrast
summaries. Raw activity counts are NOT persisted here — they are re-read from the
original monitor files on demand inside the engine, and never serialised into a
session. A server restart therefore cannot resurrect raw data into the model's
reach, and also cannot lose an hour of decisions.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _default_state_dir() -> Path:
    env = os.environ.get("DAM_MCP_STATE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".dam_mcp" / "sessions"


@dataclass
class MonitorSummary:
    """Structural facts about one monitor file. No counts — shape only."""
    file: str
    path: str
    n_reads: int
    n_channels: int
    first_ts: str
    last_ts: str
    bin_seconds: int | None


@dataclass
class Session:
    """One analysis pipeline. Everything here is a summary or a decision; the
    numbers that produced them stay in the monitor files on disk."""

    session_id: str
    name: str
    created_at: str
    paths: list[str] = field(default_factory=list)
    monitors: list[dict] = field(default_factory=list)      # MonitorSummary dicts
    warnings: list[str] = field(default_factory=list)

    # Analysis window {"start": iso, "end": iso} or None. Chosen BEFORE exclusions:
    # a fly that dies after the window is valid data inside it, so windowing first
    # and excluding second preserves flies that a later window would have kept.
    window: dict | None = None

    # QC — keyed by death_hours so a re-run at a new threshold is a new artifact.
    qc: dict[str, dict] = field(default_factory=dict)

    # Group assignment: conditions rows (file, region_id, labels, order, window).
    # Human-authored via assign_groups; never inferred by the model.
    groups: list[dict] = field(default_factory=list)

    # Exclusions: [{"monitor": ..., "channel": int, "reason": str, "at": iso}]
    exclusions: list[dict] = field(default_factory=list)

    # Computed artifacts, all aggregate summaries.
    metrics: dict[str, dict] = field(default_factory=dict)
    contrasts: dict[str, dict] = field(default_factory=dict)

    # ── convenience ────────────────────────────────────────────────────────────

    @property
    def group_labels(self) -> list[str]:
        seen: dict[str, int] = {}
        for row in self.groups:
            seen.setdefault(row["labels"], row.get("order", len(seen) + 1))
        return sorted(seen, key=lambda lab: seen[lab])

    def excluded_set(self) -> set[tuple[str, int]]:
        return {(e["monitor"], int(e["channel"])) for e in self.exclusions}


class SessionStore:
    """Registry + on-disk persistence for sessions.

    Sessions are cached in memory and written to `<state_dir>/<id>.json` after
    every mutation, so a crash between tool calls loses nothing. `get` falls back
    to disk, so a fresh server process still finds sessions from before a restart.
    """

    def __init__(self, state_dir: Path | str | None = None):
        self.state_dir = Path(state_dir) if state_dir else _default_state_dir()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Session] = {}

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def create(self, name: str, paths: list[str]) -> Session:
        sid = f"dam-{uuid.uuid4().hex[:12]}"
        session = Session(
            session_id=sid,
            name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
            paths=[str(Path(p).resolve()) for p in paths],
        )
        self._cache[sid] = session
        self.save(session)
        return session

    def get(self, session_id: str) -> Session | None:
        if session_id in self._cache:
            return self._cache[session_id]
        path = self._path(session_id)
        if path.exists():
            session = self._load(path)
            self._cache[session_id] = session
            return session
        return None

    def save(self, session: Session) -> None:
        self._path(session.session_id).write_text(
            json.dumps(asdict(session), indent=2, default=str)
        )

    def list_ids(self) -> list[str]:
        ids = set(self._cache)
        ids.update(p.stem for p in self.state_dir.glob("dam-*.json"))
        return sorted(ids)

    def session_dir(self, session_id: str) -> Path:
        """Per-session working directory for artifacts (QC report, symlinks)."""
        d = self.state_dir / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── internal ───────────────────────────────────────────────────────────────

    def _path(self, session_id: str) -> Path:
        return self.state_dir / f"{session_id}.json"

    @staticmethod
    def _load(path: Path) -> Session:
        data = json.loads(path.read_text())
        return Session(**data)
