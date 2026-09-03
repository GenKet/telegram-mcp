"""MCP Server for Telegram bot testing."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .client import TelegramTestClient
from .ai import AIHelper

# Load environment variables from project root
_project_root = Path(__file__).parent.parent.parent
_env_file = _project_root / ".env"
load_dotenv(_env_file)

# Initialize MCP server
server = Server("telegram-mcp")

# Global client instance
_client: TelegramTestClient | None = None
_connected: bool = False
_ai: AIHelper | None = None

_PEER_PROPERTY = {
    "type": "string",
    "description": "Username, t.me link or numeric id of the peer (user/bot/group/private group) to interact with. If not provided, uses the default peer set via telegram_set_bot.",
}


def get_ai() -> AIHelper:
    """Get or create the AI helper."""
    global _ai
    if _ai is None:
        _ai = AIHelper()
    return _ai


async def get_client() -> TelegramTestClient:
    """Get or create the Telegram client."""
    global _client, _connected

    if _client is None:
        api_id = os.getenv("API_ID")
        api_hash = os.getenv("API_HASH")
        phone = os.getenv("PHONE")
        session_string = os.getenv("SESSION_STRING")
        session_name = os.getenv("SESSION_NAME", "telegram_mcp_session")

        if not api_id or not api_hash:
            raise Exception(
                "API_ID and API_HASH must be set in environment variables. "
                "Get them from https://my.telegram.org/apps"
            )

        _client = TelegramTestClient(
            api_id=int(api_id),
            api_hash=api_hash,
            session_string=session_string,
            session_name=session_name,
            phone=phone,
        )

    # Check actual connection state, not just the flag
    is_actually_connected = (
        _client.client is not None
        and _client.client.is_connected()
    )

    if not is_actually_connected:
        await _client.connect()
        _connected = True

    return _client


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="telegram_connect",
            description="Connect to Telegram and optionally set the default peer (bot/user/group) to interact with",
            inputSchema={
                "type": "object",
                "properties": {
                    "bot_username": {
                        "type": "string",
                        "description": "Username of the default peer to interact with (without @). Optional — tools also accept a per-call 'peer' parameter.",
                    }
                },
            },
        ),
        Tool(
            name="telegram_send_message",
            description="Send a text message to a peer (bot, user, or group) and get the response",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Message text to send",
                    },
                    "peer": _PEER_PROPERTY,
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="telegram_click_button",
            description="Click a button on the keyboard (reply or inline)",
            inputSchema={
                "type": "object",
                "properties": {
                    "button_text": {
                        "type": "string",
                        "description": "Text of the button to click",
                    },
                    "peer": _PEER_PROPERTY,
                },
                "required": ["button_text"],
            },
        ),
        Tool(
            name="telegram_get_keyboard",
            description="Get the current keyboard buttons shown in the conversation",
            inputSchema={
                "type": "object",
                "properties": {
                    "peer": _PEER_PROPERTY,
                },
            },
        ),
        Tool(
            name="telegram_get_messages",
            description="Get recent messages from the conversation with a peer (bot, user, or group). Media is auto-downloaded by default; pass download_media=false to skip.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": ["integer", "string"],
                        "description": "Number of messages to retrieve (default: 10)",
                        "default": 10,
                    },
                    "download_media": {
                        "type": "boolean",
                        "description": "Download all media in returned messages to /tmp/tg_mcp_media and include 'media_path' per message (default: true)",
                        "default": True,
                    },
                    "peer": _PEER_PROPERTY,
                },
            },
        ),
        Tool(
            name="telegram_send_voice",
            description="Send a voice message to a peer",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the voice file (ogg/mp3/wav)",
                    },
                    "peer": _PEER_PROPERTY,
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="telegram_wait_response",
            description="Wait for a new response from a peer",
            inputSchema={
                "type": "object",
                "properties": {
                    "timeout": {
                        "type": ["integer", "string"],
                        "description": "Timeout in seconds (default: 30)",
                        "default": 30,
                    },
                    "peer": _PEER_PROPERTY,
                },
            },
        ),
        Tool(
            name="telegram_set_bot",
            description="Set the default peer (bot/user/group) for all subsequent operations",
            inputSchema={
                "type": "object",
                "properties": {
                    "bot_username": {
                        "type": "string",
                        "description": "Username of the peer (without @)",
                    }
                },
                "required": ["bot_username"],
            },
        ),
        Tool(
            name="telegram_get_session_string",
            description="Get the current session string for storage (useful after first auth)",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="telegram_open_webapp",
            description="Open a WebApp button and get the URL with init data. Use this to test Mini Apps.",
            inputSchema={
                "type": "object",
                "properties": {
                    "button_text": {
                        "type": "string",
                        "description": "Text of the WebApp button to click. If not provided, clicks the first WebApp button found.",
                    },
                    "peer": _PEER_PROPERTY,
                },
            },
        ),
        Tool(
            name="telegram_send_photo",
            description="Send a photo to a peer",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the photo file (jpg/png/webp)",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional caption for the photo",
                        "default": "",
                    },
                    "peer": _PEER_PROPERTY,
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="telegram_send_file",
            description="Send a file/document to a peer",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to send",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional caption for the file",
                        "default": "",
                    },
                    "peer": _PEER_PROPERTY,
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="telegram_send_video",
            description="Send a video to a peer",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the video file (mp4/mov/avi)",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional caption for the video",
                        "default": "",
                    },
                    "peer": _PEER_PROPERTY,
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="telegram_send_video_note",
            description="Send a video note (circle/кружок) to a peer",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the video file for the circle message (mp4, should be square)",
                    },
                    "peer": _PEER_PROPERTY,
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="telegram_ai_send_message",
            description="Generate a text message via GPT from a prompt and send it to a peer",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Prompt for GPT describing what the message should say",
                    },
                    "system": {
                        "type": "string",
                        "description": "Optional system prompt to set tone/style",
                    },
                    "model": {
                        "type": "string",
                        "description": "OpenAI chat model (default: gpt-4o-mini)",
                    },
                    "peer": _PEER_PROPERTY,
                },
                "required": ["prompt"],
            },
        ),
        Tool(
            name="telegram_ai_send_voice",
            description="Generate a voice message via OpenAI TTS from text (or from a GPT prompt) and send it to a peer",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to speak. Either 'text' or 'prompt' must be provided.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Optional prompt for GPT to generate the text first, then synthesize it. Either 'text' or 'prompt' must be provided.",
                    },
                    "voice": {
                        "type": "string",
                        "description": "TTS voice (alloy, echo, fable, onyx, nova, shimmer). Default: alloy",
                    },
                    "model": {
                        "type": "string",
                        "description": "TTS model (default: tts-1)",
                    },
                    "system": {
                        "type": "string",
                        "description": "Optional system prompt when using 'prompt' mode",
                    },
                    "peer": _PEER_PROPERTY,
                },
            },
        ),
        Tool(
            name="telegram_transcribe_voice",
            description="Download a voice message or video note from the conversation and transcribe it via Whisper",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": ["integer", "string"],
                        "description": "Specific message id to transcribe. If omitted, finds the latest voice/video_note in the conversation.",
                    },
                    "language": {
                        "type": "string",
                        "description": "Optional ISO language code hint (e.g. 'ru', 'en') to improve accuracy",
                    },
                    "peer": _PEER_PROPERTY,
                },
            },
        ),
        Tool(
            name="telegram_analyze_voice",
            description="Transcribe a voice/video_note via Whisper and analyze the content with GPT based on a prompt",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Question/instruction for GPT about the transcript (e.g. 'summarize', 'extract action items', 'identify sentiment')",
                        "default": "Summarize the content of this voice message and highlight the key points.",
                    },
                    "message_id": {
                        "type": ["integer", "string"],
                        "description": "Specific message id. If omitted, finds the latest voice/video_note.",
                    },
                    "language": {
                        "type": "string",
                        "description": "Optional ISO language code hint for transcription",
                    },
                    "model": {
                        "type": "string",
                        "description": "OpenAI chat model for analysis (default: gpt-4o-mini)",
                    },
                    "peer": _PEER_PROPERTY,
                },
            },
        ),
        Tool(
            name="telegram_read_channel",
            description=(
                "Read posts/messages from a Telegram channel, group, or user chat. "
                "Supports pagination via offset_id — call repeatedly with oldest_id from previous result to read the full history. "
                "Media is auto-downloaded by default; pass download_media=false to skip."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel, group or user: username ('durov'), t.me link, or numeric id for private groups/channels (e.g. '2156914166' or '-1002156914166')",
                    },
                    "limit": {
                        "type": ["integer", "string"],
                        "description": "Number of messages to fetch, max 100 per call (default: 100)",
                        "default": 100,
                    },
                    "offset_id": {
                        "type": ["integer", "string"],
                        "description": "Fetch messages older than this ID. Use oldest_id from previous response for pagination. 0 = start from newest.",
                        "default": 0,
                    },
                    "download_media": {
                        "type": "boolean",
                        "description": "Download all media (photos/videos/docs/voice) to /tmp/tg_mcp_media and include 'media_path' per message (default: true)",
                        "default": True,
                    },
                },
                "required": ["channel"],
            },
        ),
        Tool(
            name="telegram_download_media",
            description="Download media of a specific message by its id. Returns the local file path so it can be read/viewed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": ["integer", "string"],
                        "description": "ID of the message whose media should be downloaded",
                    },
                    "peer": _PEER_PROPERTY,
                },
                "required": ["message_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    try:
        client = await get_client()
        peer = arguments.get("peer")

        if name == "telegram_connect":
            bot_username = arguments.get("bot_username")
            if bot_username:
                await client.set_bot(bot_username)
                result = {"status": "connected", "peer": bot_username}
            else:
                result = {
                    "status": "connected",
                    "peer": None,
                    "note": "No default peer set. Pass 'peer' to individual tools or use telegram_set_bot.",
                }

        elif name == "telegram_send_message":
            text = arguments["text"]
            result = await client.send_message(text, peer=peer)

        elif name == "telegram_click_button":
            button_text = arguments["button_text"]
            result = await client.click_button(button_text, peer=peer)

        elif name == "telegram_get_keyboard":
            result = await client.get_keyboard(peer=peer)

        elif name == "telegram_get_messages":
            limit = int(arguments.get("limit", 10))
            download_media = bool(arguments.get("download_media", True))
            result = await client.get_messages(limit, peer=peer, download_media=download_media)

        elif name == "telegram_send_voice":
            file_path = arguments["file_path"]
            result = await client.send_voice(file_path, peer=peer)

        elif name == "telegram_wait_response":
            timeout = int(arguments.get("timeout", 30))
            result = await client.wait_for_response(timeout, peer=peer)

        elif name == "telegram_set_bot":
            bot_username = arguments["bot_username"]
            await client.set_bot(bot_username)
            result = {"status": "ok", "bot": bot_username}

        elif name == "telegram_get_session_string":
            session_string = client.get_session_string()
            result = {"session_string": session_string}

        elif name == "telegram_open_webapp":
            button_text = arguments.get("button_text")
            result = await client.open_webapp(button_text, peer=peer)

        elif name == "telegram_send_file":
            file_path = arguments["file_path"]
            caption = arguments.get("caption", "")
            result = await client.send_file(file_path, caption=caption, peer=peer)

        elif name == "telegram_send_video":
            file_path = arguments["file_path"]
            caption = arguments.get("caption", "")
            result = await client.send_video(file_path, caption=caption, peer=peer)

        elif name == "telegram_send_video_note":
            file_path = arguments["file_path"]
            result = await client.send_video_note(file_path, peer=peer)

        elif name == "telegram_send_photo":
            file_path = arguments["file_path"]
            caption = arguments.get("caption", "")
            result = await client.send_photo(file_path, caption=caption, peer=peer)

        elif name == "telegram_read_channel":
            channel = arguments["channel"]
            limit = int(arguments.get("limit", 100))
            offset_id = int(arguments.get("offset_id", 0))
            download_media = bool(arguments.get("download_media", True))
            result = await client.read_channel(
                channel, limit=limit, offset_id=offset_id, download_media=download_media,
            )

        elif name == "telegram_download_media":
            message_id = int(arguments["message_id"])
            result = await client.download_message(message_id, peer=peer)

        elif name == "telegram_ai_send_message":
            ai = get_ai()
            text = await ai.generate_text(
                prompt=arguments["prompt"],
                system=arguments.get("system"),
                model=arguments.get("model"),
            )
            send_result = await client.send_message(text, peer=peer)
            result = {"generated_text": text, "response": send_result}

        elif name == "telegram_ai_send_voice":
            ai = get_ai()
            text = arguments.get("text")
            prompt = arguments.get("prompt")
            if not text and not prompt:
                raise Exception("Either 'text' or 'prompt' must be provided")
            if not text:
                text = await ai.generate_text(
                    prompt=prompt,
                    system=arguments.get("system"),
                )
            voice_path = await ai.generate_voice(
                text=text,
                voice=arguments.get("voice"),
                model=arguments.get("model"),
            )
            try:
                send_result = await client.send_voice(voice_path, peer=peer)
            finally:
                if os.path.exists(voice_path):
                    os.unlink(voice_path)
            result = {"generated_text": text, "response": send_result}

        elif name == "telegram_transcribe_voice":
            ai = get_ai()
            message_id = arguments.get("message_id")
            if message_id is not None:
                message_id = int(message_id)
            download = await client.download_voice(peer=peer, message_id=message_id)
            file_path = download["file_path"]
            try:
                transcript = await ai.transcribe_audio(
                    file_path=file_path,
                    language=arguments.get("language"),
                )
            finally:
                if file_path and os.path.exists(file_path):
                    os.unlink(file_path)
            result = {
                "message_id": download["message_id"],
                "is_voice": download["is_voice"],
                "is_video_note": download["is_video_note"],
                "transcript": transcript,
            }

        elif name == "telegram_analyze_voice":
            ai = get_ai()
            message_id = arguments.get("message_id")
            if message_id is not None:
                message_id = int(message_id)
            download = await client.download_voice(peer=peer, message_id=message_id)
            file_path = download["file_path"]
            try:
                transcript = await ai.transcribe_audio(
                    file_path=file_path,
                    language=arguments.get("language"),
                )
            finally:
                if file_path and os.path.exists(file_path):
                    os.unlink(file_path)
            analysis_prompt = arguments.get(
                "prompt",
                "Summarize the content of this voice message and highlight the key points.",
            )
            analysis = await ai.generate_text(
                prompt=f"{analysis_prompt}\n\nTranscript:\n{transcript}",
                model=arguments.get("model"),
            )
            result = {
                "message_id": download["message_id"],
                "transcript": transcript,
                "analysis": analysis,
            }

        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    except Exception as e:
        error_result = {"error": str(e)}
        return [TextContent(type="text", text=json.dumps(error_result, ensure_ascii=False, indent=2))]


def main():
    """Run the MCP server."""
    asyncio.run(run_server())


async def run_server():
    """Async server runner."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    main()
