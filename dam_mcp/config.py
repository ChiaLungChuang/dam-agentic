"""Read-only access to the pre-declared contrast set.

The whole point (spec, CLAUDE.md): the agent may run any contrast in
config/contrasts.yaml and cannot invent one. Pre-registration enforced by the
tool boundary, not by discipline. This module only *reads* the file — nothing in
the server writes it — and validates that each contrast is well-formed before the
model is allowed to pick one.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ToolError

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "contrasts.yaml"
)

_REQUIRED_KEYS = {"id", "metric", "phase", "groups"}


def config_path() -> Path:
    return _CONFIG_PATH


def load_config(path: Path | None = None) -> dict:
    """Parse contrasts.yaml into a dict. Raises ToolError (errors-as-prompts) on a
    missing file, missing PyYAML, or a malformed contrast declaration."""
    path = path or _CONFIG_PATH
    if not path.exists():
        raise ToolError(
            f"No contrast config at {path}. Contrasts must be declared before the "
            "data is seen — create config/contrasts.yaml with the pre-registered "
            "comparisons (see the example in the repo)."
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
