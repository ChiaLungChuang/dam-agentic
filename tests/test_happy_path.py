"""The happy path, end to end, over the real protocol.

Every other server test proves a rail: something the server refuses. This one
proves the path a result actually travels — load, QC, groups, all four metric
families, a declared contrast, all four resources, and a written report — through
the stdio server, the way a client sees it.

It exists because nothing did. Line coverage on main @ 5ed6447 showed that no test
had ever taken `run_contrast` past `config.get_contrast` (server.py:650-655), so a
mismatch between the engine's result and `ContrastResult` would have reached a
user first; that the success paths of `compute_activity`, `compute_rhythmicity`
and `compute_survival` had never run through the server; that no resource had ever
been read; and that `render_report` had never rendered a metric or a contrast.

Three choices worth knowing before editing it:

* **4 channels per arm, not 16.** `engine.build_store` costs ~42 s per call at 32
  assigned channels and ~10 s at 8 — it is a row-wise label lookup in the
  engine's `load_from_metadata`, rebuilt on every compute call — and the compute
  itself is ~1 s. Six engine calls at full size would add minutes to every CI test
  job. The path under test does not change with group size.
* **One contrast, not three.** All three declared contrasts take the same code
  path; each costs a full store rebuild.
* **n is bounded, not pinned.** A contrast's n is the arm's channels *for which
  the metric is defined*, so it can fall below the assigned size (HANDOFF-11
  H11-4: latency and bout duration have per-group denominators), and compute does
  not consult QC flags (H11-3). Asserting `n == assigned` would pin a known defect
  as expected behaviour. What must hold is that n never exceeds what was assigned.

The report check at the end runs `unrendered_metric_values` over the payloads the
metrics resource serves: every value the real engine produced must appear in the
document. This test found that it did not (`_render_metric` kept six columns and
dropped the rest); tests/test_report_metrics.py pins the fix shape by shape, and
this pins it on whatever shapes the engine actually emits.
"""

import json

import pytest

pytest.importorskip("mcp")

from conftest import requires_rtivity, unrendered_metric_values
from evals.harness import MCPHarness

MUT, CTRL = "CG8093_mut", "w1118_ctrl"
MAPPING = {MUT: {"Monitor1.txt": [1, 4]}, CTRL: {"Monitor1.txt": [17, 20]}}
ASSIGNED = {MUT: 4, CTRL: 4}
CONTRAST = "mut_vs_ctrl_sleep_night"

# Each family's headline table: the one a reader would quote. Present, and keyed
# by both group labels, or the family did not produce a by-group result.
HEADLINE = {
    "sleep": "total_sleep_time_h",
    "activity": "counts_per_waking_minute_by_phase",
    "rhythmicity": "dominant_period_h",
    "survival": "summary",
}


def _drop_none(obj):
    if isinstance(obj, dict):
        return {k: _drop_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_drop_none(x) for x in obj]
    return obj


@requires_rtivity
@pytest.mark.asyncio
async def test_the_full_pipeline_produces_a_consistent_result(
        tmp_path, monitor_files, monkeypatch):
    # The report lands in <state_dir>/reports by default; an inherited
    # DAM_REPORT_DIR would send it somewhere this test does not look.
    monkeypatch.delenv("DAM_REPORT_DIR", raising=False)

    async with MCPHarness(tmp_path) as h:
        r = await h.call("load_experiment", paths=monitor_files, name="happy")
        assert not r.is_error, r.text
        sid = r.data["session_id"]

        qc = await h.call("run_qc", session_id=sid)
        assert not qc.is_error, qc.text

        r = await h.call("assign_groups", session_id=sid, mapping=MAPPING)
        assert not r.is_error, r.text
        assert r.data["group_sizes"] == ASSIGNED

        # ── all four metric families ──────────────────────────────────────────
        # `not is_error` carries more than it looks: the server validates every
        # summary against MetricResult, whose rows are scalar-only, so a raw
        # series crossing the boundary would surface here as an error.
        summaries = {}
        for family, table in HEADLINE.items():
            r = await h.call(f"compute_{family}", session_id=sid)
            assert not r.is_error, f"compute_{family}: {r.text}"
            assert r.data["metric"] == family
            assert r.data["resource_uri"] == f"dam://session/{sid}/metrics"
            rows = r.data["summary"][table]
            assert {row["labels"] for row in rows} == {MUT, CTRL}, (
                f"compute_{family}.{table} is not by-group: {rows}")
            summaries[family] = r.data["summary"]

        # ── a declared contrast, checked against the server's own declaration ─
        declared = await h.call("list_contrasts", session_id=sid)
        (spec,) = [c for c in declared.data["contrasts"] if c["id"] == CONTRAST]

        c = await h.call("run_contrast", session_id=sid, contrast_id=CONTRAST)
        assert not c.is_error, c.text
        res = c.data
        assert res["contrast_id"] == CONTRAST
        assert res["metric"] == spec["metric"]
        assert res["phase"] == spec["phase"]
        assert res["groups"] == spec["groups"]
        assert res["rationale"] == spec["rationale"]
        assert res["exclusions_applied"] == 0
        assert set(res["n"]) == {MUT, CTRL}
        for label, n in res["n"].items():
            assert 1 <= n <= ASSIGNED[label], (
                f"{label}: n={n} but only {ASSIGNED[label]} channels assigned")
        assert 0.0 <= res["p_value"] <= 1.0

        # ── the resources say what the tools said ─────────────────────────────
        # Q&A is grounded in these, so a resource that disagrees with the tool
        # return is a grounded answer to the wrong number.
        manifest = await h.read_resource(f"dam://session/{sid}/manifest")
        for label, n in ASSIGNED.items():
            assert f"**{label}**: n = {n}" in manifest
        assert "Monitor1.txt" in manifest and "Monitor2.txt" in manifest

        qc_res = json.loads(await h.read_resource(f"dam://session/{sid}/qc-report"))
        assert qc_res[str(qc.data["death_hours"])]["tally"] == qc.data["tally"]

        # Compared without None-valued keys: the tool return passes through
        # SummaryRow, which spells out every unset optional field (id, order,
        # channel, ...) as null, while the resource serves the stored rows as
        # they are. Same values, different padding.
        metrics = json.loads(await h.read_resource(f"dam://session/{sid}/metrics"))
        assert _drop_none(metrics) == _drop_none(summaries)

        contrasts = json.loads(await h.read_resource(f"dam://session/{sid}/contrasts"))
        assert set(contrasts) == {CONTRAST}
        for key in ("n", "statistic", "p_value", "effect_size"):
            assert contrasts[CONTRAST][key] == res[key], key

        # ── the report: preview writes nothing, confirm writes in the root ────
        preview = await h.call("render_report", session_id=sid, path="happy.md")
        assert not preview.is_error, preview.text
        assert preview.data["written"] is False
        dest = tmp_path / "reports" / "happy.md"
        assert preview.data["would_write_to"] == str(dest.resolve())
        assert not dest.exists(), "a preview wrote the file"

        w = await h.call("render_report", session_id=sid, path="happy.md",
                         confirm=True)
        assert not w.is_error, w.text
        assert w.data["written"] is True
        assert w.data["path"] == str(dest.resolve())

    text = dest.read_text()
    assert "_No metrics computed._" not in text
    assert "_No contrasts run._" not in text
    for family in HEADLINE:
        assert f"### {family}" in text, f"report has no {family} section"
    assert unrendered_metric_values(metrics, text) == []
    # The contrast row a reader would copy out: the same p and n the tool returned.
    n_str = ", ".join(f"{k}={v}" for k, v in res["n"].items())
    (row,) = [ln for ln in text.splitlines() if ln.startswith(f"| {CONTRAST} |")]
    assert f"| {res['p_value']:.3g} |" in row
    assert row.endswith(f"| {n_str} |")
