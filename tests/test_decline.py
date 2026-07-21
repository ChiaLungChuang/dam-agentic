"""The gradual-decline detector — the adversarial case for the capability it adds.

A fly that tapers toward death without a clean zero run used to pass silently as
`alive` (measured 30/30). The acceptance is not that the detector dates the death —
there is no fact of the matter — but that it *surfaces* the channel as `suspect` for
a human. The load-bearing risk is the opposite error: flagging a healthy fly at the
light-dark transition, which a naive method does every time. Both are asserted here.
"""

import importlib.util
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_QC = ROOT / "skills" / "dam-qc" / "scripts" / "validate_dam.py"
_spec = importlib.util.spec_from_file_location("validate_dam", _QC)
vd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vd)

N_BINS = 3 * 24 * 60          # 3 days, 1-min bins
_START = datetime(2026, 3, 2, 9, 0, 0)


def _circadian(rng):
    counts = []
    for i in range(N_BINS):
        t = _START + timedelta(minutes=i)
        light = 9 <= t.hour < 21
        peak = t.hour in (9, 10, 19, 20)
        counts.append(rng.randint(0, 14 if peak else 7) if light else rng.randint(0, 2))
    return counts


def _declining(rng, onset_frac=0.4):
    onset = int(N_BINS * onset_frac)
    counts = _circadian(rng)
    for i in range(onset, N_BINS):
        frac = 1.0 - (i - onset) / (N_BINS - onset)
        counts[i] = 1 if rng.random() < frac * 0.15 else 0
    return counts


def _build_mon():
    rng = random.Random(7)
    channels = []
    for ch in range(vd.N_CHANNELS):
        if ch == 0:
            channels.append(_declining(rng))     # the taper
        elif ch == 1:
            channels.append([0] * N_BINS)         # empty tube
        else:
            channels.append(_circadian(rng))      # healthy
    rows = [
        {"index": i, "ts": _START + timedelta(minutes=i), "status": 1, "light":
         1 if 9 <= (_START + timedelta(minutes=i)).hour < 21 else 0,
         "counts": [channels[ch][i] for ch in range(vd.N_CHANNELS)]}
        for i in range(N_BINS)
    ]
    return {"path": "Monitor1.txt", "rows": rows}


def test_declining_channel_lands_in_suspect():
    chans = vd.classify_channels(_build_mon(), death_bins=24 * 60)
    decliner = next(c for c in chans if c["channel"] == 1)   # ch index 0 -> channel 1
    assert decliner["state"] == "suspect"
    assert "decline" in decliner["reason"].lower()
    assert decliner["decline_ratio"] < vd.DECLINE_RATIO


def test_healthy_channels_are_not_flagged_as_decline():
    chans = vd.classify_channels(_build_mon(), death_bins=24 * 60)
    for c in chans:
        if c["channel"] in (1, 2):                # the decliner and the empty tube
            continue
        assert c["state"] == "alive", f"healthy channel {c['channel']} misflagged"


def test_ratio_separates_decline_from_health():
    mon = _build_mon()
    assert vd.decline_ratio(mon["rows"], 0) < vd.DECLINE_RATIO      # taper
    assert vd.decline_ratio(mon["rows"], 5) > vd.DECLINE_RATIO      # healthy ~1.0


def test_short_run_is_not_judged():
    """Below DECLINE_MIN_RUN_H there is no baseline-vs-trailing separation, so the
    check declines to guess rather than risk a false positive."""
    mon = _build_mon()
    short = {"rows": mon["rows"][: 30 * 60]}       # 30 h < 48 h
    assert vd.decline_ratio(short["rows"], 0) is None
