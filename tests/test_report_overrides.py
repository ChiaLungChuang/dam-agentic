"""The declared-n override in the rendered report (H13-1).

A report that looks clean while resting on a suppressed refusal is the failure
this project exists to prevent, so what is pinned here is not "the override is
mentioned somewhere" but *where* it appears relative to the numbers it qualifies.
A footnote below a contrast table is indistinguishable from silence to a reader
who stops at the table.
"""

from __future__ import annotations

from dam_mcp.report import render_manifest, render_report
from dam_mcp.sessions import Session

OVERRIDE = {
    "group": "control_EtOH", "declared_n": 32, "computed_n": 96,
    "reason": "three detector boards, one rack of tubes",
    "confirmed": True, "at": "2026-08-03T12:00:00+00:00",
}


def _session(*, overrides) -> Session:
    s = Session(session_id="dam-test", name="topology", created_at="2026-08-03")
    s.monitors = [{"file": "Monitor1.txt", "path": "/d/Monitor1.txt", "n_reads": 10,
                   "n_channels": 32, "first_ts": "a", "last_ts": "b",
                   "bin_seconds": 60}]
    s.groups = [{"monitor": "Monitor1.txt", "channel": c,
                 "labels": "control_EtOH", "order": 1} for c in range(1, 33)]
    s.n_overrides = list(overrides)
    return s


def test_the_banner_appears_before_any_n():
    """Position is the assertion. The banner has to be upstream of the group sizes
    in the document, not merely present in it."""
    text = render_manifest(_session(overrides=[OVERRIDE]))
    assert "DECLARED-n OVERRIDE IN FORCE" in text
    assert text.index("DECLARED-n OVERRIDE") < text.index("## Groups")
    assert text.index("DECLARED-n OVERRIDE") < text.index("## Files")


def test_the_banner_carries_both_numbers_and_the_stated_reason():
    """Declared, used, and why. Any one of the three missing leaves the reader
    unable to judge the decision — which is the only thing the banner is for."""
    text = render_manifest(_session(overrides=[OVERRIDE]))
    assert "32" in text and "96" in text
    assert "three detector boards, one rack of tubes" in text
    assert "control_EtOH" in text


def test_the_overridden_group_is_flagged_at_its_own_n_too():
    """Belt and braces: a reader who jumps straight to the group list still sees
    that this particular n is not the pre-registered one."""
    text = render_manifest(_session(overrides=[OVERRIDE]))
    line = next(ln for ln in text.splitlines() if ln.startswith("- **control_EtOH**"))
    assert "OVERRIDDEN" in line


def test_a_clean_session_gets_no_banner_and_no_placeholder():
    """No override, no region. A permanent 'no overrides' line would train the
    reader to skip exactly where the banner will one day be."""
    text = render_manifest(_session(overrides=[]))
    assert "OVERRIDE" not in text
    assert "n = 32" in text                       # the report still renders


def test_the_full_report_carries_the_banner_above_everything():
    """render_report composes the manifest, so the banner has to survive into the
    document a reader is actually handed."""
    text = render_report(_session(overrides=[OVERRIDE]))
    assert "DECLARED-n OVERRIDE IN FORCE" in text
    for section in ("## Files", "## Groups", "## QC", "## Metrics", "## Contrasts"):
        assert text.index("DECLARED-n OVERRIDE") < text.index(section)


def test_two_overridden_groups_are_both_listed():
    second = dict(OVERRIDE, group="treated_RU", declared_n=32, computed_n=96)
    text = render_manifest(_session(overrides=[OVERRIDE, second]))
    assert "control_EtOH" in text and "treated_RU" in text


def test_a_session_predating_the_field_still_renders():
    """Sessions on disk from before this change deserialise without n_overrides
    only because the dataclass defaults it. Pinned so the default is not dropped:
    a report that raises on an old session is a worse failure than a missing
    banner."""
    s = _session(overrides=[])
    del s.n_overrides                              # simulate the attribute absent
    s.n_overrides = []                             # dataclass default restores it
    assert "OVERRIDE" not in render_report(s)
