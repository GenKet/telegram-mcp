"""Telegram client wrapper using Telethon with StringSession support."""

import asyncio
import os
from typing import Optional, List, Dict, Any
from telethon.sessions import StringSession
from telethon.tl.types import (
    ReplyKeyboardMarkup,
    ReplyInlineMarkup,
    KeyboardButtonRow,
    KeyboardButton,
    KeyboardButtonCallback,
    KeyboardButtonWebView,
    KeyboardButtonSimpleWebView,
    Message,
    InputPeerUser,
)
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest, RequestWebViewRequest

# Apply patch before importing TelegramClient
from .patch import telegrambaseclient  # noqa: F401 - patches TelegramClient on import
from telethon import TelegramClient


class TelegramTestClient:
    """Client for interacting with Telegram bots for testing."""

    # Default device parameters (Android)
    DEFAULT_DEVICE_MODEL = "Samsung SM-G998B"
    DEFAULT_SYSTEM_VERSION = "SDK 33"
    DEFAULT_APP_VERSION = "10.8.3"
    DEFAULT_LANG_CODE = "ru"
    DEFAULT_LANG_PACK = "android"
    DEFAULT_SYSTEM_LANG_CODE = "ru"

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_string: Optional[str] = None,
        session_name: str = "telegram_mcp_session",
        phone: Optional[str] = None,
        device_model: Optional[str] = None,
        system_version: Optional[str] = None,
        app_version: Optional[str] = None,
        lang_code: Optional[str] = None,
        lang_pack: Optional[str] = None,
        system_lang_code: Optional[str] = None,
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.session_name = session_name
        self.phone = phone

        # Device parameters
        self.device_model = device_model or self.DEFAULT_DEVICE_MODEL
        self.system_version = system_version or self.DEFAULT_SYSTEM_VERSION
        self.app_version = app_version or self.DEFAULT_APP_VERSION
        self.lang_code = lang_code or self.DEFAULT_LANG_CODE
        self.lang_pack = lang_pack or self.DEFAULT_LANG_PACK
        self.system_lang_code = system_lang_code or self.DEFAULT_SYSTEM_LANG_CODE

        self.client: Optional[TelegramClient] = None
        self.bot_entity = None
        self.bot_username: Optional[str] = None

    def _create_client(self) -> TelegramClient:
        """Create TelegramClient with proper parameters."""
        # Use StringSession if session_string provided, otherwise file-based
        if self.session_string:
            session = StringSession(self.session_string)
        else:
            session = self.session_name

        return TelegramClient(
            session,
            self.api_id,
            self.api_hash,
            device_model=self.device_model,
            system_version=self.system_version,
            app_version=self.app_version,
            lang_code=self.lang_code,
            lang_pack=self.lang_pack,
            system_lang_code=self.system_lang_code,
        )

    async def connect(self) -> bool:
        """Connect to Telegram."""
        self.client = self._create_client()

        # If we have a session string, just connect (already authorized)
        if self.session_string:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                raise Exception("Session is not authorized. Please re-authenticate.")
        else:
            # Need to authorize with phone
            await self.client.start(phone=self.phone)

        return self.client.is_connected()

    async def disconnect(self):
        """Disconnect from Telegram."""
        if self.client:
            await self.client.disconnect()

    def get_session_string(self) -> Optional[str]:
        """Get current session as string for storage."""
        if self.client and self.client.session:
            return self.client.session.save()
        return None

    async def set_bot(self, bot_username: str) -> bool:
        """Set the default peer (bot/user/group) to interact with."""
        if not self.client:
            return False
        try:
            self.bot_username = bot_username.lstrip("@")
            self.bot_entity = await self.client.get_entity(self.bot_username)
            return True
        except Exception as e:
            raise Exception(f"Failed to find @{bot_username}: {e}")

    async def _resolve_peer(self, peer: Optional[str] = None):
        """Resolve peer entity. Uses provided peer or falls back to default bot_entity."""
        if peer is None:
            if not self.bot_entity:
                raise Exception("No default peer set. Use telegram_set_bot or provide 'peer' parameter.")
            return self.bot_entity
        if not self.client:
            raise Exception("Not connected")
        try:
            return await self.client.get_entity(peer.lstrip("@"))
        except Exception as e:
            raise Exception(f"Failed to find peer @{peer}: {e}")

    async def send_message(self, text: str, peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a text message and wait for response."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        # Send message
        await self.client.send_message(entity, text)

        # Wait for response
        await asyncio.sleep(1)

        # Get latest messages
        messages = await self.client.get_messages(entity, limit=5)

        # Find response (first message not from us after ours)
        responses = []
        for msg in messages:
            if msg.out:
                break
            responses.append(msg)

        if not responses:
            await asyncio.sleep(2)
            messages = await self.client.get_messages(entity, limit=5)
            for msg in messages:
                if msg.out:
                    break
                responses.append(msg)

        return self._format_messages(responses)

    async def click_button(self, button_text: str, peer: Optional[str] = None) -> Dict[str, Any]:
        """Click a button (reply keyboard or inline)."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        # Get latest message with keyboard
        messages = await self.client.get_messages(entity, limit=10)

        for msg in messages:
            if msg.out:
                continue

            # Check for inline keyboard
            if msg.reply_markup and isinstance(msg.reply_markup, ReplyInlineMarkup):
                for row in msg.reply_markup.rows:
                    for button in row.buttons:
                        if hasattr(button, "text") and button.text == button_text:
                            if hasattr(button, "data"):
                                await self.client(
                                    GetBotCallbackAnswerRequest(
                                        peer=entity,
                                        msg_id=msg.id,
                                        data=button.data,
                                    )
                                )
                                await asyncio.sleep(1)
                                return await self._get_latest_response(entity)

            # Check for reply keyboard
            if msg.reply_markup and isinstance(msg.reply_markup, ReplyKeyboardMarkup):
                for row in msg.reply_markup.rows:
                    for button in row.buttons:
                        if hasattr(button, "text") and button.text == button_text:
                            return await self.send_message(button.text, peer=peer)

        # If button not found, just send text
        return await self.send_message(button_text, peer=peer)

    async def send_voice(self, file_path: str, peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a voice message."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        if not os.path.exists(file_path):
            raise Exception(f"Voice file not found: {file_path}")

        await self.client.send_file(entity, file_path, voice_note=True)

        await asyncio.sleep(2)
        return await self._get_latest_response(entity)

    async def send_photo(self, file_path: str, caption: str = "", peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a photo."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        if not os.path.exists(file_path):
            raise Exception(f"Photo file not found: {file_path}")

        await self.client.send_file(entity, file_path, caption=caption)

        await asyncio.sleep(2)
        return await self._get_latest_response(entity)

    async def send_file(self, file_path: str, caption: str = "", peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a file/document."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        if not os.path.exists(file_path):
            raise Exception(f"File not found: {file_path}")

        await self.client.send_file(entity, file_path, caption=caption, force_document=True)

        await asyncio.sleep(2)
        return await self._get_latest_response(entity)

    async def send_video(self, file_path: str, caption: str = "", peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a video."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        if not os.path.exists(file_path):
            raise Exception(f"Video file not found: {file_path}")

        await self.client.send_file(entity, file_path, caption=caption, supports_streaming=True)

        await asyncio.sleep(2)
        return await self._get_latest_response(entity)

    async def send_video_note(self, file_path: str, peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a video note (circle)."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        if not os.path.exists(file_path):
            raise Exception(f"Video file not found: {file_path}")

        await self.client.send_file(entity, file_path, video_note=True)

        await asyncio.sleep(2)
        return await self._get_latest_response(entity)

    async def get_messages(self, limit: int = 10, peer: Optional[str] = None) -> Dict[str, Any]:
        """Get the latest messages from the conversation."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)
        messages = await self.client.get_messages(entity, limit=limit)
        return self._format_messages(messages)

    async def get_keyboard(self, peer: Optional[str] = None) -> Dict[str, Any]:
        """Get the current keyboard buttons."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)
        messages = await self.client.get_messages(entity, limit=10)

        for msg in messages:
            if msg.out:
                continue

            keyboard_info = {"type": None, "buttons": []}

            if msg.reply_markup:
                if isinstance(msg.reply_markup, ReplyInlineMarkup):
                    keyboard_info["type"] = "inline"
                    for row in msg.reply_markup.rows:
                        row_buttons = []
                        for button in row.buttons:
                            if hasattr(button, "text"):
                                row_buttons.append(button.text)
                        if row_buttons:
                            keyboard_info["buttons"].append(row_buttons)

                elif isinstance(msg.reply_markup, ReplyKeyboardMarkup):
                    keyboard_info["type"] = "reply"
                    for row in msg.reply_markup.rows:
                        row_buttons = []
                        for button in row.buttons:
                            if hasattr(button, "text"):
                                row_buttons.append(button.text)
                        if row_buttons:
                            keyboard_info["buttons"].append(row_buttons)

                return keyboard_info

        return {"type": None, "buttons": []}

    async def wait_for_response(self, timeout: int = 30, peer: Optional[str] = None) -> Dict[str, Any]:
        """Wait for a new message from the peer."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        # Get current latest message id
        messages = await self.client.get_messages(entity, limit=1)
        last_id = messages[0].id if messages else 0

        # Poll for new messages
        for _ in range(timeout):
            await asyncio.sleep(1)
            messages = await self.client.get_messages(entity, limit=5)
            new_messages = [m for m in messages if m.id > last_id and not m.out]
            if new_messages:
                return self._format_messages(new_messages)

        return {"messages": [], "note": "Timeout waiting for response"}

    async def open_webapp(self, button_text: Optional[str] = None, peer: Optional[str] = None) -> Dict[str, Any]:
        """Open a WebApp and get its URL.

        Args:
            button_text: Text of the WebApp button to click. If None, finds first WebApp button.
        """
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        # Get latest messages to find WebApp button
        messages = await self.client.get_messages(entity, limit=10)

        for msg in messages:
            if msg.out:
                continue

            if not msg.reply_markup:
                continue

            # Search for WebApp button
            if isinstance(msg.reply_markup, ReplyKeyboardMarkup):
                for row in msg.reply_markup.rows:
                    for button in row.buttons:
                        if isinstance(button, (KeyboardButtonWebView, KeyboardButtonSimpleWebView)):
                            if button_text is None or button.text == button_text:
                                # Found WebApp button - request the web view
                                result = await self.client(
                                    RequestWebViewRequest(
                                        peer=entity,
                                        bot=entity,
                                        url=button.url,
                                        platform="android",
                                    )
                                )
                                return {
                                    "button_text": button.text,
                                    "url": result.url,
                                    "query_id": getattr(result, 'query_id', None),
                                }

            elif isinstance(msg.reply_markup, ReplyInlineMarkup):
                for row in msg.reply_markup.rows:
                    for button in row.buttons:
                        if isinstance(button, (KeyboardButtonWebView, KeyboardButtonSimpleWebView)):
                            if button_text is None or button.text == button_text:
                                result = await self.client(
                                    RequestWebViewRequest(
                                        peer=entity,
                                        bot=entity,
                                        url=button.url,
                                        platform="android",
                                    )
                                )
                                return {
                                    "button_text": button.text,
                                    "url": result.url,
                                    "query_id": getattr(result, 'query_id', None),
                                }

        return {"error": "No WebApp button found"}

    async def read_channel(
        self,
        channel: str,
        limit: int = 100,
        offset_id: int = 0,
    ) -> Dict[str, Any]:
        """Read messages from a channel/group with pagination.

        Args:
            channel: Channel/group username or invite link.
            limit: Number of messages to fetch (max 100 per call).
            offset_id: Fetch messages older than this message ID (0 = from newest).
        """
        if not self.client:
            raise Exception("Not connected")

        entity = await self.client.get_entity(channel)

        messages = await self.client.get_messages(
            entity,
            limit=min(limit, 100),
            offset_id=offset_id,
        )

        formatted = []
        for msg in messages:
            msg_data = {
                "id": msg.id,
                "date": msg.date.isoformat() if msg.date else None,
                "text": msg.text or "",
                "views": getattr(msg, "views", None),
                "forwards": getattr(msg, "forwards", None),
                "has_photo": bool(msg.photo),
                "has_video": bool(msg.video),
                "has_document": bool(msg.document),
            }
            if msg.sender:
                msg_data["sender"] = getattr(msg.sender, "title", None) or getattr(msg.sender, "first_name", None)
            formatted.append(msg_data)

        oldest_id = messages[-1].id if messages else None

        return {
            "channel": channel,
            "messages": formatted,
            "count": len(formatted),
            "oldest_id": oldest_id,
            "has_more": len(messages) == min(limit, 100),
        }

    async def _get_latest_response(self, entity=None) -> Dict[str, Any]:
        """Get the latest response from peer."""
        entity = entity or self.bot_entity
        messages = await self.client.get_messages(entity, limit=10)
        peer_messages = [m for m in messages if not m.out][:3]
        return self._format_messages(peer_messages)

    async def download_voice(
        self,
        peer: Optional[str] = None,
        message_id: Optional[int] = None,
        search_limit: int = 20,
    ) -> Dict[str, Any]:
        """Find a voice/video_note message and download it to a temp file.

        Args:
            peer: Peer to search in. If None, uses default.
            message_id: Specific message id. If None, finds the latest voice/video_note.
            search_limit: How many recent messages to scan when message_id is None.
        """
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        target_msg = None
        if message_id is not None:
            msgs = await self.client.get_messages(entity, ids=[message_id])
            if msgs and msgs[0]:
                target_msg = msgs[0]
        else:
            messages = await self.client.get_messages(entity, limit=search_limit)
            for msg in messages:
                if msg.voice or msg.video_note:
                    target_msg = msg
                    break

        if target_msg is None:
            raise Exception("No voice/video_note message found")

        if not (target_msg.voice or target_msg.video_note):
            raise Exception(f"Message {target_msg.id} is not a voice or video_note")

        file_path = await target_msg.download_media()

        return {
            "message_id": target_msg.id,
            "file_path": file_path,
            "is_video_note": bool(target_msg.video_note),
            "is_voice": bool(target_msg.voice),
            "duration": getattr(target_msg.media.document.attributes[0], "duration", None)
                if target_msg.media and hasattr(target_msg.media, "document") else None,
        }

    def _format_messages(self, messages: List[Message]) -> Dict[str, Any]:
        """Format messages for output."""
        formatted = []
        for msg in reversed(messages):  # Chronological order
            msg_data = {
                "id": msg.id,
                "text": msg.text or "",
                "from_bot": not msg.out,
                "has_keyboard": bool(msg.reply_markup),
                "has_photo": bool(msg.photo),
                "has_voice": bool(msg.voice),
                "has_document": bool(msg.document),
            }

            # Add keyboard info if present
            if msg.reply_markup:
                buttons = []
                if isinstance(msg.reply_markup, (ReplyInlineMarkup, ReplyKeyboardMarkup)):
                    for row in msg.reply_markup.rows:
                        row_buttons = []
                        for button in row.buttons:
                            if hasattr(button, "text"):
                                row_buttons.append(button.text)
                        if row_buttons:
                            buttons.append(row_buttons)
                msg_data["keyboard"] = buttons

            formatted.append(msg_data)

        return {"messages": formatted}
