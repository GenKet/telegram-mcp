"""Tests for the config writers used by the auth CLI."""

import json

from telegram_mcp.auth import update_claude_config, write_env_var


def test_write_env_var_replaces_existing_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("API_ID=1\nSESSION_STRING=old\nPHONE=+1\n")

    write_env_var(env, "SESSION_STRING", "new")

    assert env.read_text() == "API_ID=1\nSESSION_STRING=new\nPHONE=+1\n"


def test_write_env_var_appends_missing_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("API_ID=1\n")

    write_env_var(env, "SESSION_STRING", "new")

    assert env.read_text().splitlines()[-1] == "SESSION_STRING=new"


def test_write_env_var_creates_file(tmp_path):
    env = tmp_path / ".env"

    write_env_var(env, "SESSION_STRING", "new")

    assert env.read_text() == "SESSION_STRING=new\n"


def test_update_claude_config_replaces_session_and_keeps_formatting(tmp_path):
    config = tmp_path / ".claude.json"
    config.write_text(
        '{\n  "other": {"keep": true},\n  "mcpServers": {\n'
        '    "telegram": {"env": {"API_ID": "1", "SESSION_STRING": "old"}}\n  }\n}\n'
    )

    assert update_claude_config(config, "new") is True

    raw = config.read_text()
    assert '"other": {"keep": true}' in raw  # untouched, not reformatted
    assert json.loads(raw)["mcpServers"]["telegram"]["env"]["SESSION_STRING"] == "new"


def test_update_claude_config_adds_missing_session_key(tmp_path):
    config = tmp_path / ".claude.json"
    config.write_text('{"mcpServers": {"telegram": {"env": {"API_ID": "1"}}}}')

    assert update_claude_config(config, "new") is True
    assert json.loads(config.read_text())["mcpServers"]["telegram"]["env"]["SESSION_STRING"] == "new"


def test_update_claude_config_skips_unknown_server(tmp_path):
    config = tmp_path / ".claude.json"
    config.write_text('{"mcpServers": {"other": {"env": {}}}}')

    assert update_claude_config(config, "new") is False


def test_update_claude_config_skips_missing_file(tmp_path):
    assert update_claude_config(tmp_path / "absent.json", "new") is False
