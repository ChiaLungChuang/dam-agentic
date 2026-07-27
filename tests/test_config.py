"""The contrast config is the pre-registration boundary: readable, not writable,
and the model can only pick an id that exists."""

import pytest

pytest.importorskip("yaml")

from dam_mcp import config
from dam_mcp.errors import ToolError


def test_lists_declared_contrasts():
    contrasts = config.list_contrasts()
    assert len(contrasts) >= 1
    for c in contrasts:
        assert {"id", "metric", "phase", "groups"} <= set(c)


def test_get_known_contrast():
    first = config.list_contrasts()[0]
    got = config.get_contrast(first["id"])
    assert got["id"] == first["id"]


def test_unknown_contrast_is_actionable():
    with pytest.raises(ToolError) as exc:
        config.get_contrast("this_was_never_declared")
    assert "only run a contrast" in str(exc.value)


# ── which pre-registration is in effect ───────────────────────────────────────
#
# One server, several experiments (young / middle / old timepoints), each with its
# own pre-registered set. Before this the path was a module constant, so the only
# way to switch sets was to overwrite the file in place — and nothing anywhere
# recorded which set had been live for a given run.


def test_contrasts_path_is_overridable_by_env(tmp_path, monkeypatch):
    other = tmp_path / "contrasts-old.yaml"
    other.write_text(
        "contrasts:\n"
        "  - id: only_in_the_other_file\n"
        "    metric: total_sleep\n"
        "    phase: dark\n"
        "    groups: [a, b]\n"
    )
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(other))
    assert config.config_path() == other
    assert [c["id"] for c in config.list_contrasts()] == ["only_in_the_other_file"]


def test_contrasts_path_falls_back_to_the_repo_file(monkeypatch):
    """Unset and empty both mean 'use the repo default' — an empty env var is what
    a launch spec built from a maybe-None value produces, and silently resolving it
    to Path('') would read every contrast set as missing."""
    monkeypatch.delenv("DAM_CONTRASTS_PATH", raising=False)
    assert config.config_path() == config.DEFAULT_CONFIG_PATH
    monkeypatch.setenv("DAM_CONTRASTS_PATH", "")
    assert config.config_path() == config.DEFAULT_CONFIG_PATH


def test_path_is_resolved_per_call_not_frozen_at_import(tmp_path, monkeypatch):
    """A path captured once at import is indistinguishable from correct in a
    single-experiment process and wrong the moment a second set is used."""
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    for p, cid in ((a, "from_a"), (b, "from_b")):
        p.write_text(
            f"contrasts:\n  - id: {cid}\n    metric: total_sleep\n"
            "    phase: dark\n    groups: [x, y]\n"
        )
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(a))
    assert config.list_contrasts()[0]["id"] == "from_a"
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(b))
    assert config.list_contrasts()[0]["id"] == "from_b"


def test_missing_contrast_file_names_the_path_it_looked_at(tmp_path, monkeypatch):
    """Errors are prompts: with the path configurable, 'no contrast config' is
    useless unless it says which path was consulted."""
    missing = tmp_path / "nope" / "contrasts.yaml"
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(missing))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    assert str(missing) in str(exc.value)


def test_the_repos_own_contrast_file_parses(monkeypatch):
    """The one test that reads the live config/contrasts.yaml. It asserts only that
    the file parses and is structurally well-formed — never what it declares — so a
    lab writing its real pre-registration cannot turn CI red."""
    monkeypatch.delenv("DAM_CONTRASTS_PATH", raising=False)
    contrasts = config.list_contrasts()
    assert len(contrasts) >= 1
    for c in contrasts:
        assert {"id", "metric", "phase", "groups"} <= set(c)
        assert len(c["groups"]) == 2, f"{c['id']}: a contrast compares exactly two"
    ids = [c["id"] for c in contrasts]
    assert len(set(ids)) == len(ids), "duplicate contrast id: get_contrast returns the first"
