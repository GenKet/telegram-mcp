"""Persistence for interview state files.

One JSON file per interview, outside the repository, so a brief survives both
Claude Code restarts and listener restarts.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_STATE_DIR = Path.home() / ".telegram-mcp"
HEARTBEAT_NAME = "listener.heartbeat"
ACTIVE_STATUSES = ("draft", "waiting", "needs_attention")


def state_dir() -> Path:
    """Directory holding interview files. TELEGRAM_MCP_STATE_DIR overrides it (tests)."""
    root = Path(os.getenv("TELEGRAM_MCP_STATE_DIR") or DEFAULT_STATE_DIR)
    path = root / "interviews"
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def interview_path(interview_id: str) -> Path:
    return state_dir() / f"{interview_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(
    interview_id: str,
    peer: str,
    goal: str,
    questions: List[str],
    max_messages: int = 10,
    quiet_hours: Optional[Tuple[int, int]] = None,
) -> Dict[str, Any]:
    """Create a new interview in 'draft' — nothing is sent to the peer yet."""
    path = interview_path(interview_id)
    if path.exists():
        raise FileExistsError(f"interview '{interview_id}' already exists at {path}")

    for existing in load_all():
        if existing["peer"] == peer and existing["status"] in ACTIVE_STATUSES:
            raise ValueError(
                f"peer '{peer}' already has an active interview '{existing['id']}' "
                f"({existing['status']}) — finish or escalate it first"
            )

    state = {
        "id": interview_id,
        "peer": peer,
        "peer_id": None,
        "goal": goal,
        "status": "draft",
        "escalation": None,
        "checklist": [
            {
                "id": f"q{index}",
                "question": question,
                "status": "open",
                "answer": None,
                "asked_at": None,
            }
            for index, question in enumerate(questions, start=1)
        ],
        "messages": [],
        "last_seen_message_id": 0,
        "sent_count": 0,
        "max_messages": max_messages,
        "quiet_hours": list(quiet_hours) if quiet_hours else None,
        "summary": None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    save(state)
    return state


def save(state: Dict[str, Any]) -> Path:
    """Write the state atomically — a crash must never leave a half-written brief."""
    state["updated_at"] = _now_iso()
    path = interview_path(state["id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.chmod(0o600)
    os.replace(tmp, path)
    return path


def load(interview_id: str) -> Dict[str, Any]:
    path = interview_path(interview_id)
    if not path.exists():
        raise FileNotFoundError(f"no interview '{interview_id}' at {path}")
    return json.loads(path.read_text())


def load_all() -> List[Dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(state_dir().glob("*.json"))]


def heartbeat_write(now: Optional[float] = None) -> Path:
    """Record that the listener is alive."""
    path = state_dir() / HEARTBEAT_NAME
    path.write_text(str(now if now is not None else time.time()))
    return path


def heartbeat_age(now: Optional[float] = None) -> Optional[float]:
    """Seconds since the listener's last heartbeat, or None if it never wrote one."""
    path = state_dir() / HEARTBEAT_NAME
    if not path.exists():
        return None
    return (now if now is not None else time.time()) - float(path.read_text())
