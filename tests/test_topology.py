"""Declared monitor topology — the `machines:` block (HANDOFF-12, HANDOFF-13).

A machine is one rack of 32 tubes passing through three detector boards; its three
monitor files are three beams on the same 32 flies. Declared, the engine sums the
beams per tube before QC and everything downstream runs on tubes: n counts
animals, and a fly that stops visiting one end of its tube is no longer called
dead there while alive at the other.

The beam files here are built from a real damsim monitor file by splitting every
count across three files whose per-tube sum IS the original. That gives the
strongest oracle available: summing must reproduce the original exactly, and QC
on the summed machine must equal QC on the original file.

Undeclared behaviour is unchanged by construction — no fixture, declaration or
existing test changes in this slice — and the tests marked UNDECLARED pin it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import requires_rtivity
from dam_mcp import config, engine
from dam_mcp.errors import ToolError

ROLES = ("food", "middle", "plug")
TAIL_ROWS = 14 * 60          # 14 h at 60 s bins: past the 12 h death threshold
BEAMS = {"M1": ("Monitor1.txt", "Monitor2.txt", "Monitor3.txt"),
         "M2": ("Monitor4.txt", "Monitor5.txt", "Monitor6.txt")}


# ── building beam files ───────────────────────────────────────────────────────

def _rows(path: Path) -> list[list[str]]:
    return [ln.rstrip("\n").split("\t") for ln in path.read_text().splitlines()
            if ln.count("\t") >= 41]


def _write(path: Path, rows: list[list[str]]) -> None:
    path.write_text("".join("\t".join(r) + "\n" for r in rows))


def split_into_beams(src: Path, out_dir: Path, names: tuple[str, str, str],
                     tail_at_food: int | None = None) -> list[Path]:
    """Three beam files (food, middle, plug) whose per-tube sum equals `src`.

    `tail_at_food` plants the case per-beam QC gets wrong: for that channel, every
    count in the final TAIL_ROWS goes to the food beam — a fly that settled at the
    food end and stopped visiting the rest of its tube. Alive, but silent on the
    middle and plug beams for longer than the death threshold."""
    rows = _rows(src)
    beams: list[list[list[str]]] = [[], [], []]
    for i, r in enumerate(rows):
        parts = [list(r[:42]), list(r[:42]), list(r[:42])]
        for col in range(10, 42):
            c = int(r[col])
            share = [c - 2 * (c // 3), c // 3, c // 3]
            if tail_at_food == col - 9 and i >= len(rows) - TAIL_ROWS:
                share = [c, 0, 0]
            for b in range(3):
                parts[b][col] = str(share[b])
        for b in range(3):
            beams[b].append(parts[b])
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / n for n in names]
    for p, b in zip(paths, beams):
        _write(p, b)
    return paths


def _alive_active_channel(src: Path) -> int:
    """A channel QC calls alive on `src` with counts in both the early run and its
    final TAIL_ROWS — so a tail moved to the food beam leaves the other beams
    with activity followed by >12 h of silence."""
    rows = _rows(src)
    for ch in range(1, 33):
        early = sum(int(r[9 + ch]) for r in rows[: len(rows) // 2])
        tail = sum(int(r[9 + ch]) for r in rows[-TAIL_ROWS:])
        if early >= 30 and tail >= 30:
            return ch
    raise AssertionError("no suitably active channel in the fixture corpus")


@pytest.fixture(scope="module")
def rig(corpus_dir, tmp_path_factory):
    """Six beam files = two machines, built from the corpus's Monitor1/Monitor2.
    M1's chosen channel carries the planted food-end tail."""
    root = tmp_path_factory.mktemp("rig")
    src1, src2 = corpus_dir / "Monitor1.txt", corpus_dir / "Monitor2.txt"
    planted = _alive_active_channel(src1)
    files = (split_into_beams(src1, root, BEAMS["M1"], tail_at_food=planted)
             + split_into_beams(src2, root, BEAMS["M2"]))
    return {"dir": root, "files": [str(p) for p in files], "planted": planted,
            "sources": {"M1": src1, "M2": src2}}


def _declaration(tmp_path: Path, machines: str | None, groups: str = "") -> str:
    path = tmp_path / "contrasts-beamrig.yaml"
    text = "experiment: beamrig\n" + (groups or "groups:\n  ctrl: 32\n  mut: 32\n")
    if machines is not None:
        text += machines
    path.write_text(text)
    return str(path)


MACHINES = """machines:
  M1: {food: Monitor1.txt, middle: Monitor2.txt, plug: Monitor3.txt}
  M2: {food: Monitor4.txt, middle: Monitor5.txt, plug: Monitor6.txt}
"""


# ── the declaration ───────────────────────────────────────────────────────────

def test_a_machines_block_is_read_by_role(tmp_path):
    got = config.declared_machines(Path(_declaration(tmp_path, MACHINES)))
    assert got == {"M1": dict(zip(ROLES, BEAMS["M1"])),
                   "M2": dict(zip(ROLES, BEAMS["M2"]))}


@pytest.mark.parametrize("machines", [None, "machines: independent\n"])
def test_no_machines_and_independent_both_mean_undeclared(tmp_path, machines):
    """UNDECLARED. `independent` says out loud what absence already means."""
    assert config.declared_machines(Path(_declaration(tmp_path, machines))) == {}


@pytest.mark.parametrize("roles", [
    "{food: Monitor1.txt, middle: Monitor2.txt}",
    "{plug: Monitor3.txt}",
])
def test_a_machine_without_all_three_roles_refuses_and_says_why(tmp_path, roles):
    """Refused uniformly, one role short or two — and for a stated reason, with
    the honest alternatives named, not a bare 'must have three'."""
    decl = _declaration(tmp_path, f"machines:\n  M1: {roles}\n")
    with pytest.raises(ToolError) as e:
        config.declared_machines(Path(decl))
    msg = str(e.value)
    assert "summed zero means the fly crossed no beam anywhere" in msg   # the reason
    assert "leave the machine out" in msg                                # alternative 1
    assert "ROLES in dam_mcp/config.py" in msg                           # alternative 2
    assert "missing" in msg


def test_the_role_vocabulary_is_closed(tmp_path):
    decl = _declaration(tmp_path, "machines:\n  M1: {food: a.txt, middle: b.txt, "
                                  "plug: c.txt, centre: d.txt}\n")
    with pytest.raises(ToolError, match=r"\['centre'\].*food.*middle.*plug"):
        config.declared_machines(Path(decl))


def test_a_file_named_twice_refuses_naming_both_slots(tmp_path):
    decl = _declaration(tmp_path, "machines:\n"
                        "  M1: {food: a.txt, middle: b.txt, plug: c.txt}\n"
                        "  M2: {food: c.txt, middle: d.txt, plug: e.txt}\n")
    with pytest.raises(ToolError, match=r"M1\.plug.*M2\.food"):
        config.declared_machines(Path(decl))


def test_a_path_is_refused_in_favour_of_the_filename(tmp_path):
    decl = _declaration(tmp_path, "machines:\n  M1: {food: /data/a.txt, "
                                  "middle: b.txt, plug: c.txt}\n")
    with pytest.raises(ToolError, match=r"'a\.txt'"):
        config.declared_machines(Path(decl))


@pytest.mark.parametrize("mid", ["../x", "M 1", ""])
def test_a_machine_id_must_be_safe_as_a_key_and_a_filename(tmp_path, mid):
    decl = _declaration(tmp_path, f"machines:\n  '{mid}': {{food: a.txt, "
                                  "middle: b.txt, plug: c.txt}\n")
    with pytest.raises(ToolError, match="machine id"):
        config.declared_machines(Path(decl))


def test_a_malformed_block_refuses_at_load_not_later(tmp_path):
    """Like a non-positive declared n: wrong in the declaration, so said when the
    declaration is read — list_contrasts refuses too, not only load_experiment."""
    decl = _declaration(tmp_path, "machines: [M1, M2]\n")
    with pytest.raises(ToolError, match="machines:"):
        config.list_contrasts(Path(decl))


# ── summing ───────────────────────────────────────────────────────────────────

def _beam_paths(rig, machine) -> dict[str, Path]:
    return dict(zip(ROLES, (rig["dir"] / f for f in BEAMS[machine])))


def test_summing_reproduces_the_original_exactly(rig, tmp_path):
    """The oracle: M2's beams are an exact three-way split of a real file."""
    out = tmp_path / "M2"
    assert engine.sum_machine("M2", _beam_paths(rig, "M2"), out) == []
    want = [r[1:3] + r[10:42] for r in _rows(rig["sources"]["M2"])]
    got = [r[1:3] + r[10:42] for r in _rows(out)]
    assert got == want


def test_qc_on_the_summed_machine_equals_qc_on_the_original(rig, tmp_path):
    out = tmp_path / "M2"
    engine.sum_machine("M2", _beam_paths(rig, "M2"), out)
    summed = engine.run_validate([str(out)], 12.0, tmp_path / "a.json")
    original = engine.run_validate([str(rig["sources"]["M2"])], 12.0, tmp_path / "b.json")
    (s,), (o,) = summed["monitors"], original["monitors"]
    assert s["tally"] == o["tally"]
    assert [(c["channel"], c["state"]) for c in s["channels"]] == \
           [(c["channel"], c["state"]) for c in o["channels"]]


def test_beams_are_aligned_within_the_machine_and_the_drop_is_reported(rig, tmp_path):
    """The beams can start a read apart. Only reads all three recorded are summed,
    and the dropped reads are named — alignment is mechanical, never silent."""
    beams = _beam_paths(rig, "M2")
    late = tmp_path / "late_middle.txt"
    _write(late, _rows(beams["middle"])[2:])               # middle starts 2 reads late
    beams["middle"] = late
    out = tmp_path / "M2"
    (warning,) = engine.sum_machine("M2", beams, out)
    assert "dropped 2 of Monitor4.txt (food), 2 of Monitor6.txt (plug)" in warning
    assert len(_rows(out)) == len(_rows(rig["sources"]["M2"])) - 2


def test_beams_with_different_bin_widths_are_refused(rig, tmp_path):
    beams = _beam_paths(rig, "M2")
    coarse = tmp_path / "coarse_plug.txt"
    _write(coarse, _rows(beams["plug"])[::5])              # a 5-min board
    beams["plug"] = coarse
    with pytest.raises(ToolError, match="disagree on bin width"):
        engine.sum_machine("M2", beams, tmp_path / "M2")


def test_beams_that_never_overlap_are_refused(rig, tmp_path):
    beams = _beam_paths(rig, "M2")
    rows = _rows(beams["plug"])
    shifted = tmp_path / "shifted_plug.txt"
    _write(shifted, [r[:1] + ["01 Jan 20"] + r[2:] for r in rows])
    beams["plug"] = shifted
    with pytest.raises(ToolError, match="share no timestamp"):
        engine.sum_machine("M2", beams, tmp_path / "M2")


def test_a_bad_status_on_any_beam_survives_summing(rig, tmp_path):
    beams = _beam_paths(rig, "M2")
    rows = _rows(beams["plug"])
    rows[100][3] = "51"
    bad = tmp_path / "bad_plug.txt"
    _write(bad, rows)
    beams["plug"] = bad
    out = tmp_path / "M2"
    engine.sum_machine("M2", beams, out)
    assert _rows(out)[100][3] == "51"


# ── over the protocol ─────────────────────────────────────────────────────────

pytest.importorskip("mcp")
from evals.harness import MCPHarness  # noqa: E402


async def _load(h, files) -> dict:
    r = await h.call("load_experiment", paths=files, name="rig")
    assert not r.is_error, r.text
    return r.data


@pytest.mark.asyncio
async def test_a_declared_rig_is_keyed_by_machine(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    async with MCPHarness(tmp_path / "s") as h:
        d = await _load(h, rig["files"])
    assert d["monitor_keys"] == ["M1", "M2"]
    assert d["n_monitors"] == 6                     # what was loaded is still six files
    assert d["machines"] == [{"machine": m, **dict(zip(ROLES, BEAMS[m]))}
                             for m in ("M1", "M2")]


@pytest.mark.asyncio
async def test_UNDECLARED_load_is_unchanged(rig, tmp_path, monkeypatch):
    """No machines: block, no `machines` key, filenames as keys — the result has
    exactly the shape it had before topology existed."""
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, None))
    async with MCPHarness(tmp_path / "s") as h:
        d = await _load(h, rig["files"])
    assert set(d) == {"session_id", "name", "n_monitors", "monitors",
                      "monitor_keys", "time_window", "warnings", "next"}
    assert d["monitor_keys"] == [Path(f).name for f in rig["files"]]


@pytest.mark.asyncio
async def test_the_per_beam_contradiction_is_gone(rig, tmp_path, monkeypatch):
    """The reason topology exists. A fly that settled at the food end is called
    dead on the middle and plug beams by per-beam QC — while the food beam shows
    it alive. Summed first, the same tube is alive and nothing contradicts."""
    ch = rig["planted"]
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, None))
    async with MCPHarness(tmp_path / "beam") as h:
        sid = (await _load(h, rig["files"]))["session_id"]
        per_beam = (await h.call("run_qc", session_id=sid)).data
    dead = {(f["monitor"], f["state"]) for f in per_beam["flags"] if f["channel"] == ch}
    assert ("Monitor3.txt", "died") in dead           # plug: "dead"
    assert not any(m == "Monitor1.txt" for m, _ in dead)   # food: alive

    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    async with MCPHarness(tmp_path / "machine") as h:
        sid = (await _load(h, rig["files"]))["session_id"]
        summed = (await h.call("run_qc", session_id=sid)).data
    assert not any(f["monitor"] == "M1" and f["channel"] == ch for f in summed["flags"])
    assert set(summed["tally"]) == {"M1", "M2"}
    assert all(sum(t.values()) == 32 for t in summed["tally"].values())


@pytest.mark.asyncio
async def test_a_declared_n_counts_tubes_on_a_rig(rig, tmp_path, monkeypatch):
    """The checksum needs no change: on a rig the channels ARE tubes. One machine
    per group is n = 32 and passes; the same files assigned per beam are 96 and
    refused — the tripwire HANDOFF-13 records, still standing."""
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    async with MCPHarness(tmp_path / "m") as h:
        sid = (await _load(h, rig["files"]))["session_id"]
        await h.call("run_qc", session_id=sid)
        r = await h.call("assign_groups", session_id=sid,
                         mapping={"ctrl": {"M1": [1, 32]}, "mut": {"M2": [1, 32]}})
    assert not r.is_error, r.text
    assert r.data["group_sizes"] == {"ctrl": 32, "mut": 32}

    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, None))
    async with MCPHarness(tmp_path / "b") as h:
        sid = (await _load(h, rig["files"]))["session_id"]
        await h.call("run_qc", session_id=sid)
        r = await h.call("assign_groups", session_id=sid, mapping={
            "ctrl": {f: [1, 32] for f in BEAMS["M1"]},
            "mut": {f: [1, 32] for f in BEAMS["M2"]}})
    assert r.is_error and "declares n=32" in r.text and "96" in r.text


@pytest.mark.asyncio
async def test_a_beam_filename_is_refused_by_naming_its_machine_and_role(
        rig, tmp_path, monkeypatch):
    """Not substituted: the caller learns the structure, and nothing is assigned."""
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    async with MCPHarness(tmp_path / "s") as h:
        sid = (await _load(h, rig["files"]))["session_id"]
        await h.call("run_qc", session_id=sid)
        r = await h.call("assign_groups", session_id=sid,
                         mapping={"ctrl": {"Monitor5.txt": [1, 32]}})
        manifest = await h.read_resource(f"dam://session/{sid}/manifest")
    assert r.is_error
    assert "'Monitor5.txt' is the middle beam of machine 'M2'" in r.text
    assert "_No groups assigned._" in manifest


@pytest.mark.asyncio
async def test_exclusions_are_keyed_by_machine_too(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    async with MCPHarness(tmp_path / "s") as h:
        sid = (await _load(h, rig["files"]))["session_id"]
        await h.call("run_qc", session_id=sid)
        await h.call("assign_groups", session_id=sid,
                     mapping={"ctrl": {"M1": [1, 32]}, "mut": {"M2": [1, 32]}})
        beam = await h.call("apply_exclusions", session_id=sid,
                            exclusions=["Monitor1.txt:5"], reason="empty", confirm=True)
        tube = await h.call("apply_exclusions", session_id=sid,
                            exclusions=["M1:5"], reason="empty", confirm=True)
    assert beam.is_error and "food beam of machine 'M1'" in beam.text
    assert not tube.is_error, tube.text
    assert tube.data["n_by_group"] == {"ctrl": 31, "mut": 32}


@pytest.mark.asyncio
async def test_a_file_outside_every_machine_refuses_the_load(rig, corpus_dir, tmp_path,
                                                             monkeypatch):
    """On a declared rig, a file outside every machine would be analysed as
    independent animals beside summed machines — the ambiguity the block removes."""
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    stray = tmp_path / "Monitor9.txt"
    stray.write_text((corpus_dir / "Monitor1.txt").read_text())
    async with MCPHarness(tmp_path / "s") as h:
        r = await h.call("load_experiment", name="rig", paths=rig["files"] + [str(stray)])
    assert r.is_error and "['Monitor9.txt'] is loaded but no machine's beam" in r.text


@pytest.mark.asyncio
async def test_a_machine_loaded_short_refuses(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    files = [f for f in rig["files"] if not f.endswith("Monitor6.txt")]
    async with MCPHarness(tmp_path / "s") as h:
        r = await h.call("load_experiment", name="rig", paths=files)
    assert r.is_error
    assert "Machine 'M2' is loaded without {'plug': 'Monitor6.txt'}" in r.text
    assert "summed zero means the fly crossed no beam anywhere" in r.text


@pytest.mark.asyncio
async def test_a_declared_machine_left_unloaded_is_reported(rig, tmp_path, monkeypatch):
    """A whole machine left out is a legitimate partial load — said, not refused."""
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    async with MCPHarness(tmp_path / "s") as h:
        d = await _load(h, rig["files"][:3])
    assert d["monitor_keys"] == ["M1"]
    assert any("['M2'] were not loaded" in w for w in d["warnings"])


@pytest.mark.asyncio
async def test_the_manifest_lists_files_and_machines(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    async with MCPHarness(tmp_path / "s") as h:
        sid = (await _load(h, rig["files"]))["session_id"]
        text = await h.read_resource(f"dam://session/{sid}/manifest")
    for f in sum(BEAMS.values(), ()):
        assert f"| {f} |" in text                      # every physical file
    assert "| M2 | Monitor4.txt | Monitor5.txt | Monitor6.txt |" in text
    assert "counts tubes" in text


@pytest.mark.asyncio
async def test_window_tradeoff_counts_tubes(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    async with MCPHarness(tmp_path / "s") as h:
        sid = (await _load(h, rig["files"]))["session_id"]
        r = await h.call("window_tradeoff", session_id=sid)
    assert not r.is_error, r.text
    for row in r.data["rows"]:
        assert row["n_alive"] + row["n_died"] + row["n_empty"] + row["n_suspect"] == 64


@pytest.mark.asyncio
async def test_the_confound_warning_names_the_machine(rig, tmp_path, monkeypatch):
    """One group per machine is treatment aliased with rack — HANDOFF-11's second
    defect, named with the unit it is about."""
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    async with MCPHarness(tmp_path / "s") as h:
        sid = (await _load(h, rig["files"]))["session_id"]
        await h.call("run_qc", session_id=sid)
        r = await h.call("assign_groups", session_id=sid,
                         mapping={"ctrl": {"M1": [1, 32]}, "mut": {"M2": [1, 32]}})
    assert any(w.startswith("Treatment is confounded with machine:")
               for w in r.data["warnings"])


@pytest.mark.asyncio
async def test_no_tool_return_carries_a_beam_series(rig, tmp_path, monkeypatch):
    """Summing must not open a path for raw counts to the caller: the summed files
    live in the engine's work area and never appear in a result."""
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(tmp_path, MACHINES))
    async with MCPHarness(tmp_path / "s") as h:
        load = await _load(h, rig["files"])
        sid = load["session_id"]
        qc = (await h.call("run_qc", session_id=sid)).data
    blob = json.dumps([load, qc])
    assert "/machines/" not in blob


# ── through the engine (needs Rtivity-Python) ─────────────────────────────────

@requires_rtivity
@pytest.mark.asyncio
async def test_metrics_compute_on_tubes(rig, tmp_path, monkeypatch):
    """The DataStore reads the summed file through its existing path. Four tubes
    per group keeps the store build short (HANDOFF note in test_happy_path)."""
    monkeypatch.setenv("DAM_PREREG_PATH", _declaration(
        tmp_path, MACHINES, groups="groups:\n  - ctrl\n  - mut\n"))
    async with MCPHarness(tmp_path / "s") as h:
        sid = (await _load(h, rig["files"]))["session_id"]
        await h.call("run_qc", session_id=sid)
        await h.call("assign_groups", session_id=sid,
                     mapping={"ctrl": {"M1": [1, 4]}, "mut": {"M2": [1, 4]}})
        r = await h.call("compute_sleep", session_id=sid)
    assert not r.is_error, r.text
    rows = r.data["summary"]["total_sleep_time_h"]
    assert {row["labels"] for row in rows} == {"ctrl", "mut"}
    assert all(1 <= row["n"] <= 4 for row in rows)
