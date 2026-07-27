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


def _write(path, *, experiment, cid="c1", phase="dark", metric="total_sleep"):
    path.write_text(
        f"experiment: {experiment}\n"
        f"contrasts:\n  - id: {cid}\n    metric: {metric}\n"
        f"    phase: {phase}\n    groups: [a, b]\n"
    )
    return path


# ── which pre-registration is in effect ───────────────────────────────────────
#
# One server, several experiments (young / middle / old timepoints), each with its
# own pre-registered set. Selected per run by DAM_CONTRASTS_PATH.


def test_contrasts_path_is_set_by_env(tmp_path, monkeypatch):
    other = _write(tmp_path / "contrasts-old.yaml", experiment="old",
                   cid="only_in_the_other_file")
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(other))
    assert config.config_path() == other
    assert [c["id"] for c in config.list_contrasts()] == ["only_in_the_other_file"]


def test_unset_contrasts_path_refuses_rather_than_defaulting(monkeypatch):
    """There is deliberately no default. config/contrasts.yaml is a TEMPLATE with
    placeholder labels; silently loading it would let an unregistered comparison
    look registered, which is the one failure this gate exists to prevent. Better
    to break loudly once at setup."""
    monkeypatch.delenv("DAM_CONTRASTS_PATH", raising=False)
    with pytest.raises(ToolError) as exc:
        config.config_path()
    assert "DAM_CONTRASTS_PATH" in str(exc.value)
    assert "template" in str(exc.value).lower()


def test_empty_contrasts_path_refuses_too(monkeypatch):
    """An empty value is what a launch spec built from a maybe-None produces. It
    must refuse like unset, not resolve to Path('')."""
    monkeypatch.setenv("DAM_CONTRASTS_PATH", "")
    with pytest.raises(ToolError):
        config.config_path()


def test_path_is_resolved_per_call_not_frozen_at_import(tmp_path, monkeypatch):
    """A path captured once at import is indistinguishable from correct in a
    single-experiment process and wrong the moment a second set is used."""
    a = _write(tmp_path / "contrasts-alpha.yaml", experiment="alpha", cid="from_a")
    b = _write(tmp_path / "contrasts-beta.yaml", experiment="beta", cid="from_b")
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(a))
    assert config.list_contrasts()[0]["id"] == "from_a"
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(b))
    assert config.list_contrasts()[0]["id"] == "from_b"


def test_missing_contrast_file_names_the_path_it_looked_at(tmp_path, monkeypatch):
    """Errors are prompts: with the path configurable, 'no contrast config' is
    useless unless it says which path was consulted."""
    missing = tmp_path / "nope" / "contrasts-x.yaml"
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(missing))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    assert str(missing) in str(exc.value)


# ── filename must agree with experiment: ──────────────────────────────────────
#
# The per-file layout's whole value is that the commit introducing a file is that
# experiment's pre-registration timestamp. A file named -young declaring -old
# destroys that silently, and the proposed workflow is literally "copy a file and
# edit it", which is exactly how that happens.


def test_experiment_must_appear_in_the_filename(tmp_path, monkeypatch):
    bad = _write(tmp_path / "contrasts-young.yaml", experiment="geneA-old-2026")
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(bad))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    msg = str(exc.value)
    assert "geneA-old-2026" in msg and "contrasts-young" in msg


def test_matching_experiment_and_filename_loads(tmp_path, monkeypatch):
    ok = _write(tmp_path / "contrasts-geneA-old-2026.yaml", experiment="geneA-old-2026")
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(ok))
    assert len(config.list_contrasts()) == 1


def test_missing_experiment_field_refuses(tmp_path, monkeypatch):
    """experiment: was unparsed before this. It has to be required now, or the
    filename check has nothing to check against."""
    p = tmp_path / "contrasts-x.yaml"
    p.write_text("contrasts:\n  - id: c\n    metric: total_sleep\n"
                 "    phase: dark\n    groups: [a, b]\n")
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(p))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    assert "experiment" in str(exc.value)


def test_the_template_refuses_to_load(monkeypatch):
    """config/contrasts.yaml is a template: experiment: TEMPLATE_replace_me does
    not appear in the stem 'contrasts', so it cannot be loaded even if someone
    points at it deliberately. Running the template is never correct."""
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(config.TEMPLATE_PATH))
    with pytest.raises(ToolError):
        config.list_contrasts()


def test_the_template_is_structurally_wellformed():
    """The template is still checked for shape — just not through load_config,
    which now refuses it by design. Read it directly."""
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(config.TEMPLATE_PATH.read_text())
    contrasts = data["contrasts"]
    assert len(contrasts) >= 1
    for c in contrasts:
        assert {"id", "metric", "phase", "groups"} <= set(c)
        assert len(c["groups"]) == 2
        assert c["phase"] in config.PHASES
    ids = [c["id"] for c in contrasts]
    assert len(set(ids)) == len(ids)


# ── phase is a closed vocabulary ──────────────────────────────────────────────
#
# The engine resolved anything not in (light, l, day) to Dark. A typo therefore
# returned a clean-looking result for the wrong phase, with nothing anomalous to
# notice — combined with a template that loaded by default, that is a path to a
# confident wrong number.


@pytest.mark.parametrize("phase", ["dusk", "ligth", "DARKNESS", "night", ""])
def test_unknown_phase_is_rejected(tmp_path, monkeypatch, phase):
    p = _write(tmp_path / "contrasts-p.yaml", experiment="p", phase=phase or "''")
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(p))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    assert "phase" in str(exc.value).lower()
    assert "light" in str(exc.value) and "dark" in str(exc.value)


@pytest.mark.parametrize("phase", ["light", "dark"])
def test_declared_phases_are_accepted(tmp_path, monkeypatch, phase):
    p = _write(tmp_path / "contrasts-p.yaml", experiment="p", phase=phase)
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(p))
    assert config.list_contrasts()[0]["phase"] == phase


def test_unknown_metric_is_rejected_at_load_not_at_run(tmp_path, monkeypatch):
    """Same class of defect one field over: a bad metric used to surface only when
    that specific contrast was executed, so a typo in contrast #12 stayed invisible
    through eleven successful runs."""
    p = _write(tmp_path / "contrasts-m.yaml", experiment="m", metric="total_slep")
    monkeypatch.setenv("DAM_CONTRASTS_PATH", str(p))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    assert "total_slep" in str(exc.value)


@pytest.mark.parametrize("phase,expected", [
    ("light", "Light"), ("Light", "Light"), ("l", "Light"), ("day", "Light"),
    ("dark", "Dark"), ("DARK", "Dark"), ("d", "Dark"), ("night", "Dark"),
])
def test_engine_phase_label_maps_the_known_vocabulary(phase, expected):
    """The same rule at the point of use. config.py rejects unknown phases at load;
    the engine refuses them too, so a contrast dict arriving by any other route
    cannot fall through. No engine install needed — this is string logic."""
    from dam_mcp import engine
    assert engine._phase_label(phase) == expected


@pytest.mark.parametrize("phase", ["dusk", "ligth", "", "twilight", None])
def test_engine_phase_label_refuses_the_rest(phase):
    """This is the line that used to be `else: return "Dark"`. It swallowed every
    typo and every unmodelled phase into a well-formed number for the wrong half
    of the day."""
    from dam_mcp import engine
    with pytest.raises(ToolError):
        engine._phase_label(phase)


# ── the gate cannot be skipped by an unreadable config ────────────────────────

def test_group_label_check_propagates_a_missing_config(monkeypatch):
    """_check_contrast_labels used to swallow a ToolError so an unreadable config
    never blocked assignment. With no default that is a hole: unset means 'no
    pre-registration is in effect', and proceeding would assign groups outside any
    declared set."""
    pytest.importorskip("mcp")
    from dam_mcp import server
    monkeypatch.delenv("DAM_CONTRASTS_PATH", raising=False)
    with pytest.raises(ToolError):
        server._check_contrast_labels({"whatever"})
