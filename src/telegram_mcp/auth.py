"""CLI for Telegram authorization.

Interactive (asks for the code on stdin):

    telegram-mcp-auth

Step-by-step (each step is its own process — usable from scripts and agents):

    telegram-mcp-auth request
    telegram-mcp-auth code 12345 [--password secret]
    telegram-mcp-auth password secret
    telegram-mcp-auth status
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .session_creator import TelegramSessionCreator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
STATE_PATH = PROJECT_ROOT / ".auth_state.json"
CLAUDE_CONFIG_PATH = Path.home() / ".claude.json"
MCP_SERVER_NAME = "telegram"


def write_env_var(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file, replacing an existing line for that key."""
    lines = path.read_text().splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")


def update_claude_config(
    path: Path,
    session_string: str,
    server: str = MCP_SERVER_NAME,
) -> bool:
    """Point the MCP server entry in ~/.claude.json at a new session string.

    Replaces the old value textually so the rest of the (large) config keeps its formatting.
    Returns False when there is nothing to update.
    """
    if not path.exists():
        return False

    raw = path.read_text()
    entry = json.loads(raw).get("mcpServers", {}).get(server)
    if not isinstance(entry, dict) or not isinstance(entry.get("env"), dict):
        return False

    old = entry["env"].get("SESSION_STRING")
    if old == session_string:
        return True
    if old:
        path.write_text(raw.replace(old, session_string))
    else:
        config = json.loads(raw)
        config["mcpServers"][server]["env"]["SESSION_STRING"] = session_string
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return True


def save_state(session: str, phone: str, phone_code_hash: str) -> None:
    """Store the half-finished login. Contains an auth key — keep it out of git."""
    STATE_PATH.write_text(
        json.dumps({"session": session, "phone": phone, "phone_code_hash": phone_code_hash})
    )
    STATE_PATH.chmod(0o600)


def load_state() -> dict:
    if not STATE_PATH.exists():
        sys.exit("No pending authorization. Run: telegram-mcp-auth request")
    return json.loads(STATE_PATH.read_text())


def clear_state() -> None:
    STATE_PATH.unlink(missing_ok=True)


def credentials() -> tuple:
    load_dotenv(ENV_PATH)
    api_id, api_hash = os.getenv("API_ID"), os.getenv("API_HASH")
    if not api_id or not api_hash:
        sys.exit("API_ID and API_HASH must be set in .env (get them at https://my.telegram.org/apps)")
    return int(api_id), api_hash


def persist(session_string: str, update_claude: bool = True) -> None:
    """Write the new session everywhere it is read from, then drop the pending state."""
    print("\nSESSION_STRING=" + session_string)
    write_env_var(ENV_PATH, "SESSION_STRING", session_string)
    print(f"\nwritten to {ENV_PATH}")
    if update_claude and update_claude_config(CLAUDE_CONFIG_PATH, session_string):
        print(f"written to {CLAUDE_CONFIG_PATH} (mcpServers.{MCP_SERVER_NAME})")
        print("run /mcp in Claude Code to reconnect the server")
    clear_state()


async def cmd_request(phone: Optional[str], **_) -> None:
    api_id, api_hash = credentials()
    phone = phone or os.getenv("PHONE") or input("Phone number (e.g. +79001234567): ")

    creator = TelegramSessionCreator(api_id, api_hash)
    try:
        await creator.request_code(phone)
        save_state(creator.save_session(), phone, creator.phone_code_hash)
        print(f"Code sent to {phone} (check the Telegram app first, then SMS)")
        print("Next: telegram-mcp-auth code <code>")
    finally:
        await creator.cleanup()


async def cmd_code(code: str, password: Optional[str] = None, no_claude_config: bool = False, **_) -> None:
    api_id, api_hash = credentials()
    state = load_state()

    creator = TelegramSessionCreator(api_id, api_hash)
    try:
        await creator.attach(state["session"], state["phone_code_hash"])
        try:
            session_string = await creator.complete_auth(state["phone"], code, password)
        except ValueError as e:
            if "Two-factor" not in str(e):
                raise
            print("Code accepted, but this account has 2FA enabled.")
            sys.exit("Next: telegram-mcp-auth password <your-2fa-password>")
        persist(session_string, update_claude=not no_claude_config)
    finally:
        await creator.cleanup()


async def cmd_password(password: str, no_claude_config: bool = False, **_) -> None:
    api_id, api_hash = credentials()
    state = load_state()

    creator = TelegramSessionCreator(api_id, api_hash)
    try:
        await creator.attach(state["session"], state["phone_code_hash"])
        persist(await creator.complete_2fa(password), update_claude=not no_claude_config)
    finally:
        await creator.cleanup()


async def cmd_status(**_) -> None:
    api_id, api_hash = credentials()
    session_string = os.getenv("SESSION_STRING")
    if not session_string:
        sys.exit("No SESSION_STRING in .env. Run: telegram-mcp-auth request")

    creator = TelegramSessionCreator(api_id, api_hash)
    try:
        client = await creator.create_from_session_string(session_string)
        me = await client.get_me()
        print(f"authorized: id={me.id} name={me.first_name} username=@{me.username} phone=+{me.phone}")
        await client.disconnect()
    except Exception as e:
        sys.exit(f"session dead ({e}). Run: telegram-mcp-auth request")


async def cmd_interactive(no_claude_config: bool = False, **_) -> None:
    """Original single-shot flow: request a code and read it from stdin."""
    api_id, api_hash = credentials()
    phone = os.getenv("PHONE") or input("Phone number (e.g. +79001234567): ")

    creator = TelegramSessionCreator(api_id, api_hash)
    try:
        print(f"Requesting code for {phone}...")
        await creator.request_code(phone)
        code = input("Code: ")
        try:
            session_string = await creator.complete_auth(phone, code)
        except ValueError as e:
            if "Two-factor" not in str(e):
                raise
            session_string = await creator.complete_2fa(input("2FA password: "))
        persist(session_string, update_claude=not no_claude_config)
    except Exception as e:
        sys.exit(f"Authorization failed: {e}")
    finally:
        await creator.cleanup()


def main():
    """Entry point for the authorization CLI."""
    parser = argparse.ArgumentParser(description="Authorize a Telegram session for telegram-mcp")
    parser.add_argument(
        "--no-claude-config",
        action="store_true",
        help="do not touch ~/.claude.json, write only .env",
    )
    sub = parser.add_subparsers(dest="command")

    p_request = sub.add_parser("request", help="request a login code")
    p_request.add_argument("--phone", help="phone number with country code (default: PHONE from .env)")

    p_code = sub.add_parser("code", help="submit the login code")
    p_code.add_argument("code")
    p_code.add_argument("--password", help="2FA password, if the account has one")

    p_password = sub.add_parser("password", help="submit the 2FA password after the code step")
    p_password.add_argument("password")

    sub.add_parser("status", help="check whether SESSION_STRING in .env still works")

    args = parser.parse_args()
    handlers = {
        None: cmd_interactive,
        "request": cmd_request,
        "code": cmd_code,
        "password": cmd_password,
        "status": cmd_status,
    }
    asyncio.run(handlers[args.command](**{k: v for k, v in vars(args).items() if k != "command"}))


if __name__ == "__main__":
    main()
