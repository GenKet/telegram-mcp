# Telegram MCP Server

An MCP (Model Context Protocol) server that lets Claude Code interact with Telegram — send and receive messages, automate bots, and run AI workflows on top of your chats.

## Features

- Send text/media (voice, photo, video, file, video note/circle) to any peer (bot, user, group) via an optional `peer` parameter — no need to switch a default peer
- Click inline / reply keyboard buttons
- Read conversation history, keyboards, and channel/group/user chat history with pagination
- **Auto-download media** in incoming messages (photos, videos, voice, video notes, documents) to `/tmp/tg_mcp_media/msg_<id>.<ext>` so the model can directly read/view them
- Open Telegram Mini Apps (WebApp) and retrieve URL + init data
- **AI tools (OpenAI)**: GPT text generation, TTS voice synthesis, Whisper transcription, GPT analysis of voice content
- Event-driven response waiting — no fixed sleeps, replies are returned the moment the peer answers
- StringSession support (recommended) for portable, env-friendly auth

## Installation

### 1. Clone and install dependencies

```bash
git clone https://github.com/Kontora-studios/telegram-mcp.git
cd telegram-mcp
poetry install
```

### 2. Get Telegram API credentials

1. Go to https://my.telegram.org/apps
2. Sign in with your phone number
3. Create a new app (or use an existing one)
4. Copy `api_id` and `api_hash`

### 3. Set up environment

Create a `.env` file in the project root:

```env
API_ID=12345678
API_HASH=your_api_hash_here
PHONE=+1234567890

# Optional — required only for AI tools:
OPENAI_API_KEY=sk-...
```

### 4. Authorize (obtain SESSION_STRING)

Interactive flow — asks for the code on stdin:

```bash
poetry run telegram-mcp-auth
```

Step-by-step flow — each step is its own process, so it works from scripts and agents
(no interactive stdin needed):

```bash
poetry run telegram-mcp-auth request              # sends the login code
poetry run telegram-mcp-auth code 12345           # submits it
poetry run telegram-mcp-auth password secret      # only if the account has 2FA
poetry run telegram-mcp-auth status               # is the current session still alive?
```

The code arrives in the Telegram app itself when the account has another active session,
otherwise by SMS. Both flows write the new `SESSION_STRING` into `.env` **and** into the
`telegram` MCP server entry of `~/.claude.json` (pass `--no-claude-config` to skip the
latter), so afterwards you only need to reconnect the server with `/mcp` in Claude Code.

**Why StringSession:**
- No session file on disk
- Safe to store in environment variables
- Easy to move between machines

### 5. Configure Claude Code

Add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "telegram": {
      "command": "poetry",
      "args": ["run", "telegram-mcp"],
      "cwd": "/path/to/telegram-mcp",
      "env": {
        "API_ID": "your_api_id",
        "API_HASH": "your_api_hash",
        "SESSION_STRING": "your_session_string",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

Or use a `.env` file (recommended for security).

## Usage

Once configured, the following tools are available in Claude Code.

### Messaging

Every messaging tool accepts an optional `peer` parameter — a username (without `@`) or identifier of any user, bot, or group. If omitted, the default peer set via `telegram_set_bot` is used.

#### `telegram_connect`
Connect to Telegram and optionally set a default peer.

#### `telegram_set_bot`
Set the default peer (bot / user / group) for subsequent operations.
```
bot_username: "some_bot"
```

#### `telegram_send_message`
Send a text message and get the response.
```
text: "Hello"
peer: "some_user"   // optional
```

#### `telegram_click_button`
Click a button on the reply or inline keyboard.
```
button_text: "Subscribe"
```

#### `telegram_get_keyboard`
Return the keyboard currently shown in the chat.
```
→ {"type": "reply", "buttons": [["Read", "Write"], ["Listen"]]}
```

#### `telegram_get_messages`
Fetch the latest messages from a conversation. Media is auto-downloaded by default.
```
limit: 5
peer: "some_user"           // optional
download_media: true        // default true — set false to skip media downloads
```
Response includes `media_path` for every message that has a photo/video/voice/document/video_note/audio. Files are saved as `/tmp/tg_mcp_media/msg_<id>.<ext>`.

#### `telegram_send_voice` / `telegram_send_photo` / `telegram_send_video` / `telegram_send_video_note` / `telegram_send_file`
Send media files.
```
file_path: "/path/to/file"
caption: "optional caption"
```

#### `telegram_wait_response`
Wait for a new message from a peer.
```
timeout: 60
```

#### `telegram_open_webapp`
Open a Telegram Mini App (WebApp) button and return the URL with init data.
```
button_text: "Open app"   // optional — first WebApp button if omitted
```

#### `telegram_read_channel`
Read history from a channel, group, or user chat with pagination. Media is auto-downloaded by default.
```
channel: "durov"        // username, t.me link, or numeric id: 2156914166 / -1002156914166
limit: 100
offset_id: 0            // use oldest_id from previous response for pagination
download_media: true    // default true — set false to skip media downloads
topic_id: 3             // optional — read a single forum topic
min_date: "2026-09-02T18:00"   // optional date window
max_date: "2026-09-02T23:00"
```
Private groups and channels work by numeric id as long as the account is a member — no
`access_hash` needed. The same is true of the `peer` parameter of every other tool.

#### `telegram_find_chat`
Find chats by a name/username substring and get their numeric ids. Empty query lists recent dialogs.
```
query: "weekly"    // matches your dialogs and public peers you are not a member of
limit: 20
```

#### `telegram_search_messages`
Server-side text search in one chat, or across all chats when `peer` is omitted. Much cheaper
than paginating the whole history.
```
query: "timeout"
peer: "2156914166"   // omit for a global search
from_user: "egor"    // optional, single-chat search only
topic_id: 3          // optional
min_date: "2026-09-01"
```

#### `telegram_list_topics`
List forum topics of a supergroup with their ids. Returns `is_forum: false` for plain chats.
```
peer: "2126855055"
limit: 100
```

#### `telegram_export_chat`
Dump history to a local file and return only the path and stats — the messages never pass
through the conversation, so exporting a 16k-message chat costs one short response.
```
peer: "2156914166"
format: "jsonl"      // or "md" for a readable transcript
limit: 5000          // optional — default is the whole window
topic_id: 3          // optional
min_date: "2026-08-01"
```
Default location is `/tmp/tg_mcp_export/chat_<id>_<timestamp>.<fmt>`.

#### `telegram_download_media`
Download media of a specific message by id.
```
message_id: 12345
peer: "some_user"    // optional
```
Returns `media_path` pointing to `/tmp/tg_mcp_media/msg_<id>.<ext>`.

#### `telegram_get_session_string`
Return the current session string (useful to save after initial auth).

### AI tools (require `OPENAI_API_KEY`)

#### `telegram_ai_send_message`
Generate a message via GPT and send it.
```
prompt: "Write a short birthday greeting"
system: "..."        // optional tone/style
model: "gpt-4o-mini" // optional
peer:  "some_user"   // optional
```

#### `telegram_ai_send_voice`
Generate a voice message via OpenAI TTS and send it. Accepts either a ready `text` or a `prompt` (GPT generates the text, then TTS synthesizes it).
```
prompt: "Record a short greeting in English"
voice:  "nova"    // alloy, echo, fable, onyx, nova, shimmer
model:  "tts-1"   // optional
```

#### `telegram_transcribe_voice`
Download a voice message or video note from the chat and transcribe it with Whisper.
```
peer:       "some_user"   // optional
message_id: 12345         // optional — latest voice/video_note if omitted
language:   "en"          // optional ISO hint for accuracy
```

#### `telegram_digest`
Summarize a period of a chat via GPT — what broke, who did what, what is still open.
```
peer: "2156914166"
hours: 12              // or an explicit min_date/max_date window
topic_id: 3            // optional
limit: 300             // max messages fed to the model
send_to: "me"          // optional — "me" delivers the summary to Saved Messages
system: "..."          // optional — change focus or language
```

#### `telegram_analyze_voice`
Transcribe a voice/video_note and analyze the content with GPT.
```
prompt:     "Extract key points and action items"
peer:       "some_user"
message_id: 12345         // optional
model:      "gpt-4o-mini" // optional
```

## Example scenario

```
1. telegram_set_bot(bot_username="some_bot")
2. telegram_send_message(text="/start")
3. telegram_get_keyboard()
4. telegram_click_button(button_text="Read")
5. telegram_wait_response(timeout=120)

// Messaging a user directly:
6. telegram_send_message(text="hi", peer="friend_username")

// AI workflow:
7. telegram_ai_send_voice(prompt="Record a motivational message", voice="nova", peer="friend_username")
8. telegram_analyze_voice(prompt="Summarize", peer="friend_username")
```

## Security

- **Do not** commit `.env` to git
- **Do not** commit `.session` files to git
- SESSION_STRING can be safely stored in environment variables

## Device parameters

The client emulates an Android device by default:
- Device: Samsung SM-G998B
- System: SDK 33
- App version: 10.8.3
- Language: ru

This reduces the chance of account limitations.

## Troubleshooting

### "Session file not found"
Use `SESSION_STRING` instead of a file-based session, or run `poetry run telegram-mcp-auth`.

### "Failed to find @username"
Double-check the username (without `@`). The peer must be someone your account can reach.

### "OPENAI_API_KEY is not set"
Add `OPENAI_API_KEY` to `.env` or the MCP server env to enable AI tools.

### "Timeout waiting for response"
The peer did not respond in time — check that the bot is running or the user is online.

### "FloodWait"
Too many requests. Wait the reported amount of time before retrying.
