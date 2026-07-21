"""Session lifecycle and persistence. A server restart must not destroy an hour
of decisions — so the disk round-trip is part of the contract, not an extra."""

from dam_mcp.sessions import Session, SessionStore


def test_create_and_get_roundtrip(tmp_path, monitor_files):
    store = SessionStore(state_dir=tmp_path)
    s = store.create(name="exp", paths=monitor_files)
    assert s.session_id.startswith("dam-")
    assert store.get(s.session_id) is s


def test_survives_restart(tmp_path, monitor_files):
    store = SessionStore(state_dir=tmp_path)
    s = store.create(name="exp", paths=monitor_files)
    s.groups = [{"monitor": "Monitor1.txt", "channel": 1, "labels": "A", "order": 1}]
    store.save(s)

    fresh = SessionStore(state_dir=tmp_path)          # simulates a new process
    reloaded = fresh.get(s.session_id)
    assert reloaded is not None
    assert reloaded.name == "exp"
    assert reloaded.groups[0]["labels"] == "A"


def test_unknown_session_is_none(tmp_path):
    store = SessionStore(state_dir=tmp_path)
    assert store.get("dam-does-not-exist") is None


def test_group_labels_ordered():
    s = Session(session_id="x", name="n", created_at="t", groups=[
        {"monitor": "M", "channel": 1, "labels": "ctrl", "order": 2},
        {"monitor": "M", "channel": 2, "labels": "mut", "order": 1},
    ])
    assert s.group_labels == ["mut", "ctrl"]      # sorted by order, not appearance


def test_excluded_set():
    s = Session(session_id="x", name="n", created_at="t", exclusions=[
        {"monitor": "M", "channel": 5, "reason": "empty", "at": "t"},
    ])
    assert ("M", 5) in s.excluded_set()
