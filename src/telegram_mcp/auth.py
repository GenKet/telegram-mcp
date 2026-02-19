"""CLI script for Telegram authorization."""

import asyncio
import os
import sys
from dotenv import load_dotenv

from .session_creator import TelegramSessionCreator


async def authorize():
    """Interactive authorization flow."""
    load_dotenv()

    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    phone = os.getenv("PHONE")

    if not api_id or not api_hash:
        print("Error: API_ID and API_HASH must be set in .env file")
        print("Get them from https://my.telegram.org/apps")
        sys.exit(1)

    if not phone:
        phone = input("Enter phone number (with country code, e.g. +79001234567): ")

    print(f"\nAuthorizing with phone: {phone}")

    creator = TelegramSessionCreator(
        api_id=int(api_id),
        api_hash=api_hash,
    )

    try:
        print("Requesting SMS code...")
        await creator.request_code(phone)

        code = input("Enter the code you received: ")

        try:
            session_string = await creator.complete_auth(phone, code)
        except ValueError as e:
            if "Two-factor" in str(e):
                password = input("Enter your 2FA password: ")
                session_string = await creator.complete_auth(phone, code, password)
            else:
                raise

        print("\n" + "=" * 60)
        print("SUCCESS! Your session string:")
        print("=" * 60)
        print(session_string)
        print("=" * 60)
        print("\nAdd this to your .env file as:")
        print(f'SESSION_STRING="{session_string}"')
        print("\nOr add it to your Claude Code config in the env section.")

    except Exception as e:
        print(f"\nError during authorization: {e}")
        sys.exit(1)
    finally:
        await creator.cleanup()


def main():
    """Entry point for authorization CLI."""
    asyncio.run(authorize())


if __name__ == "__main__":
    main()
