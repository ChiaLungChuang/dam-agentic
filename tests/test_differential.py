"""Differential test: two independent readers of the same file must agree.

This system reads every monitor file twice, by two different code paths. QC reads
it with `validate_dam.parse_monitor` (this repo's own parser). Every metric —
`compute_*`, `run_contrast` — reads it again through Rtivity-Python's
`dam_parser`, via the DataStore that `engine.build_store` builds. Neither reader is
checked against a known answer anywhere else in this suite, so each could be wrong
on its own and every other test would still pass.

They can, however, be checked against EACH OTHER: for the same file, the same
tube must have the same total beam crossings whichever reader counted it. A
reader that takes a channel from the wrong column — Rtivity-Python v0.12.0 read
channel k from tube k-1, and channel 1 from the light sensor — gives totals that
match the other reader's for no channel at all. That is what this test is for:
before it existed, QC and the metrics disagreed on which fly was which, and no
test noticed (docs/HANDOFF-14-engine-column-shift.md).

The engine path is exercised exactly as the server exercises it: a session,
groups assigned to all 32 channels, `build_store`. Only per-channel totals are
compared, which is all the relation needs.
"""

from __future__ import annotations

from collections import Counter

from dam_mcp import engine
from dam_mcp.sessions import SessionStore

from conftest import requires_rtivity

pytestmark = requires_rtivity

N_CHANNELS = 32


def _qc_reader(path: str) -> tuple[int, dict[int, int]]:
    """Reads and per-channel totals as QC's parser sees the file."""
    parsed = engine._validate_module().parse_monitor(path)
    assert parsed is not None, f"QC parser could not read {path}"
    totals = [0] * N_CHANNELS
    for row in parsed["rows"]:
        for i, c in enumerate(row["counts"]):
            totals[i] += c
    return len(parsed["rows"]), {ch: totals[ch - 1] for ch in range(1, N_CHANNELS + 1)}


def _engine_reader(tmp_path, path: str) -> tuple[int, dict[int, int]]:
    """Reads and per-channel totals as the metrics see the file: through a session
    with every channel assigned, and the DataStore `build_store` makes from it."""
    store = SessionStore(state_dir=tmp_path)
    session = store.create(name="diff", paths=[path])
    session.monitors, session.warnings = engine.scan_structural([path])
    name = session.monitors[0]["file"]
    session.groups = [
        {"monitor": name, "channel": ch, "labels": "A" if ch <= 16 else "B",
         "order": 1 if ch <= 16 else 2}
        for ch in range(1, N_CHANNELS + 1)
    ]
    store.save(session)
    data = engine.build_store(session, store).data
    reads = Counter(data.groupby("channel")["datetime"].count().tolist())
    assert len(reads) == 1, f"channels hold different numbers of reads: {reads}"
    totals = data.groupby("channel")["activity"].sum()
    return next(iter(reads)), {int(ch): int(v) for ch, v in totals.items()}


def test_qc_and_the_engine_count_the_same_crossings_on_every_channel(
        tmp_path, monitor_files):
    path = monitor_files[0]
    qc_reads, qc = _qc_reader(path)
    eng_reads, eng = _engine_reader(tmp_path, path)

    assert eng_reads == qc_reads, f"engine kept {eng_reads} reads, QC read {qc_reads}"
    assert set(eng) == set(qc) == set(range(1, N_CHANNELS + 1))
    # The fixture must be able to tell channels apart, or agreement proves nothing.
    assert len(set(qc.values())) > N_CHANNELS // 2, "fixture totals too uniform"
    mismatched = {ch: (qc[ch], eng[ch]) for ch in qc if qc[ch] != eng[ch]}
    assert not mismatched, (
        f"QC and engine disagree on {len(mismatched)}/32 channels "
        f"(channel: (QC total, engine total)): {mismatched}"
    )
