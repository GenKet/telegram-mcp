"""Pure decision rules over an interview state dict.

No I/O and no Telethon here: every guard that keeps the bot from embarrassing its
owner (two messages in a row, message limits, quiet hours) must be testable offline.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

CLOSED_STATUSES = ("answered", "skipped")


def open_items(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in state["checklist"] if item["status"] not in CLOSED_STATUSES]


def is_complete(state: Dict[str, Any]) -> bool:
    return not open_items(state)


def last_sender(state: Dict[str, Any]) -> Optional[str]:
    return state["messages"][-1]["from"] if state["messages"] else None


def in_quiet_hours(state: Dict[str, Any], now: datetime) -> bool:
    """True inside the configured no-messaging window (may wrap past midnight)."""
    window = state.get("quiet_hours")
    if not window:
        return False
    start, end = window
    hour = now.hour
    if start > end:  # window wraps past midnight, e.g. 23..9
        return hour >= start or hour < end
    return start <= hour < end


def can_send(state: Dict[str, Any], now: datetime) -> Tuple[bool, Optional[str]]:
    """Whether a message may go out right now, and why not when it may not."""
    status = state["status"]
    if status in ("done", "needs_human"):
        return False, f"interview is {status}"
    if state["sent_count"] >= state["max_messages"]:
        return False, f"message limit reached ({state['max_messages']})"
    if last_sender(state) == "me":
        return False, "waiting for the peer to reply first"
    if in_quiet_hours(state, now):
        return False, "inside quiet hours"
    return True, None


def record_sent(
    state: Dict[str, Any],
    text: str,
    message_id: int,
    covers: List[str],
    now: datetime,
) -> Dict[str, Any]:
    """Log an outgoing message and note which checklist items it asks about."""
    state["messages"].append({"id": message_id, "from": "me", "text": text, "date": now.isoformat()})
    state["sent_count"] += 1
    state["status"] = "waiting"
    for item in state["checklist"]:
        if item["id"] in covers:
            item["asked_at"] = now.isoformat()
    return state


def merge_messages(state: Dict[str, Any], incoming: List[Dict[str, Any]]) -> int:
    """Add messages we do not have yet; return how many were new."""
    known = {message["id"] for message in state["messages"]}
    fresh = [message for message in incoming if message["id"] not in known]
    if not fresh:
        return 0

    state["messages"].extend(fresh)
    state["messages"].sort(key=lambda message: message["id"])
    if any(message["from"] == "peer" for message in fresh) and state["status"] == "waiting":
        state["status"] = "needs_attention"
    return len(fresh)


def delta(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Peer messages Claude has not processed yet."""
    return [
        message
        for message in state["messages"]
        if message["from"] == "peer" and message["id"] > state["last_seen_message_id"]
    ]


def mark_answers(
    state: Dict[str, Any],
    marks: List[Dict[str, Any]],
    seen_up_to: Optional[int] = None,
) -> Dict[str, Any]:
    """Close checklist items from Claude's reading of the replies."""
    by_id = {item["id"]: item for item in state["checklist"]}
    for mark in marks:
        item = by_id.get(mark["item_id"])
        if item is None:
            raise KeyError(f"unknown checklist item '{mark['item_id']}'")
        item["status"] = mark.get("status", "answered")
        item["answer"] = mark.get("answer")

    if seen_up_to is not None:
        state["last_seen_message_id"] = max(state["last_seen_message_id"], seen_up_to)
    if state["status"] == "needs_attention":
        state["status"] = "waiting"
    return state


def escalate(state: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Hand the conversation back to the owner; the peer gets no message."""
    state["status"] = "needs_human"
    state["escalation"] = reason
    return state


def finish(state: Dict[str, Any], summary: str) -> Dict[str, Any]:
    state["status"] = "done"
    state["summary"] = summary
    return state
