"""Telegram client wrapper using Telethon with StringSession support."""

import asyncio
import json
import os
from datetime import datetime, timezone
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
    PeerChannel,
    PeerChat,
    PeerUser,
)
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest, RequestWebViewRequest

# Apply patch before importing TelegramClient
from .patch import telegrambaseclient  # noqa: F401 - patches TelegramClient on import
from telethon import TelegramClient, events

# Default response timeout (seconds) — how long we wait for a bot/user reply after sending
DEFAULT_RESPONSE_TIMEOUT = 8.0

# Directory for downloaded media (auto-created on first use)
MEDIA_DIR = "/tmp/tg_mcp_media"

# Directory for chat exports (auto-created on first use)
EXPORT_DIR = "/tmp/tg_mcp_export"


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
        self._entity_cache: Dict[str, Any] = {}

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
            self._entity_cache[self.bot_username.lower()] = self.bot_entity
            return True
        except Exception as e:
            raise Exception(f"Failed to find @{bot_username}: {e}")

    async def _resolve_peer(self, peer: Optional[str] = None):
        """Resolve peer entity. Uses provided peer or falls back to default bot_entity. Cached."""
        if peer is None:
            if not self.bot_entity:
                raise Exception("No default peer set. Use telegram_set_bot or provide 'peer' parameter.")
            return self.bot_entity
        if not self.client:
            raise Exception("Not connected")
        key = peer.lstrip("@").lower()
        if key in self._entity_cache:
            return self._entity_cache[key]
        try:
            entity = await self._resolve_identifier(peer)
        except Exception as e:
            raise Exception(f"Failed to find peer {peer}: {e}")
        self._entity_cache[key] = entity
        return entity

    async def _resolve_identifier(self, ident: str):
        """Resolve a peer by username, t.me link or numeric id.

        A bare numeric id (with optional -100 prefix) is tried as channel/supergroup,
        then legacy group, then user — get_entity() alone reads a plain int as a user id,
        so private groups referenced by id would never be found.
        """
        cleaned = ident.strip().lstrip("@")
        digits = cleaned[4:] if cleaned.startswith("-100") else cleaned.lstrip("-")
        if not digits.isdigit():
            return await self.client.get_entity(cleaned)

        peer_id = int(digits)
        errors = []
        for peer_type in (PeerChannel, PeerChat, PeerUser):
            try:
                return await self.client.get_entity(peer_type(peer_id))
            except Exception as e:
                errors.append(f"{peer_type.__name__}: {e}")

        # Last resort: scan dialogs — works for peers whose access_hash we cannot guess
        async for dialog in self.client.iter_dialogs():
            if dialog.entity.id == peer_id:
                return dialog.entity

        raise Exception(f"id {peer_id} not resolvable ({'; '.join(errors)})")

    async def _await_response(
        self,
        entity,
        last_id: int,
        timeout: float = DEFAULT_RESPONSE_TIMEOUT,
    ) -> List[Message]:
        """Wait for new incoming messages from `entity` with id > last_id, via NewMessage event.

        Returns the collected messages once the bot stops sending (200ms quiet window)
        or when timeout is hit. Returns empty list on timeout with no messages.
        """
        received: List[Message] = []
        new_msg_event = asyncio.Event()

        try:
            peer_id = entity.id
        except AttributeError:
            peer_id = None

        async def handler(event):
            msg = event.message
            if msg.out:
                return
            if peer_id is not None:
                chat_id = getattr(event, "chat_id", None)
                if chat_id is not None and chat_id != peer_id:
                    return
            if msg.id <= last_id:
                return
            received.append(msg)
            new_msg_event.set()

        self.client.add_event_handler(handler, events.NewMessage(incoming=True))

        try:
            # Quick check: maybe response already arrived between send and handler registration
            recent = await self.client.get_messages(entity, limit=3)
            for m in recent:
                if not m.out and m.id > last_id and not any(r.id == m.id for r in received):
                    received.append(m)
            if received:
                new_msg_event.set()

            try:
                await asyncio.wait_for(new_msg_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return received

            # Got first message — give bot up to 600ms more for follow-up messages
            quiet_window = 0.6
            while True:
                new_msg_event.clear()
                try:
                    await asyncio.wait_for(new_msg_event.wait(), timeout=quiet_window)
                except asyncio.TimeoutError:
                    break
            return received
        finally:
            self.client.remove_event_handler(handler)

    async def send_message(self, text: str, peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a text message and wait for response."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        sent = await self.client.send_message(entity, text)
        responses = await self._await_response(entity, last_id=sent.id)
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
                                latest = await self.client.get_messages(entity, limit=1)
                                last_id = latest[0].id if latest else 0
                                await self.client(
                                    GetBotCallbackAnswerRequest(
                                        peer=entity,
                                        msg_id=msg.id,
                                        data=button.data,
                                    )
                                )
                                responses = await self._await_response(entity, last_id=last_id)
                                return self._format_messages(responses)

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

        sent = await self.client.send_file(entity, file_path, voice_note=True)
        responses = await self._await_response(entity, last_id=sent.id)
        return self._format_messages(responses)

    async def send_photo(self, file_path: str, caption: str = "", peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a photo."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        if not os.path.exists(file_path):
            raise Exception(f"Photo file not found: {file_path}")

        sent = await self.client.send_file(entity, file_path, caption=caption)
        responses = await self._await_response(entity, last_id=sent.id)
        return self._format_messages(responses)

    async def send_file(self, file_path: str, caption: str = "", peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a file/document."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        if not os.path.exists(file_path):
            raise Exception(f"File not found: {file_path}")

        sent = await self.client.send_file(entity, file_path, caption=caption, force_document=True)
        responses = await self._await_response(entity, last_id=sent.id)
        return self._format_messages(responses)

    async def send_video(self, file_path: str, caption: str = "", peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a video."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        if not os.path.exists(file_path):
            raise Exception(f"Video file not found: {file_path}")

        sent = await self.client.send_file(entity, file_path, caption=caption, supports_streaming=True)
        responses = await self._await_response(entity, last_id=sent.id)
        return self._format_messages(responses)

    async def send_video_note(self, file_path: str, peer: Optional[str] = None) -> Dict[str, Any]:
        """Send a video note (circle)."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)

        if not os.path.exists(file_path):
            raise Exception(f"Video file not found: {file_path}")

        sent = await self.client.send_file(entity, file_path, video_note=True)
        responses = await self._await_response(entity, last_id=sent.id)
        return self._format_messages(responses)

    async def get_messages(
        self,
        limit: int = 10,
        peer: Optional[str] = None,
        download_media: bool = True,
    ) -> Dict[str, Any]:
        """Get the latest messages from the conversation."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_peer(peer)
        messages = await self.client.get_messages(entity, limit=limit)
        media_paths = await self._bulk_download(messages) if download_media else None
        return self._format_messages(messages, media_paths=media_paths)

    async def _bulk_download(self, messages: List[Message]) -> Dict[int, str]:
        """Download media for all messages in parallel. Returns {msg_id: path}."""
        targets = [m for m in messages if self._media_type(m)]
        if not targets:
            return {}
        paths = await asyncio.gather(
            *(self._download_message_media(m) for m in targets),
            return_exceptions=True,
        )
        return {
            m.id: p for m, p in zip(targets, paths)
            if isinstance(p, str) and p
        }

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

        messages = await self.client.get_messages(entity, limit=1)
        last_id = messages[0].id if messages else 0

        responses = await self._await_response(entity, last_id=last_id, timeout=float(timeout))
        if not responses:
            return {"messages": [], "note": "Timeout waiting for response"}
        return self._format_messages(responses)

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

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[datetime]:
        """Parse an ISO date/datetime ('2026-09-02', '2026-09-02T18:00') into aware UTC."""
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _history_item(self, msg: Message) -> Dict[str, Any]:
        """Common message shape for history reads, exports and digests."""
        item = {
            "id": msg.id,
            "date": msg.date.isoformat() if msg.date else None,
            "text": msg.text or "",
        }
        if msg.sender:
            item["sender"] = getattr(msg.sender, "title", None) or getattr(msg.sender, "first_name", None)
        media_type = self._media_type(msg)
        if media_type:
            item["media_type"] = media_type
        return item

    async def _iter_history(
        self,
        entity,
        limit: Optional[int] = None,
        offset_id: int = 0,
        topic_id: Optional[int] = None,
        min_date: Optional[datetime] = None,
        max_date: Optional[datetime] = None,
    ):
        """Yield messages newest-first, honouring a date window and a forum topic."""
        kwargs: Dict[str, Any] = {}
        if offset_id:
            kwargs["offset_id"] = offset_id
        if max_date:
            kwargs["offset_date"] = max_date
        if topic_id:
            kwargs["reply_to"] = topic_id

        async for msg in self.client.iter_messages(entity, limit=limit, **kwargs):
            if min_date and msg.date and msg.date < min_date:
                return
            yield msg

    async def list_topics(self, peer: str, limit: int = 100) -> Dict[str, Any]:
        """List forum topics of a supergroup, so a single topic can be read on its own."""
        if not self.client:
            raise Exception("Not connected")

        from telethon.tl.functions.messages import GetForumTopicsRequest

        entity = await self._resolve_identifier(peer)
        info = self._describe_entity(entity)
        if not getattr(entity, "forum", False):
            return {**info, "is_forum": False, "topics": [], "note": "not a forum — read it as a plain chat"}

        found = await self.client(
            GetForumTopicsRequest(
                peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=min(limit, 100)
            )
        )
        topics = [
            {
                "id": topic.id,
                "title": topic.title,
                "closed": getattr(topic, "closed", False),
                "top_message_id": getattr(topic, "top_message", None),
            }
            for topic in found.topics
            if hasattr(topic, "title")
        ]
        topics = topics[: min(limit, 100)]
        return {**info, "is_forum": True, "topics": topics, "count": len(topics)}

    async def collect_messages(
        self,
        peer: Optional[str] = None,
        limit: int = 200,
        topic_id: Optional[int] = None,
        min_date: Optional[str] = None,
        max_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Collect messages (oldest-first) for further processing — digests, analysis."""
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_identifier(peer) if peer else await self._resolve_peer()
        items = [
            self._history_item(msg)
            async for msg in self._iter_history(
                entity,
                limit=limit,
                topic_id=topic_id,
                min_date=self._parse_date(min_date),
                max_date=self._parse_date(max_date),
            )
        ]
        items.reverse()
        return items

    async def export_chat(
        self,
        peer: str,
        out_path: Optional[str] = None,
        fmt: str = "jsonl",
        limit: Optional[int] = None,
        topic_id: Optional[int] = None,
        min_date: Optional[str] = None,
        max_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Stream a chat's history into a local file and return stats, not the messages.

        Keeps whole-history dumps out of the model context: the file is written
        message by message, so a 16k-message chat costs one short response.
        """
        if not self.client:
            raise Exception("Not connected")
        if fmt not in ("jsonl", "md"):
            raise Exception("fmt must be 'jsonl' or 'md'")

        entity = await self._resolve_identifier(peer)
        info = self._describe_entity(entity)

        if out_path is None:
            os.makedirs(EXPORT_DIR, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            out_path = os.path.join(EXPORT_DIR, f"chat_{entity.id}_{stamp}.{fmt}")

        count = 0
        newest_date = oldest_date = None
        oldest_id = None

        with open(out_path, "w", encoding="utf-8") as handle:
            if fmt == "md":
                handle.write(f"# {info['name'] or peer} (id {entity.id})\n\n")
            async for msg in self._iter_history(
                entity,
                limit=limit,
                topic_id=topic_id,
                min_date=self._parse_date(min_date),
                max_date=self._parse_date(max_date),
            ):
                item = self._history_item(msg)
                if fmt == "jsonl":
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                else:
                    handle.write(
                        f"**{item.get('sender') or 'unknown'}** · {item['date']}"
                        f"{' · [' + item['media_type'] + ']' if item.get('media_type') else ''}\n"
                        f"{item['text']}\n\n"
                    )
                count += 1
                newest_date = newest_date or item["date"]
                oldest_date = item["date"]
                oldest_id = item["id"]

        return {
            "peer": info,
            "path": out_path,
            "format": fmt,
            "count": count,
            "newest_date": newest_date,
            "oldest_date": oldest_date,
            "oldest_id": oldest_id,
            "size_bytes": os.path.getsize(out_path),
        }

    async def read_channel(
        self,
        channel: str,
        limit: int = 100,
        offset_id: int = 0,
        download_media: bool = True,
        topic_id: Optional[int] = None,
        min_date: Optional[str] = None,
        max_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read messages from a channel/group with pagination.

        Args:
            channel: Channel/group username, invite link or numeric id (e.g. 2156914166 / -1002156914166).
            limit: Number of messages to fetch (max 100 per call).
            offset_id: Fetch messages older than this message ID (0 = from newest).
            download_media: If True, download all media (photos/videos/docs/voice) to MEDIA_DIR
                and include local file paths in the response.
            topic_id: Read only this forum topic (see list_topics).
            min_date: Stop at messages older than this ISO date/datetime.
            max_date: Start from messages older than this ISO date/datetime.
        """
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_identifier(channel)
        capped = min(limit, 100)

        messages = [
            msg
            async for msg in self._iter_history(
                entity,
                limit=capped,
                offset_id=offset_id,
                topic_id=topic_id,
                min_date=self._parse_date(min_date),
                max_date=self._parse_date(max_date),
            )
        ]

        media_paths = await self._bulk_download(messages) if download_media else {}

        formatted = []
        for msg in messages:
            item = self._history_item(msg)
            item.update(
                {
                    "views": getattr(msg, "views", None),
                    "forwards": getattr(msg, "forwards", None),
                    "has_photo": bool(msg.photo),
                    "has_video": bool(msg.video),
                    "has_document": bool(msg.document),
                }
            )
            if msg.id in media_paths:
                item["media_path"] = media_paths[msg.id]
            formatted.append(item)

        return {
            "channel": channel,
            "messages": formatted,
            "count": len(formatted),
            "oldest_id": messages[-1].id if messages else None,
            "has_more": len(messages) == capped,
        }

    def _describe_entity(self, entity) -> Dict[str, Any]:
        """Short peer description: id, type, title/name, username."""
        kind = type(entity).__name__
        if kind == "Channel":
            kind = "supergroup" if getattr(entity, "megagroup", False) else "channel"
        elif kind == "Chat":
            kind = "group"
        elif kind == "User":
            kind = "bot" if getattr(entity, "bot", False) else "user"
        name = getattr(entity, "title", None) or " ".join(
            p for p in (getattr(entity, "first_name", None), getattr(entity, "last_name", None)) if p
        )
        return {
            "id": entity.id,
            "type": kind,
            "name": name or None,
            "username": getattr(entity, "username", None),
        }

    async def find_chat(self, query: str = "", limit: int = 20) -> Dict[str, Any]:
        """Find chats by a name/username substring, or list recent dialogs when query is empty.

        Scans the dialog list (case-insensitive substring match) and, for a non-empty query,
        also asks the server for public matches — so peers you are not a member of show up too.
        """
        if not self.client:
            raise Exception("Not connected")

        needle = query.strip().lstrip("@").lower()
        results, seen = [], set()

        async for dialog in self.client.iter_dialogs():
            if needle:
                haystack = f"{dialog.name or ''} {getattr(dialog.entity, 'username', '') or ''}".lower()
                if needle not in haystack:
                    continue
            info = self._describe_entity(dialog.entity)
            info["in_dialogs"] = True
            info["last_message_date"] = dialog.date.isoformat() if dialog.date else None
            results.append(info)
            seen.add(dialog.entity.id)
            if not needle and len(results) >= limit:
                break

        if needle:
            from telethon.tl.functions.contacts import SearchRequest

            try:
                found = await self.client(SearchRequest(q=query.strip().lstrip("@"), limit=limit))
                for entity in list(found.users) + list(found.chats):
                    if entity.id in seen:
                        continue
                    info = self._describe_entity(entity)
                    info["in_dialogs"] = False
                    results.append(info)
                    seen.add(entity.id)
            except Exception as e:
                results.append({"warning": f"global search failed: {e}"})

        return {"query": query, "results": results[:limit], "count": min(len(results), limit)}

    async def search_messages(
        self,
        query: str,
        peer: Optional[str] = None,
        limit: int = 50,
        offset_id: int = 0,
        from_user: Optional[str] = None,
        topic_id: Optional[int] = None,
        min_date: Optional[str] = None,
        max_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Server-side message search inside one peer, or across all chats when peer is None.

        Args:
            query: Text to search for.
            peer: Where to search — username, link or numeric id. None = global search.
            limit: Max messages to return (max 100).
            offset_id: Return messages older than this id (pagination).
            from_user: Only messages from this sender (per-peer search only).
            topic_id: Restrict to one forum topic (see list_topics).
            min_date: Ignore messages older than this ISO date/datetime.
            max_date: Start from messages older than this ISO date/datetime.
        """
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_identifier(peer) if peer else None
        sender = await self._resolve_identifier(from_user) if from_user else None

        floor = self._parse_date(min_date)
        ceiling = self._parse_date(max_date)

        kwargs = {"search": query, "limit": min(limit, 100)}
        if offset_id:
            kwargs["offset_id"] = offset_id
        if sender is not None:
            kwargs["from_user"] = sender
        if topic_id:
            kwargs["reply_to"] = topic_id
        if ceiling:
            kwargs["offset_date"] = ceiling

        messages = []
        async for msg in self.client.iter_messages(entity, **kwargs):
            if floor and msg.date and msg.date < floor:
                break
            item = self._history_item(msg)
            if entity is None and msg.chat:
                item["chat"] = getattr(msg.chat, "title", None) or getattr(msg.chat, "first_name", None)
                item["chat_id"] = msg.chat.id
            messages.append(item)

        return {
            "query": query,
            "peer": peer or "global",
            "messages": messages,
            "count": len(messages),
            "oldest_id": messages[-1]["id"] if messages else None,
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

    def _media_type(self, msg: Message) -> Optional[str]:
        """Identify the dominant media type of a message."""
        if msg.photo:
            return "photo"
        if msg.video:
            return "video"
        if msg.video_note:
            return "video_note"
        if msg.voice:
            return "voice"
        if msg.audio:
            return "audio"
        if msg.sticker:
            return "sticker"
        if msg.gif:
            return "gif"
        if msg.document:
            return "document"
        return None

    async def _download_message_media(self, msg: Message) -> Optional[str]:
        """Download media from a message into MEDIA_DIR. Returns absolute path or None.

        Files are named msg_<id>.<ext> so ordering matches message ordering.
        """
        if not (msg.photo or msg.video or msg.video_note or msg.voice
                or msg.audio or msg.document):
            return None
        os.makedirs(MEDIA_DIR, exist_ok=True)
        path = await msg.download_media(file=os.path.join(MEDIA_DIR, f"msg_{msg.id}"))
        return path

    async def download_message(
        self,
        message_id: int,
        peer: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Download media of a specific message by id."""
        if not self.client:
            raise Exception("Not connected")
        entity = await self._resolve_peer(peer)
        msgs = await self.client.get_messages(entity, ids=[message_id])
        if not msgs or not msgs[0]:
            raise Exception(f"Message {message_id} not found")
        msg = msgs[0]
        media_type = self._media_type(msg)
        if media_type is None:
            return {"message_id": msg.id, "media_type": None, "media_path": None, "note": "Message has no media"}
        path = await self._download_message_media(msg)
        return {"message_id": msg.id, "media_type": media_type, "media_path": path}

    def _format_messages(
        self,
        messages: List[Message],
        media_paths: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        """Format messages for output. media_paths: optional {msg_id: file_path}."""
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

            media_type = self._media_type(msg)
            if media_type:
                msg_data["media_type"] = media_type
            if media_paths and msg.id in media_paths:
                msg_data["media_path"] = media_paths[msg.id]

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
