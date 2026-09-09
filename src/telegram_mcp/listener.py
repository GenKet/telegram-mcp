"""Listener daemon: records incoming interview replies as they arrive.

It is deliberately dumb — it never formulates a question and never sends anything to
the peer. Claude does that through the tools. Losing this daemon costs reaction speed,
never correctness: telegram_interview_pending re-reads the chat anyway.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Dict

from dotenv import load_dotenv
from telethon import events

from . import interview_rules as rules
from . import interview_store as store
from .session_creator import TelegramSessionCreator

ACTIVE_STATUSES = ("waiting", "needs_attention")
HEARTBEAT_SECONDS = 10.0


def _watched_peers() -> Dict[int, str]:
    """peer_id -> interview id, for interviews that are waiting on a reply."""
    watched = {}
    for state in store.load_all():
        if state["status"] in ACTIVE_STATUSES and state.get("peer_id"):
            watched[int(state["peer_id"])] = state["id"]
    return watched


async def run(rescan_seconds: float = 15.0) -> None:
    load_dotenv()
    creator = TelegramSessionCreator(int(os.environ["API_ID"]), os.environ["API_HASH"])
    client = await creator.create_from_session_string(os.environ["SESSION_STRING"])

    watched: Dict[int, str] = _watched_peers()
    print(f"listening for {len(watched)} interview(s): {sorted(watched.values())}", flush=True)

    @client.on(events.NewMessage(incoming=True))
    async def on_message(event):
        interview_id = watched.get(event.chat_id) or watched.get(getattr(event, "sender_id", None))
        if not interview_id:
            return

        state = store.load(interview_id)
        added = rules.merge_messages(
            state,
            [
                {
                    "id": event.message.id,
                    "from": "peer",
                    "text": event.message.text or "",
                    "date": (event.message.date or datetime.now(timezone.utc)).isoformat(),
                }
            ],
        )
        if added:
            store.save(state)
            print(f"[{interview_id}] recorded reply {event.message.id}", flush=True)

    while True:
        store.heartbeat_write(connected=client.is_connected())
        fresh = _watched_peers()
        if fresh != watched:
            watched.clear()
            watched.update(fresh)
            print(f"watching: {sorted(watched.values())}", flush=True)
        await asyncio.sleep(min(rescan_seconds, HEARTBEAT_SECONDS))


def main() -> None:
    """Entry point for telegram-mcp-listen."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("stopped", flush=True)


if __name__ == "__main__":
    main()
