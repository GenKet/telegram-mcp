"""Tests for the pure interview decision rules."""

from datetime import datetime, timezone

import pytest

from telegram_mcp import interview_rules as rules


def make_state(**overrides):
    state = {
        "id": "acme",
        "peer": "acmeuser",
        "peer_id": 42,
        "goal": "goal",
        "status": "draft",
        "escalation": None,
        "checklist": [
            {"id": "q1", "question": "Тип сайта?", "status": "open", "answer": None, "asked_at": None},
            {"id": "q2", "question": "Сроки?", "status": "open", "answer": None, "asked_at": None},
        ],
        "messages": [],
        "last_seen_message_id": 0,
        "sent_count": 0,
        "max_messages": 10,
        "quiet_hours": None,
        "summary": None,
    }
    state.update(overrides)
    return state


NOON = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def test_open_items_lists_only_unanswered():
    state = make_state()
    state["checklist"][0]["status"] = "answered"

    assert [item["id"] for item in rules.open_items(state)] == ["q2"]


def test_is_complete_when_every_item_answered_or_skipped():
    state = make_state()
    state["checklist"][0]["status"] = "answered"
    state["checklist"][1]["status"] = "skipped"

    assert rules.is_complete(state) is True


def test_is_complete_false_with_an_open_item():
    assert rules.is_complete(make_state()) is False


def test_can_send_allows_first_message():
    assert rules.can_send(make_state(), NOON) == (True, None)


def test_can_send_blocks_two_messages_in_a_row():
    state = make_state(messages=[{"id": 1, "from": "me", "text": "первый вопрос", "date": None}])

    allowed, reason = rules.can_send(state, NOON)
    assert allowed is False
    assert "waiting for the peer" in reason


def test_can_send_allows_after_peer_replied():
    state = make_state(
        messages=[
            {"id": 1, "from": "me", "text": "вопрос", "date": None},
            {"id": 2, "from": "peer", "text": "ответ", "date": None},
        ]
    )

    assert rules.can_send(state, NOON) == (True, None)


def test_can_send_blocks_at_message_limit():
    state = make_state(sent_count=10, max_messages=10)

    allowed, reason = rules.can_send(state, NOON)
    assert allowed is False
    assert "message limit" in reason


def test_can_send_blocks_when_finished_or_escalated():
    for status in ("done", "needs_human"):
        allowed, reason = rules.can_send(make_state(status=status), NOON)
        assert allowed is False
        assert status in reason


def test_quiet_hours_block_overnight_window():
    state = make_state(quiet_hours=[23, 9])

    assert rules.in_quiet_hours(state, datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)) is True
    assert rules.in_quiet_hours(state, datetime(2026, 9, 8, 23, 30, tzinfo=timezone.utc)) is True
    assert rules.in_quiet_hours(state, NOON) is False


def test_quiet_hours_off_by_default():
    assert rules.in_quiet_hours(make_state(), datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc)) is False


def test_can_send_blocks_inside_quiet_hours():
    state = make_state(quiet_hours=[23, 9])

    allowed, reason = rules.can_send(state, datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc))
    assert allowed is False
    assert "quiet hours" in reason


def test_record_sent_marks_asked_items_and_switches_to_waiting():
    state = make_state()

    rules.record_sent(state, "Тип сайта? Сроки?", message_id=7, covers=["q1", "q2"], now=NOON)

    assert state["status"] == "waiting"
    assert state["sent_count"] == 1
    assert state["messages"][-1] == {"id": 7, "from": "me", "text": "Тип сайта? Сроки?", "date": NOON.isoformat()}
    assert state["checklist"][0]["asked_at"] == NOON.isoformat()
    assert state["checklist"][0]["status"] == "open"


def test_merge_messages_deduplicates_and_sorts():
    state = make_state(messages=[{"id": 5, "from": "peer", "text": "старое", "date": None}])

    added = rules.merge_messages(
        state,
        [
            {"id": 5, "from": "peer", "text": "старое", "date": None},
            {"id": 7, "from": "peer", "text": "новое", "date": None},
            {"id": 6, "from": "me", "text": "моё", "date": None},
        ],
    )

    assert added == 2
    assert [m["id"] for m in state["messages"]] == [5, 6, 7]


def test_merge_messages_flags_attention_for_peer_replies():
    state = make_state(status="waiting")

    rules.merge_messages(state, [{"id": 9, "from": "peer", "text": "ответ", "date": None}])

    assert state["status"] == "needs_attention"


def test_merge_messages_keeps_status_for_own_messages():
    state = make_state(status="waiting")

    rules.merge_messages(state, [{"id": 9, "from": "me", "text": "моё", "date": None}])

    assert state["status"] == "waiting"


def test_delta_returns_unprocessed_peer_messages_only():
    state = make_state(
        last_seen_message_id=5,
        messages=[
            {"id": 5, "from": "peer", "text": "обработано", "date": None},
            {"id": 6, "from": "me", "text": "моё", "date": None},
            {"id": 7, "from": "peer", "text": "новое", "date": None},
        ],
    )

    assert [m["id"] for m in rules.delta(state)] == [7]


def test_mark_answers_records_answers_and_advances_seen_pointer():
    state = make_state(status="needs_attention")

    rules.mark_answers(
        state,
        [{"item_id": "q1", "status": "answered", "answer": "лендинг"}],
        seen_up_to=7,
    )

    assert state["checklist"][0]["status"] == "answered"
    assert state["checklist"][0]["answer"] == "лендинг"
    assert state["last_seen_message_id"] == 7
    assert state["status"] == "waiting"


def test_mark_answers_is_idempotent():
    state = make_state()
    marks = [{"item_id": "q1", "status": "answered", "answer": "лендинг"}]

    rules.mark_answers(state, marks, seen_up_to=7)
    rules.mark_answers(state, marks, seen_up_to=7)

    assert state["checklist"][0]["answer"] == "лендинг"
    assert state["last_seen_message_id"] == 7


def test_mark_answers_never_rewinds_seen_pointer():
    state = make_state(last_seen_message_id=9)

    rules.mark_answers(state, [], seen_up_to=4)

    assert state["last_seen_message_id"] == 9


def test_mark_answers_rejects_unknown_item():
    state = make_state()

    with pytest.raises(KeyError):
        rules.mark_answers(state, [{"item_id": "nope", "status": "answered", "answer": "x"}])


def test_escalate_sets_needs_human_with_reason():
    state = make_state(status="needs_attention")

    rules.escalate(state, "спросил про цену")

    assert state["status"] == "needs_human"
    assert state["escalation"] == "спросил про цену"


def test_finish_stores_summary_and_closes():
    state = make_state()

    rules.finish(state, "нужен лендинг за две недели")

    assert state["status"] == "done"
    assert state["summary"] == "нужен лендинг за две недели"
