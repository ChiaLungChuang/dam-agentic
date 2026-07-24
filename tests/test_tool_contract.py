"""The monitor-key and channel-spec contract, pinned over the real protocol.

Why this file exists. A real agent run sent `assign_groups` a monitor key the
tool refused. The finding was not "the model made a mistake": the task prompt
supplies *full paths*, `assign_groups` required *basenames*, and nothing in the
tool surface said which. The model was choosing between two equally-supported
readings of an ambiguous contract — and mostly, but not always, guessing right.

So the contract is now stated at both ends and pinned here:

* `load_experiment` returns `monitor_keys` — the canonical strings to use later,
  present in context rather than inferable.
* `assign_groups` normalises a monitor key to its basename, so a full path is
  accepted rather than refused.
* The accepted channel-spec forms are in the tool description, not only in the
  error text a caller sees after failing.

Keyless: a real server over stdio, no model.
"""

import pytest

pytest.importorskip("mcp")

from evals.harness import MCPHarness

MUT, CTRL = "CG8093_mut", "w1118_ctrl"


async def _session(h, monitor_files):
    r = await h.call("load_experiment", paths=monitor_files, name="contract")
    assert not r.is_error
    return r.data


def _mapping(monitor_key, lo_spec, hi_spec):
    return {MUT: {monitor_key: lo_spec}, CTRL: {monitor_key: hi_spec}}


# ── the loader states the canonical keys ──────────────────────────────────────

@pytest.mark.asyncio
async def test_load_experiment_reports_canonical_monitor_keys(tmp_path, monitor_files):
    """The correct key must be *present*, not inferable. This is what removes the
    coin flip for a model that was handed full paths in its prompt."""
    async with MCPHarness(tmp_path) as h:
        data = await _session(h, monitor_files)
    assert data["monitor_keys"] == ["Monitor1.txt", "Monitor2.txt"]
    # and they agree with the per-monitor summary, so the two cannot drift
    assert [m["file"] for m in data["monitors"]] == data["monitor_keys"]


# ── accepted forms ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("lo_spec,hi_spec,form", [
    ([1, 16], [17, 32], "[lo, hi] inclusive range"),
    ("1-16", "17-32", "range string"),
    (list(range(1, 17)), list(range(17, 33)), "explicit channel list"),
])
async def test_accepted_channel_specs(tmp_path, monitor_files, lo_spec, hi_spec, form):
    async with MCPHarness(tmp_path) as h:
        await _session(h, monitor_files)
        sid = (await h.call("load_experiment", paths=monitor_files,
                            name="spec")).data["session_id"]
        r = await h.call("assign_groups", session_id=sid,
                         mapping=_mapping("Monitor1.txt", lo_spec, hi_spec))
        assert not r.is_error, f"{form} should be accepted: {r.text[:160]}"
        assert r.data["group_sizes"] == {MUT: 16, CTRL: 16}


@pytest.mark.asyncio
async def test_full_path_monitor_key_is_accepted(tmp_path, monitor_files):
    """The ambiguity fix: a caller handed full paths may pass them straight back.
    Basename normalisation accepts that instead of refusing it."""
    async with MCPHarness(tmp_path) as h:
        sid = (await _session(h, monitor_files))["session_id"]
        full = monitor_files[0]                       # absolute path, as prompts carry
        assert "/" in full
        r = await h.call("assign_groups", session_id=sid,
                         mapping=_mapping(full, [1, 16], [17, 32]))
        assert not r.is_error, r.text[:200]
        assert r.data["group_sizes"] == {MUT: 16, CTRL: 16}


# ── rejected forms still refuse, with the actionable text ─────────────────────

@pytest.mark.asyncio
async def test_unknown_monitor_still_refuses_with_the_loaded_files(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = (await _session(h, monitor_files))["session_id"]
        r = await h.call("assign_groups", session_id=sid,
                         mapping=_mapping("Monitor9.txt", [1, 16], [17, 32]))
        assert r.is_error
        assert "is not in this session" in r.text
        assert "Monitor1.txt" in r.text                # enumerates what *is* loaded
        # Pin the pointer added with monitor_keys, not just the pre-existing text:
        # without this the assertion would still pass if the pointer were removed.
        assert "monitor_keys" in r.text


@pytest.mark.asyncio
async def test_nested_dict_channel_spec_refuses_and_names_the_forms(tmp_path, monitor_files):
    async with MCPHarness(tmp_path) as h:
        sid = (await _session(h, monitor_files))["session_id"]
        r = await h.call("assign_groups", session_id=sid,
                         mapping={MUT: {"Monitor1.txt": {"channels": [1, 16]}},
                                  CTRL: {"Monitor1.txt": {"channels": [17, 32]}}})
        assert r.is_error
        assert "Cannot read channel spec" in r.text
        assert "[low, high]" in r.text and "'1-16'" in r.text


@pytest.mark.asyncio
async def test_inverted_mapping_refuses(tmp_path, monitor_files):
    """{monitor: {channels: label}} is the mapping inside out; it must not be
    silently reinterpreted."""
    async with MCPHarness(tmp_path) as h:
        sid = (await _session(h, monitor_files))["session_id"]
        r = await h.call("assign_groups", session_id=sid,
                         mapping={"Monitor1.txt": {"1-16": MUT, "17-32": CTRL}})
        assert r.is_error
        assert "is not in this session" in r.text
