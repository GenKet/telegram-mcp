"""Tests for argument parsing helpers used by the tools."""

from datetime import datetime, timezone

import pytest

from telegram_mcp.client import TelegramTestClient
from telegram_mcp.server import _optional_int

parse_date = TelegramTestClient._parse_date


def test_parse_date_none_stays_none():
    assert parse_date(None) is None
    assert parse_date("") is None


def test_parse_date_bare_date_becomes_utc_midnight():
    assert parse_date("2026-09-02") == datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_parse_date_naive_datetime_is_treated_as_utc():
    assert parse_date("2026-09-02T18:30") == datetime(2026, 9, 2, 18, 30, tzinfo=timezone.utc)


def test_parse_date_keeps_explicit_offset():
    parsed = parse_date("2026-09-02T18:30:00+03:00")
    assert parsed.utcoffset().total_seconds() == 3 * 3600
    assert parsed.astimezone(timezone.utc).hour == 15


def test_parse_date_accepts_zulu_suffix():
    assert parse_date("2026-09-02T18:30:00Z") == datetime(2026, 9, 2, 18, 30, tzinfo=timezone.utc)


def test_parse_date_rejects_garbage():
    with pytest.raises(ValueError):
        parse_date("last tuesday")


@pytest.mark.parametrize(
    "value,expected",
    [("5", 5), (5, 5), (None, None), ("", None)],
)
def test_optional_int(value, expected):
    assert _optional_int(value) == expected
