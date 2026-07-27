"""Read-only access to the pre-declared contrast set.

The whole point (spec, CLAUDE.md): the agent may run any contrast in
config/contrasts.yaml and cannot invent one. Pre-registration enforced by the
tool boundary, not by discipline. This module only *reads* the file — nothing in
the server writes it — and validates that each contrast is well-formed before the
model is allowed to pick one.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ToolError

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "contrasts.yaml"
)

_REQUIRED_KEYS = {"id", "metric", "phase", "groups"}


def config_path() -> Path:
    """Which pre-registered set is in effect: DAM_CONTRASTS_PATH if set, else the
    repo's config/contrasts.yaml.

    One server commonly serves several experiments — a young / middle / old
    timepoint series is one pre-registration each — and the alternative to this is
    overwriting one file in place, where nothing records which set was live for a
    given run. That is the failure this repo keeps finding: an operation that
    silently used the wrong input and reported success. list_contrasts returns the
    resolved path for the same reason.

    Read per call, never frozen at import: a captured path is indistinguishable
    from correct in a single-experiment process and wrong the moment a second set
    is used. `or` rather than a get() default, so an empty value from a launch spec
    built off a maybe-None falls back instead of resolving to Path('')."""
    env = os.environ.get("DAM_CONTRASTS_PATH")
    if env:
        return Path(env)
    return DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> dict:
    """Parse contrasts.yaml into a dict. Raises ToolError (errors-as-prompts) on a
    missing file, missing PyYAML, or a malformed contrast declaration."""
    path = path or config_path()
    if not path.exists():
        raise ToolError(
            f"No contrast config at {path}. Contrasts must be declared before the "
            "data is seen — create that file with the pre-registered comparisons "
            "(see the example in the repo). If that is not the set you meant, "
            "check DAM_CONTRASTS_PATH: it overrides the default location and is "
            f"currently {os.environ.get('DAM_CONTRASTS_PATH') or 'unset'}."
        )
    try:
        import yaml
    except ModuleNotFoundError:
        raise ToolError(
            "PyYAML is not installed, so the contrast config cannot be read. Run "
            "`pip install pyyaml` (it is a declared dependency) and retry. Every "
            "other tool works without it; only list_contrasts and run_contrast need it."
        ) from None

    data = yaml.safe_load(path.read_text()) or {}
    contrasts = data.get("contrasts") or []
    if not isinstance(contrasts, list):
        raise ToolError(
            "config/contrasts.yaml has a 'contrasts:' key that is not a list. It "
            "must be a list of contrast declarations."
        )
    for i, c in enumerate(contrasts):
        if not isinstance(c, dict):
            raise ToolError(f"Contrast #{i} in contrasts.yaml is not a mapping.")
        missing = _REQUIRED_KEYS - set(c)
        if missing:
            raise ToolError(
                f"Contrast '{c.get('id', f'#{i}')}' is missing required key(s): "
                f"{sorted(missing)}. Each needs id, metric, phase, groups."
            )
    return data


def list_contrasts(path: Path | None = None) -> list[dict]:
    """The declared contrasts, model-readable. This is the menu the agent chooses
    from — it may run any of these and nothing else."""
    return load_config(path).get("contrasts", [])


def contrast_group_labels(path: Path | None = None) -> set[str]:
    """Every group label referenced by a declared contrast. assign_groups checks
    the human's labels against this so a legal contrast id can never point at a
    group that does not exist in the session (the config/session mismatch bug)."""
    labels: set[str] = set()
    for c in list_contrasts(path):
        for g in c.get("groups", []):
            labels.add(g)
    return labels


def get_contrast(contrast_id: str, path: Path | None = None) -> dict:
    for c in list_contrasts(path):
        if c.get("id") == contrast_id:
            return c
    available = [c.get("id") for c in list_contrasts(path)]
    raise ToolError(
        f"No declared contrast '{contrast_id}'. The pre-registered set is: "
        f"{available}. The model may only run a contrast that appears here; it "
        "cannot define a new comparison."
    )
