"""Tests for interview state persistence."""

import json
import os
import stat

import pytest

from telegram_mcp import interview_store as store


@pytest.fixture(autouse=True)
def state_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_MCP_STATE_DIR", str(tmp_path))
    return tmp_path


def test_create_builds_checklist_from_questions():
    state = store.create("clientx-site", "clientX", "выяснить какой сайт нужен", ["Тип сайта?", "Сроки?"])

    assert state["id"] == "clientx-site"
    assert state["peer"] == "clientX"
    assert state["peer_id"] is None
    assert state["status"] == "draft"
    assert state["last_seen_message_id"] == 0
    assert state["sent_count"] == 0
    assert state["max_messages"] == 10
    assert [item["id"] for item in state["checklist"]] == ["q1", "q2"]
    assert state["checklist"][0] == {
        "id": "q1",
        "question": "Тип сайта?",
        "status": "open",
        "answer": None,
        "asked_at": None,
    }


def test_create_persists_file_and_is_readable_back():
    store.create("acme", "acmeuser", "goal", ["Q?"])

    loaded = store.load("acme")
    assert loaded["peer"] == "acmeuser"
    assert loaded["goal"] == "goal"


def test_create_rejects_duplicate_id():
    store.create("acme", "acmeuser", "goal", ["Q?"])

    with pytest.raises(FileExistsError):
        store.create("acme", "acmeuser", "goal", ["Q?"])


def test_save_is_atomic_and_leaves_no_temp_files(state_root):
    state = store.create("acme", "acmeuser", "goal", ["Q?"])
    state["goal"] = "updated"
    store.save(state)

    files = sorted(p.name for p in (state_root / "interviews").iterdir())
    assert files == ["acme.json"]
    assert json.loads((state_root / "interviews" / "acme.json").read_text())["goal"] == "updated"


def test_state_files_are_owner_only(state_root):
    store.create("acme", "acmeuser", "goal", ["Q?"])

    mode = stat.S_IMODE(os.stat(state_root / "interviews" / "acme.json").st_mode)
    assert mode == 0o600


def test_load_missing_interview_raises():
    with pytest.raises(FileNotFoundError):
        store.load("nope")


def test_load_all_returns_every_interview_sorted_by_id():
    store.create("b-one", "peer1", "goal", ["Q?"])
    store.create("a-two", "peer2", "goal", ["Q?"])

    assert [s["id"] for s in store.load_all()] == ["a-two", "b-one"]


def test_load_all_on_empty_dir_returns_empty_list():
    assert store.load_all() == []


def test_heartbeat_age_is_none_without_file():
    assert store.heartbeat_age() is None


def test_heartbeat_age_measures_seconds_since_write():
    store.heartbeat_write(now=1000.0)

    assert store.heartbeat_age(now=1042.0) == pytest.approx(42.0)


def test_create_rejects_second_active_interview_for_same_peer():
    store.create("first", "acmeuser", "goal", ["Q?"])

    with pytest.raises(ValueError, match="already has an active interview"):
        store.create("second", "acmeuser", "another goal", ["Q?"])


def test_create_allows_new_interview_once_the_previous_is_done():
    state = store.create("first", "acmeuser", "goal", ["Q?"])
    state["status"] = "done"
    store.save(state)

    assert store.create("second", "acmeuser", "another goal", ["Q?"])["id"] == "second"


def test_create_allows_same_id_pattern_for_different_peers():
    store.create("first", "acmeuser", "goal", ["Q?"])

    assert store.create("second", "otheruser", "goal", ["Q?"])["peer"] == "otheruser"


def test_heartbeat_reports_connection_state():
    store.heartbeat_write(connected=False, now=1000.0)

    status = store.heartbeat_status(now=1005.0)
    assert status == {"age": pytest.approx(5.0), "connected": False}


def test_heartbeat_status_is_none_without_file():
    assert store.heartbeat_status() is None


def test_heartbeat_status_reads_legacy_plain_timestamp():
    """A heartbeat written by an older listener holds a bare float."""
    (store.state_dir() / store.HEARTBEAT_NAME).write_text("1000.0")

    status = store.heartbeat_status(now=1003.0)
    assert status["age"] == pytest.approx(3.0)
    assert status["connected"] is None
