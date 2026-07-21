"""Schema validation. The types encode the boundary — a channel outside 1..32 is
not a channel, and the model should never be handed one."""

import pytest

pytest.importorskip("pydantic")

from pydantic import ValidationError

from dam_mcp.schemas import ChannelFlag, ContrastResult, MetricResult, QCResult


def test_metric_result_rejects_raw_series():
    """#10 — the boundary is a type: a bare numeric array cannot occupy any
    summary field, and a row carrying an array-valued field is rejected too. A
    leak is a ValidationError here, not something a reader has to notice."""
    with pytest.raises(ValidationError):
        MetricResult(session_id="s", metric="leak",
                     summary={"activity_series": [12, 0, 3, 5, 0, 1]},
                     resource_uri="dam://session/s/metrics")
    with pytest.raises(ValidationError):
        MetricResult(session_id="s", metric="leak2",
                     summary={"t": [{"labels": "A", "series": [1, 2, 3]}]},
                     resource_uri="dam://session/s/metrics")


def test_metric_result_accepts_scalar_only_rows():
    m = MetricResult(
        session_id="s", metric="sleep",
        summary={
            "immobility_minutes": 5.0,
            "total_sleep_time_h": [
                {"labels": "A", "x": "TotalSleepTime", "n": 16, "mean": 1.1,
                 "sd": 0.4, "median": 1.0, "sem": 0.1, "q1": 0.8, "q3": 1.4},
            ],
        },
        resource_uri="dam://session/s/metrics",
    )
    assert m.summary["total_sleep_time_h"][0].labels == "A"


def test_channel_flag_rejects_out_of_range():
    with pytest.raises(ValidationError):
        ChannelFlag(monitor="M", channel=33, state="empty", evidence="x")
    with pytest.raises(ValidationError):
        ChannelFlag(monitor="M", channel=0, state="empty", evidence="x")


def test_channel_flag_rejects_unknown_state():
    with pytest.raises(ValidationError):
        ChannelFlag(monitor="M", channel=1, state="zombie", evidence="x")


def test_qc_result_roundtrip():
    qc = QCResult(
        session_id="s", death_hours=24.0,
        tally={"Monitor1.txt": {"alive": 30, "empty": 2}},
        flags=[ChannelFlag(monitor="Monitor1.txt", channel=5, state="empty",
                           evidence="zero throughout")],
        decisions_required=["Monitor1.txt ch5: include?"],
        report_uri="dam://session/s/qc-report",
    )
    dumped = qc.model_dump()
    assert dumped["flags"][0]["channel"] == 5
    assert dumped["report_uri"].endswith("qc-report")


def test_metric_result_carries_resource_uri():
    m = MetricResult(session_id="s", metric="sleep", summary={"total_sleep_time_h": []},
                     resource_uri="dam://session/s/metrics")
    assert m.model_dump()["resource_uri"].endswith("metrics")


def test_contrast_result_shape():
    c = ContrastResult(
        session_id="s", contrast_id="c", metric="total_sleep", phase="dark",
        groups=["A", "B"], test="mann_whitney_u", n={"A": 15, "B": 16},
        median={"A": 1.2, "B": 0.8}, statistic=88.0, p_value=0.03,
        effect_size={"kind": "rank_biserial", "value": 0.27}, exclusions_applied=1,
    )
    assert c.model_dump()["p_value"] == 0.03
