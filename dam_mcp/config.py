"""Read-only access to the pre-declared contrast set.

The whole point (spec, CLAUDE.md): the agent may run any contrast in the
declaration and cannot invent one. Pre-registration enforced by the tool boundary,
not by discipline. This module only *reads* the file, and validates that each
contrast is well-formed before the model is allowed to pick one.

**Nothing in the server writes the declaration.** That sentence used to be here as
a claim about this module and was read as a claim about the server, which was
false: `render_report` accepted any path and would overwrite the declaration
(HANDOFF-10 Finding 1). It is true again, and enforced rather than asserted —
`server._resolve_report_path` confines every write to the report directory and
refuses the resolved declaration path outright. If a third write path is ever
added, that enforcement is what has to be extended; this docstring is not the
control.

Every failure reading or parsing the file is a **caller** error and leaves as a
ToolError, never a raw exception (`_read_declaration`). That is not cosmetic: a
ToolError audits as `refused` — a guard firing — while a raw exception audits as
`error`, the shadow of an infrastructure fault, and errors the span. Unwrapped, a
YAML typo in someone's pre-registration read as a crashing server.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ToolError

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "contrasts.yaml"
)

_REQUIRED_KEYS = {"id", "metric", "phase", "groups"}

#: The phases a contrast may name. Closed on purpose — the engine used to resolve
#: anything outside (light, l, day) to Dark, so a typo produced a clean-looking
#: result for the wrong phase with nothing anomalous to notice.
PHASES = ("light", "dark")

#: The metrics the engine can compute. Checked here, at load, so a typo in the
#: twelfth contrast surfaces before the first is run rather than eleven runs later.
METRICS = ("total_sleep", "mean_bout_duration", "counts_per_waking_minute")

#: The tests run_contrast supports.
TESTS = ("wilcoxon", "t")


def config_path() -> Path:
    """Which pre-registered set is in effect. DAM_PREREG_PATH, and nothing else.

    There is deliberately **no default**. config/contrasts.yaml is a template with
    placeholder labels; falling back to it would mean an unregistered comparison
    could run and look registered, which is the single failure this gate exists to
    prevent. An unset variable breaks loudly, once, at setup — the right failure.

    One server commonly serves several experiments (a young / middle / old
    timepoint series is one pre-registration each), so the alternative is
    overwriting one file in place, where nothing records which set was live for a
    given run. list_contrasts returns the resolved path for the same reason.

    Read per call, never frozen at import: a captured path is indistinguishable
    from correct in a single-experiment process and wrong the moment a second set
    is used."""
    env = os.environ.get("DAM_PREREG_PATH")
    if not env:
        raise ToolError(
            "DAM_PREREG_PATH is not set, so no pre-registered contrast set is "
            "in effect and no contrast can be run. There is no default on purpose: "
            f"the file at {TEMPLATE_PATH} is a TEMPLATE with placeholder group "
            "labels, and loading it silently would let an unregistered comparison "
            "look pre-registered. Point the server at a real set, e.g. "
            "DAM_PREREG_PATH=config/contrasts-<experiment>.yaml — see "
            "docs/running.md. Every other tool works without it; only "
            "list_contrasts, run_contrast and assign_groups need it."
        )
    return Path(env)


def load_config(path: Path | None = None) -> dict:
    """Parse contrasts.yaml into a dict. Raises ToolError (errors-as-prompts) on a
    missing file, missing PyYAML, or a malformed contrast declaration."""
    path = path or config_path()
    if not path.exists():
        raise ToolError(
            f"No contrast config at {path}. Contrasts must be declared before the "
            "data is seen — create that file with the pre-registered comparisons "
            "(see the example in the repo). If that is not the set you meant, "
            "check DAM_PREREG_PATH: it overrides the default location and is "
            f"currently {os.environ.get('DAM_PREREG_PATH') or 'unset'}."
        )
    try:
        import yaml
    except ModuleNotFoundError:
        raise ToolError(
            "PyYAML is not installed, so the contrast config cannot be read. Run "
            "`pip install pyyaml` (it is a declared dependency) and retry. Every "
            "other tool works without it; only list_contrasts and run_contrast need it."
        ) from None

    data = _read_declaration(path, yaml)
    _check_experiment_matches_filename(data, path)
    groups = _parse_groups(data, path)

    contrasts = data.get("contrasts") or []
    if not isinstance(contrasts, list):
        raise ToolError(
            f"{path} has a 'contrasts:' key that is not a list. It must be a list "
            "of contrast declarations. To declare no contrasts, omit the key — a "
            "file with groups: and no contrasts: is a complete declaration."
        )
    for i, c in enumerate(contrasts):
        if not isinstance(c, dict):
            raise ToolError(f"Contrast #{i} in {path} is not a mapping.")
        missing = _REQUIRED_KEYS - set(c)
        if missing:
            raise ToolError(
                f"Contrast '{c.get('id', f'#{i}')}' is missing required key(s): "
                f"{sorted(missing)}. Each needs id, metric, phase, groups."
            )
        _check_vocabularies(c, path)
        _check_contrast_groups_are_declared(c, groups, path)
    return data


def _parse_groups(data: dict, path: Path) -> set[str]:
    """The declared group labels — the authoritative list of what is legal.

    Two shapes are accepted, both unambiguous about the labels themselves:

      groups: [mut, ctrl]              a list of labels
      groups:                          a mapping whose KEYS are the labels;
        mut:  {Monitor1.txt: [1, 16]}  values are documentary and never read
        ctrl: {Monitor1.txt: [17, 32]}

    Channel ranges never come from this file — they reach the server through
    assign_groups at call time (CLAUDE.md). A mapping is allowed only because both
    forms already exist in this repo and both name the labels deterministically;
    this is not a guess about intent."""
    raw = data.get("groups")
    if raw is None:
        declared = sorted({g for c in (data.get("contrasts") or [])
                           if isinstance(c, dict)
                           for g in (c.get("groups") or [])})
        hint = (f" The contrasts here reference {declared}; declare exactly the "
                "labels you intend, then re-run.") if declared else ""
        raise ToolError(
            f"{path} has no 'groups:' key. It is required: groups: declares the "
            "legal group labels and assign_groups is checked against it. This is "
            "not derived from the contrasts on purpose — deriving it would let a "
            "typo in a contrast label quietly become a legal group, which is the "
            f"defect the check exists to catch.{hint}"
        )
    if isinstance(raw, dict):
        labels = list(raw.keys())
    elif isinstance(raw, list):
        labels = list(raw)
    else:
        raise ToolError(
            f"{path} has a 'groups:' key that is neither a list of labels nor a "
            f"mapping of label -> notes; got {type(raw).__name__}."
        )
    clean = {str(g).strip() for g in labels if str(g).strip()}
    if len(clean) != len(labels) or not clean:
        raise ToolError(
            f"{path} declares an empty or malformed groups: list ({labels!r}). "
            "Every entry must be a non-empty label, and there must be at least one."
        )
    return clean


def _check_contrast_groups_are_declared(c: dict, groups: set[str],
                                        path: Path) -> None:
    """Contrast labels must be a SUBSET of groups:, not a union with it.

    The union rule this replaces accepted any label a contrast named, so a typo
    silently became a new legal group and surfaced much later as an arm with no
    animals. As a subset check it is caught here, at load, naming the typo."""
    undeclared = sorted(set(c.get("groups") or []) - groups)
    if undeclared:
        raise ToolError(
            f"Contrast '{c.get('id', '?')}' in {path} compares {undeclared}, which "
            f"groups: does not declare. Declared: {sorted(groups)}. A contrast may "
            "only compare groups the design declares — if that is a typo, fix the "
            "contrast; if the group is real, add it to groups: first."
        )


def _read_declaration(path: Path, yaml) -> dict:
    """Read and parse the declaration, or refuse with an actionable message.

    Every failure here is a **caller** problem — a typo, the wrong path, a
    directory — and must reach the model as a ToolError, for two reasons that both
    bit (HANDOFF-10 Finding 2):

      * errors-as-prompts. A raw `yaml.parser.ParserError` names no file, does not
        say it is the declaration, and tells a model nothing to do next.
      * the outcome taxonomy. A ToolError audits as `refused` — a guard firing, a
        defensive success. A raw exception audits as `error`, the shadow of an
        *infrastructure fault*, and errors the span. Unwrapped, a scientist's YAML
        typo was booked as a crashing server.

    Five distinct raw exceptions were escaping, not one: ParserError/ScannerError
    from a malformed file, AttributeError from a top-level scalar *or* list (the
    `.get` lands on a str), UnicodeDecodeError from a non-UTF-8 file, and
    IsADirectoryError from a path that is a directory."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(
            f"{path} is not valid UTF-8 text, so it cannot be a declaration file "
            f"({exc.reason} at byte {exc.start}). Check DAM_PREREG_PATH points at "
            "the YAML file and not at a binary or a compiled artifact."
        ) from None
    except IsADirectoryError:
        raise ToolError(
            f"{path} is a directory, not a declaration file. DAM_PREREG_PATH must "
            "name the .yaml file itself, e.g. config/contrasts-<experiment>.yaml."
        ) from None
    except OSError as exc:
        raise ToolError(
            f"{path} could not be read: {exc.strerror or exc}. Check the path and "
            "its permissions; DAM_PREREG_PATH is currently "
            f"{os.environ.get('DAM_PREREG_PATH') or 'unset'}."
        ) from None

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        # PyYAML marks are 0-based; editors count from 1.
        where = (f" at line {mark.line + 1}, column {mark.column + 1}"
                 if mark is not None else "")
        problem = getattr(exc, "problem", None) or "could not be parsed"
        raise ToolError(
            f"{path} is not valid YAML{where}: {problem}. This is the "
            "pre-registered declaration, so nothing can run until it parses. The "
            "commonest cause is an unclosed bracket or a bad indent — open the "
            "file at that line."
        ) from None

    if data is None:
        raise ToolError(
            f"{path} is empty. A declaration needs at least 'experiment:' and "
            "'groups:'; contrasts are optional."
        )
    if not isinstance(data, dict):
        raise ToolError(
            f"{path} parses as a {type(data).__name__}, not a mapping. A "
            "declaration is a mapping with 'experiment:' and 'groups:' keys at the "
            "top level — check the indentation and that the file is not a bare "
            "list or a single value."
        )
    return data


def _check_experiment_matches_filename(data: dict, path: Path) -> None:
    """The filename must name the experiment the file declares.

    The per-file layout's value is that the commit introducing
    config/contrasts-<experiment>.yaml is that experiment's pre-registration
    timestamp. A file named -young that declares -old destroys that silently, and
    the workflow is 'copy the previous timepoint and edit it' — precisely how it
    happens. Cheap to check, and it cannot be checked at all if experiment: is
    absent, so experiment: is required."""
    experiment = str(data.get("experiment") or "").strip()
    if not experiment:
        raise ToolError(
            f"{path} has no 'experiment:' value. It is required: the filename must "
            "name the experiment the file declares, so that the commit introducing "
            "the file is a checkable pre-registration timestamp. Add "
            "'experiment: <name>' and name the file contrasts-<name>.yaml."
        )
    if experiment not in path.stem:
        raise ToolError(
            f"{path} declares 'experiment: {experiment}' but its filename stem is "
            f"'{path.stem}', which does not contain it. One of the two is wrong, "
            "and guessing which would defeat the point — a file named for one "
            "experiment while declaring another makes its own commit useless as a "
            f"pre-registration record. Rename the file to contrasts-{experiment}"
            ".yaml, or correct the experiment: value."
        )


def _check_vocabularies(c: dict, path: Path) -> None:
    """phase, metric and test are closed sets, checked at load for every contrast.

    Two reasons this is not left to run time. The engine used to resolve any phase
    outside (light, l, day) to Dark, so 'dusk' or a typo returned a clean-looking
    result for the wrong phase — nothing anomalous to notice. And a bad metric
    surfaced only when that specific contrast ran, so a typo in the twelfth
    contrast stayed invisible through eleven successful ones."""
    cid = c.get("id", "?")
    phase = str(c.get("phase") or "").strip()
    if phase not in PHASES:
        raise ToolError(
            f"Contrast '{cid}' in {path} declares phase {phase!r}, which is not a "
            f"phase this server recognises. Use one of {list(PHASES)}. This is "
            "refused rather than guessed at: resolving an unknown phase to dark "
            "would return a clean-looking result for the wrong half of the day."
        )
    metric = c.get("metric")
    if metric not in METRICS:
        raise ToolError(
            f"Contrast '{cid}' in {path} declares metric {metric!r}, which this "
            f"server cannot compute. Supported: {list(METRICS)}. Add a mapping in "
            "dam_mcp/engine.py before declaring a new one here."
        )
    test = c.get("test", "wilcoxon")
    if test not in TESTS:
        raise ToolError(
            f"Contrast '{cid}' in {path} asks for test {test!r}, which is not "
            f"supported. Use one of {list(TESTS)} ('wilcoxon' is rank-sum, 't' is "
            "Welch)."
        )
    groups = c.get("groups")
    if not isinstance(groups, list) or len(groups) != 2:
        raise ToolError(
            f"Contrast '{cid}' in {path} names {groups!r}; a contrast compares "
            "exactly two groups."
        )


def list_contrasts(path: Path | None = None) -> list[dict]:
    """The declared contrasts, model-readable. This is the menu the agent chooses
    from — it may run any of these and nothing else."""
    return load_config(path).get("contrasts", [])


def declaration_warnings(path: Path | None = None) -> list[str]:
    """Things about the declaration file a reader should know but the loader will
    not refuse over. Surfaced by list_contrasts and assign_groups.

    Currently one: `groups:` written as a mapping carries values that are never
    read. Ignoring them silently is the same shape as the phase fallback this repo
    already found — an input that looks like it does something and does not.
    Someone will write channel ranges there and assume they applied."""
    data = load_config(path)
    raw = data.get("groups")
    if not isinstance(raw, dict):
        return []
    with_values = sorted(k for k, v in raw.items() if v not in (None, {}, [], ""))
    if not with_values:
        return []
    return [
        f"groups: entries {with_values} carry values under them. Those values are "
        "NOT read — this file declares which group labels are legal, nothing more. "
        "Channel ranges reach the server only through assign_groups, at call time. "
        "If you expected the ranges written here to apply, they did not."
    ]


def declared_groups(path: Path | None = None) -> set[str]:
    """The legal group labels this experiment declares.

    assign_groups is checked against this. It used to be checked against the union
    of labels the *contrasts* named — see HANDOFF-9 for why that moved: it gated
    grouping on declared tests, and a workflow whose statistics happen outside this
    tool never declares any. The check did not become optional; it moved onto the
    thing it was always really about, the experimental design."""
    return _parse_groups(load_config(path), path or config_path())


def get_contrast(contrast_id: str, path: Path | None = None) -> dict:
    for c in list_contrasts(path):
        if c.get("id") == contrast_id:
            return c
    available = [c.get("id") for c in list_contrasts(path)]
    if not available:
        raise ToolError(
            f"This experiment declares no contrasts, so '{contrast_id}' cannot be "
            "run — nor can any other. The declaration file carries groups: only, "
            "which is a complete and valid declaration: it permits the whole "
            "load -> window -> group -> compute pipeline, and no statistical test. "
            "Report the metrics; do not invent a comparison to fill the gap."
        )
    raise ToolError(
        f"No declared contrast '{contrast_id}'. The pre-registered set is: "
        f"{available}. The model may only run a contrast that appears here; it "
        "cannot define a new comparison."
    )
