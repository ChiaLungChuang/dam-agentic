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


def _write(path, *, experiment, cid="c1", phase="dark", metric="total_sleep",
           groups=("a", "b"), contrast_groups=None):
    """A declaration file. groups: is the authoritative list of legal labels;
    contrasts: is optional and its labels must be a subset of groups:."""
    body = f"experiment: {experiment}\ngroups: [{', '.join(groups)}]\n"
    if cid is not None:
        cg = list(contrast_groups if contrast_groups is not None else groups[:2])
        body += (f"contrasts:\n  - id: {cid}\n    metric: {metric}\n"
                 f"    phase: {phase}\n    groups: [{', '.join(cg)}]\n")
    path.write_text(body)
    return path


def _write_groups_only(path, *, experiment, groups=("a", "b")):
    path.write_text(f"experiment: {experiment}\ngroups: [{', '.join(groups)}]\n")
    return path


# ── which pre-registration is in effect ───────────────────────────────────────
#
# One server, several experiments (young / middle / old timepoints), each with its
# own pre-registered set. Selected per run by DAM_PREREG_PATH.


def test_contrasts_path_is_set_by_env(tmp_path, monkeypatch):
    other = _write(tmp_path / "contrasts-old.yaml", experiment="old",
                   cid="only_in_the_other_file")
    monkeypatch.setenv("DAM_PREREG_PATH", str(other))
    assert config.config_path() == other
    assert [c["id"] for c in config.list_contrasts()] == ["only_in_the_other_file"]


def test_unset_contrasts_path_refuses_rather_than_defaulting(monkeypatch):
    """There is deliberately no default. config/contrasts.yaml is a TEMPLATE with
    placeholder labels; silently loading it would let an unregistered comparison
    look registered, which is the one failure this gate exists to prevent. Better
    to break loudly once at setup."""
    monkeypatch.delenv("DAM_PREREG_PATH", raising=False)
    with pytest.raises(ToolError) as exc:
        config.config_path()
    assert "DAM_PREREG_PATH" in str(exc.value)
    assert "template" in str(exc.value).lower()


def test_empty_contrasts_path_refuses_too(monkeypatch):
    """An empty value is what a launch spec built from a maybe-None produces. It
    must refuse like unset, not resolve to Path('')."""
    monkeypatch.setenv("DAM_PREREG_PATH", "")
    with pytest.raises(ToolError):
        config.config_path()


def test_path_is_resolved_per_call_not_frozen_at_import(tmp_path, monkeypatch):
    """A path captured once at import is indistinguishable from correct in a
    single-experiment process and wrong the moment a second set is used."""
    a = _write(tmp_path / "contrasts-alpha.yaml", experiment="alpha", cid="from_a")
    b = _write(tmp_path / "contrasts-beta.yaml", experiment="beta", cid="from_b")
    monkeypatch.setenv("DAM_PREREG_PATH", str(a))
    assert config.list_contrasts()[0]["id"] == "from_a"
    monkeypatch.setenv("DAM_PREREG_PATH", str(b))
    assert config.list_contrasts()[0]["id"] == "from_b"


def test_missing_contrast_file_names_the_path_it_looked_at(tmp_path, monkeypatch):
    """Errors are prompts: with the path configurable, 'no contrast config' is
    useless unless it says which path was consulted."""
    missing = tmp_path / "nope" / "contrasts-x.yaml"
    monkeypatch.setenv("DAM_PREREG_PATH", str(missing))
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
    monkeypatch.setenv("DAM_PREREG_PATH", str(bad))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    msg = str(exc.value)
    assert "geneA-old-2026" in msg and "contrasts-young" in msg


def test_matching_experiment_and_filename_loads(tmp_path, monkeypatch):
    ok = _write(tmp_path / "contrasts-geneA-old-2026.yaml", experiment="geneA-old-2026")
    monkeypatch.setenv("DAM_PREREG_PATH", str(ok))
    assert len(config.list_contrasts()) == 1


def test_missing_experiment_field_refuses(tmp_path, monkeypatch):
    """experiment: was unparsed before this. It has to be required now, or the
    filename check has nothing to check against."""
    p = tmp_path / "contrasts-x.yaml"
    p.write_text("contrasts:\n  - id: c\n    metric: total_sleep\n"
                 "    phase: dark\n    groups: [a, b]\n")
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    assert "experiment" in str(exc.value)


def test_the_template_refuses_to_load(monkeypatch):
    """config/contrasts.yaml is a template: experiment: TEMPLATE_replace_me does
    not appear in the stem 'contrasts', so it cannot be loaded even if someone
    points at it deliberately. Running the template is never correct."""
    monkeypatch.setenv("DAM_PREREG_PATH", str(config.TEMPLATE_PATH))
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
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    assert "phase" in str(exc.value).lower()
    assert "light" in str(exc.value) and "dark" in str(exc.value)


@pytest.mark.parametrize("phase", ["light", "dark"])
def test_declared_phases_are_accepted(tmp_path, monkeypatch, phase):
    p = _write(tmp_path / "contrasts-p.yaml", experiment="p", phase=phase)
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    assert config.list_contrasts()[0]["phase"] == phase


def test_unknown_metric_is_rejected_at_load_not_at_run(tmp_path, monkeypatch):
    """Same class of defect one field over: a bad metric used to surface only when
    that specific contrast was executed, so a typo in contrast #12 stayed invisible
    through eleven successful runs."""
    p = _write(tmp_path / "contrasts-m.yaml", experiment="m", metric="total_slep")
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
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


# ── groups: is authoritative; contrasts: is optional ──────────────────────────
#
# Reversal, recorded in HANDOFF-9. The label check used to live on the contrast
# set: assign_groups refused unless every label the contrasts named was assigned.
# That gated grouping on declared *tests*, and this workflow has no in-tool
# statistics step — metrics are exported and tested in Prism — so it gated a path
# the work does not travel. The check moved onto groups:. It did not become
# optional: an undeclared label is still refused, one layer earlier.


def test_groups_only_file_is_valid_and_loads(tmp_path, monkeypatch):
    """The point of the change. A file that declares the design and no tests is a
    complete, legal declaration."""
    p = _write_groups_only(tmp_path / "contrasts-designonly.yaml",
                           experiment="designonly", groups=("mut", "ctrl"))
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    assert config.declared_groups() == {"mut", "ctrl"}
    assert config.list_contrasts() == []


def test_list_contrasts_on_a_contrast_free_file_is_empty_not_an_error(
        tmp_path, monkeypatch):
    p = _write_groups_only(tmp_path / "contrasts-designonly.yaml",
                           experiment="designonly")
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    assert config.list_contrasts() == []                    # no raise


def test_run_contrast_still_refuses_an_undeclared_id(tmp_path, monkeypatch):
    """Contrasts being optional must not make run_contrast permissive."""
    p = _write_groups_only(tmp_path / "contrasts-designonly.yaml",
                           experiment="designonly")
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    with pytest.raises(ToolError) as exc:
        config.get_contrast("anything_at_all")
    assert "declares no contrasts" in str(exc.value)


def test_groups_accepts_a_mapping_as_well_as_a_list(tmp_path, monkeypatch):
    """Both shapes exist in the repo — the template lists labels, the older stub
    maps label -> {monitor: range}. Keys are the labels either way, so this is
    deterministic rather than a guess. Mapping values are documentary and never
    read; channel ranges reach the server through assign_groups."""
    p = tmp_path / "contrasts-mapped.yaml"
    p.write_text(
        "experiment: mapped\n"
        "groups:\n"
        "  mut:\n    Monitor1.txt: [1, 16]\n"
        "  ctrl:\n    Monitor1.txt: [17, 32]\n"
    )
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    assert config.declared_groups() == {"mut", "ctrl"}


def test_mapping_values_under_groups_are_warned_about(tmp_path, monkeypatch):
    """Documentary-but-silent is the shape of the phase-fallback defect this repo
    already found. Someone will write channel ranges here and assume they applied,
    because there is nothing to tell them otherwise. The values are still ignored —
    they are just no longer ignored quietly."""
    p = tmp_path / "contrasts-mapped.yaml"
    p.write_text(
        "experiment: mapped\n"
        "groups:\n"
        "  mut:\n    Monitor1.txt: [1, 16]\n"
        "  ctrl:\n    Monitor1.txt: [17, 32]\n"
    )
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    (warn,) = config.declaration_warnings()
    assert "mut" in warn and "ctrl" in warn
    assert "NOT read" in warn
    assert "assign_groups" in warn


def test_a_bare_mapping_with_no_values_is_not_warned_about(tmp_path, monkeypatch):
    """`mut:` with nothing under it carries no ignored value, so there is nothing
    to warn about. Warning anyway would train the reader to ignore warnings."""
    p = tmp_path / "contrasts-bare.yaml"
    p.write_text("experiment: bare\ngroups:\n  mut:\n  ctrl:\n")
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    assert config.declaration_warnings() == []


def test_a_list_of_labels_is_not_warned_about(tmp_path, monkeypatch):
    p = _write_groups_only(tmp_path / "contrasts-list.yaml", experiment="list")
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    assert config.declaration_warnings() == []


def test_contrast_labels_must_be_a_subset_of_declared_groups(tmp_path, monkeypatch):
    """The reason for subset rather than union. Under the old union rule a typo'd
    contrast label quietly became a new legal group; here it is caught at load."""
    p = _write(tmp_path / "contrasts-sub.yaml", experiment="sub",
               groups=("mut", "ctrl"), contrast_groups=("mut", "crtl"))
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    msg = str(exc.value)
    assert "crtl" in msg and "mut" in msg and "ctrl" in msg


def test_contrast_labels_inside_declared_groups_load(tmp_path, monkeypatch):
    p = _write(tmp_path / "contrasts-ok.yaml", experiment="ok",
               groups=("mut", "ctrl", "extra"), contrast_groups=("mut", "ctrl"))
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    assert len(config.list_contrasts()) == 1
    assert config.declared_groups() == {"mut", "ctrl", "extra"}


def test_missing_groups_with_contrasts_present_refuses(tmp_path, monkeypatch):
    """The compatibility decision, made explicitly: REFUSE, do not derive.

    Deriving groups: from the contrast labels is precisely the union rule this
    change removes — a typo'd contrast label would silently become a legal group,
    and the file's meaning would depend on whether a key happened to be present.
    One loader with two silent semantics is worse than one loud refusal."""
    p = tmp_path / "contrasts-legacy.yaml"
    p.write_text(
        "experiment: legacy\n"
        "contrasts:\n  - id: c\n    metric: total_sleep\n"
        "    phase: dark\n    groups: [mut, ctrl]\n"
    )
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    with pytest.raises(ToolError) as exc:
        config.list_contrasts()
    msg = str(exc.value)
    assert "groups:" in msg
    assert "mut" in msg and "ctrl" in msg      # names what to declare


def test_missing_groups_without_contrasts_refuses_too(tmp_path, monkeypatch):
    p = tmp_path / "contrasts-empty.yaml"
    p.write_text("experiment: empty\n")
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    with pytest.raises(ToolError) as exc:
        config.declared_groups()
    assert "groups:" in str(exc.value)


@pytest.mark.parametrize("bad", ["groups: []\n", "groups: {}\n", "groups: 3\n",
                                 "groups: [a, '']\n"])
def test_malformed_groups_are_refused(tmp_path, monkeypatch, bad):
    p = tmp_path / "contrasts-bad.yaml"
    p.write_text("experiment: bad\n" + bad)
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    with pytest.raises(ToolError):
        config.declared_groups()


# ── the gate cannot be skipped by an unreadable config ────────────────────────

def test_group_label_check_propagates_a_missing_config(monkeypatch):
    """It used to swallow a ToolError so an unreadable config never blocked
    assignment. With no default that is a hole: unset means 'nothing is declared',
    and proceeding would assign groups against no declaration at all."""
    pytest.importorskip("mcp")
    from dam_mcp import server
    monkeypatch.delenv("DAM_PREREG_PATH", raising=False)
    with pytest.raises(ToolError):
        server._check_group_labels({"whatever"})


def test_assigned_label_not_declared_is_refused(tmp_path, monkeypatch):
    """The check moved, it did not go away. An undeclared label is still refused —
    now at the point the human names it, which is where the typo is."""
    pytest.importorskip("mcp")
    from dam_mcp import server
    p = _write_groups_only(tmp_path / "contrasts-d.yaml", experiment="d",
                           groups=("mut", "ctrl"))
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    with pytest.raises(ToolError) as exc:
        server._check_group_labels({"mut", "crtl"})
    assert "crtl" in str(exc.value)


def test_assigning_a_subset_of_declared_groups_is_allowed(tmp_path, monkeypatch):
    """Loading one monitor of a four-group design is legitimate, so a partial
    assignment must not refuse. run_contrast still errors clearly if a contrast
    names an arm with no animals."""
    pytest.importorskip("mcp")
    from dam_mcp import server
    p = _write_groups_only(tmp_path / "contrasts-d.yaml", experiment="d",
                           groups=("a", "b", "c", "d"))
    monkeypatch.setenv("DAM_PREREG_PATH", str(p))
    server._check_group_labels({"a", "b"})                  # no raise
