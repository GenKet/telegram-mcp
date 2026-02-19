"""Telegram session creator for authorization flow."""

import asyncio
from typing import Optional, Callable, Awaitable
from telethon.sessions import StringSession

# Apply patch before importing TelegramClient
from .patch import telegrambaseclient  # noqa: F401 - patches TelegramClient on import
from telethon import TelegramClient


class TelegramSessionCreator:
    """
    Creates and manages Telegram sessions with proper device parameters.
    Handles the full authorization flow.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        device_model: str = "Samsung SM-G998B",
        system_version: str = "SDK 33",
        app_version: str = "10.8.3",
        lang_code: str = "ru",
        lang_pack: str = "android",
        system_lang_code: str = "ru",
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.device_model = device_model
        self.system_version = system_version
        self.app_version = app_version
        self.lang_code = lang_code
        self.lang_pack = lang_pack
        self.system_lang_code = system_lang_code

        self._client: Optional[TelegramClient] = None
        self._phone_code_hash: Optional[str] = None

    def _create_client(self, session: StringSession = None) -> TelegramClient:
        """Create a TelegramClient with proper parameters."""
        return TelegramClient(
            session if session else StringSession(),
            self.api_id,
            self.api_hash,
            device_model=self.device_model,
            system_version=self.system_version,
            app_version=self.app_version,
            lang_code=self.lang_code,
            lang_pack=self.lang_pack,
            system_lang_code=self.system_lang_code,
        )

    async def request_code(self, phone: str) -> bool:
        """
        Request SMS code for authorization.
        Returns True if code was sent successfully.
        """
        self._client = self._create_client()
        await self._client.connect()

        result = await self._client.send_code_request(phone)
        self._phone_code_hash = result.phone_code_hash
        return True

    async def complete_auth(
        self,
        phone: str,
        code: str,
        password: Optional[str] = None,
    ) -> str:
        """
        Complete authorization with the received code.
        Returns the session string if successful.
        """
        if not self._client or not self._phone_code_hash:
            raise ValueError("Must call request_code first")

        try:
            await self._client.sign_in(
                phone,
                code,
                phone_code_hash=self._phone_code_hash,
            )
        except Exception as e:
            if "2FA" in str(e) or "password" in str(e).lower():
                if password:
                    await self._client.sign_in(password=password)
                else:
                    raise ValueError("Two-factor authentication required")
            else:
                raise

        session_string = self._client.session.save()
        return session_string

    async def create_from_session_string(self, session_string: str) -> TelegramClient:
        """
        Create a connected client from an existing session string.
        """
        session = StringSession(session_string)
        client = self._create_client(session)
        await client.connect()

        if not await client.is_user_authorized():
            raise ValueError("Session is not authorized")

        return client

    async def cleanup(self):
        """Disconnect and cleanup."""
        if self._client:
            await self._client.disconnect()
            self._client = None


async def create_session_interactive(
    api_id: int,
    api_hash: str,
    phone: str,
    get_code: Callable[[], Awaitable[str]],
    get_password: Optional[Callable[[], Awaitable[str]]] = None,
) -> str:
    """
    Interactive session creation helper.

    Args:
        api_id: Telegram API ID
        api_hash: Telegram API hash
        phone: Phone number with country code
        get_code: Async function that returns the SMS code
        get_password: Optional async function that returns 2FA password

    Returns:
        Session string for storage
    """
    creator = TelegramSessionCreator(api_id, api_hash)

    try:
        await creator.request_code(phone)
        code = await get_code()

        try:
            return await creator.complete_auth(phone, code)
        except ValueError as e:
            if "Two-factor" in str(e) and get_password:
                password = await get_password()
                return await creator.complete_auth(phone, code, password)
            raise
    finally:
        await creator.cleanup()
