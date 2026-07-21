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
