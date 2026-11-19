"""Tests for the message shape shared by every history-reading tool."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from telegram_mcp.client import TelegramTestClient


@pytest.fixture
def client():
    return TelegramTestClient(api_id=1, api_hash="hash", session_string="x")


def make_message(**overrides):
    """A stand-in for a Telethon Message — only the attributes _history_item reads."""
    message = SimpleNamespace(
        id=42,
        date=datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc),
        text="привет",
        sender=SimpleNamespace(first_name="Sergey", title=None),
        reply_to=None,
        photo=None,
        video=None,
        video_note=None,
        voice=None,
        audio=None,
        sticker=None,
        gif=None,
        document=None,
    )
    for key, value in overrides.items():
        setattr(message, key, value)
    return message


def test_plain_message_has_no_reply_fields(client):
    item = client._history_item(make_message())

    assert item == {
        "id": 42,
        "date": "2026-11-19T10:00:00+00:00",
        "text": "привет",
        "sender": "Sergey",
    }


def test_reply_exposes_the_parent_message_id(client):
    message = make_message(reply_to=SimpleNamespace(reply_to_msg_id=35, quote_text=None))

    assert client._history_item(message)["reply_to_id"] == 35


def test_reply_keeps_the_quoted_fragment(client):
    message = make_message(
        reply_to=SimpleNamespace(reply_to_msg_id=35, quote_text="только для онлайн")
    )
    item = client._history_item(message)

    assert item["reply_to_id"] == 35
    assert item["quote"] == "только для онлайн"


def test_reply_without_a_quote_omits_the_field(client):
    message = make_message(reply_to=SimpleNamespace(reply_to_msg_id=35, quote_text=None))

    assert "quote" not in client._history_item(message)


def test_forum_topic_header_without_a_parent_adds_nothing(client):
    """A topic root carries reply_to with no reply_to_msg_id — not a reply to anything."""
    message = make_message(reply_to=SimpleNamespace(reply_to_msg_id=None, quote_text=None))

    assert "reply_to_id" not in client._history_item(message)
