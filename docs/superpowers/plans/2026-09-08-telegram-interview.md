# Autonomous Interviewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude ведёт брифинг-диалог в Telegram: раскладывает цель на пункты, отправляет вопросы, дожидается ответов (часы-дни), дозадаёт недостающее и отдаёт саммари.

**Architecture:** Состояние — один JSON на интервью в `~/.telegram-mcp/interviews/`. Persistence (`interview_store.py`) и правила (`interview_rules.py`) — чистый код без сети, под тестами целиком. Демон (`listener.py`) держит Telethon, пишет входящие в состояние и никогда не отвечает клиенту. Решения принимает Claude через 7 тулов `telegram_interview_*`. `interview_pending` всегда дочитывает историю чата, поэтому корректность не зависит от живости демона.

**Tech Stack:** Python 3.11+, Telethon 1.42, MCP SDK, pytest, poetry.

**Spec:** `docs/superpowers/specs/2026-09-08-telegram-interview-design.md`

## Global Constraints

- Python `^3.11`; venv проекта — `telegram-mcp-MnyK7a-O-py3.14`, все команды через `poetry run`.
- Никакого OpenAI в контуре интервью: вопросы, разбор ответов и саммари формулирует Claude. `OPENAI_API_KEY` в проекте не настроен, и фича не должна его требовать.
- Состояние живёт вне репозитория: `~/.telegram-mcp/interviews/`. Каталог создаётся с правами `0o700`, файлы — `0o600`.
- Тесты переопределяют каталог состояния через переменную окружения `TELEGRAM_MCP_STATE_DIR`; в продакшене она не задана.
- Docstrings и комментарии — по-английски, как в остальном проекте. Комментарии редкие, только там, где неочевидно «почему».
- Демон никогда не отправляет сообщения собеседнику. Отправка есть только в тулах.
- Живой прогон — только с `@pythonkiller228` (user id `6558694545`). Никаких реальных клиентов.
- Уточнение к спеке (принять в Task 1): состояние хранит и `peer`, и `peer_id` — демон сопоставляет входящие по числовому id, а не по username.

---

### Task 1: Persistence состояния интервью

**Files:**
- Create: `src/telegram_mcp/interview_store.py`
- Test: `tests/test_interview_store.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `state_dir() -> Path`
  - `interview_path(interview_id: str) -> Path`
  - `create(interview_id: str, peer: str, goal: str, questions: list[str], max_messages: int = 10, quiet_hours: tuple[int, int] | None = None) -> dict`
  - `save(state: dict) -> Path`
  - `load(interview_id: str) -> dict`
  - `load_all() -> list[dict]`
  - `heartbeat_write(now: float | None = None) -> Path`
  - `heartbeat_age(now: float | None = None) -> float | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_interview_store.py
"""Tests for interview state persistence."""

import json
import os
import stat

import pytest

from telegram_mcp import interview_store as store


@pytest.fixture(autouse=True)
def state_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_MCP_STATE_DIR", str(tmp_path))
    return tmp_path


def test_create_builds_checklist_from_questions():
    state = store.create("clientx-site", "clientX", "выяснить какой сайт нужен", ["Тип сайта?", "Сроки?"])

    assert state["id"] == "clientx-site"
    assert state["peer"] == "clientX"
    assert state["peer_id"] is None
    assert state["status"] == "draft"
    assert state["last_seen_message_id"] == 0
    assert state["sent_count"] == 0
    assert state["max_messages"] == 10
    assert [item["id"] for item in state["checklist"]] == ["q1", "q2"]
    assert state["checklist"][0] == {
        "id": "q1",
        "question": "Тип сайта?",
        "status": "open",
        "answer": None,
        "asked_at": None,
    }


def test_create_persists_file_and_is_readable_back():
    store.create("acme", "acmeuser", "goal", ["Q?"])

    loaded = store.load("acme")
    assert loaded["peer"] == "acmeuser"
    assert loaded["goal"] == "goal"


def test_create_rejects_duplicate_id():
    store.create("acme", "acmeuser", "goal", ["Q?"])

    with pytest.raises(FileExistsError):
        store.create("acme", "acmeuser", "goal", ["Q?"])


def test_save_is_atomic_and_leaves_no_temp_files(state_root):
    state = store.create("acme", "acmeuser", "goal", ["Q?"])
    state["goal"] = "updated"
    store.save(state)

    files = sorted(p.name for p in (state_root / "interviews").iterdir())
    assert files == ["acme.json"]
    assert json.loads((state_root / "interviews" / "acme.json").read_text())["goal"] == "updated"


def test_state_files_are_owner_only(state_root):
    store.create("acme", "acmeuser", "goal", ["Q?"])

    mode = stat.S_IMODE(os.stat(state_root / "interviews" / "acme.json").st_mode)
    assert mode == 0o600


def test_load_missing_interview_raises():
    with pytest.raises(FileNotFoundError):
        store.load("nope")


def test_load_all_returns_every_interview_sorted_by_id():
    store.create("b-one", "peer1", "goal", ["Q?"])
    store.create("a-two", "peer2", "goal", ["Q?"])

    assert [s["id"] for s in store.load_all()] == ["a-two", "b-one"]


def test_load_all_on_empty_dir_returns_empty_list():
    assert store.load_all() == []


def test_heartbeat_age_is_none_without_file():
    assert store.heartbeat_age() is None


def test_heartbeat_age_measures_seconds_since_write():
    store.heartbeat_write(now=1000.0)

    assert store.heartbeat_age(now=1042.0) == pytest.approx(42.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_interview_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'telegram_mcp.interview_store'`

- [ ] **Step 3: Write the implementation**

```python
# src/telegram_mcp/interview_store.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_interview_store.py -q`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/interview_store.py tests/test_interview_store.py
git commit -m "interview state persistence"
```

---

### Task 2: Правила диалога

**Files:**
- Create: `src/telegram_mcp/interview_rules.py`
- Test: `tests/test_interview_rules.py`

**Interfaces:**
- Consumes: словарь состояния из Task 1 (`create`).
- Produces:
  - `open_items(state: dict) -> list[dict]`
  - `is_complete(state: dict) -> bool`
  - `last_sender(state: dict) -> str | None`
  - `in_quiet_hours(state: dict, now: datetime) -> bool`
  - `can_send(state: dict, now: datetime) -> tuple[bool, str | None]`
  - `record_sent(state: dict, text: str, message_id: int, covers: list[str], now: datetime) -> dict`
  - `merge_messages(state: dict, incoming: list[dict]) -> int`
  - `delta(state: dict) -> list[dict]`
  - `mark_answers(state: dict, marks: list[dict], seen_up_to: int | None = None) -> dict`
  - `escalate(state: dict, reason: str) -> dict`
  - `finish(state: dict, summary: str) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_interview_rules.py
"""Tests for the pure interview decision rules."""

from datetime import datetime, timezone

import pytest

from telegram_mcp import interview_rules as rules


def make_state(**overrides):
    state = {
        "id": "acme",
        "peer": "acmeuser",
        "peer_id": 42,
        "goal": "goal",
        "status": "draft",
        "escalation": None,
        "checklist": [
            {"id": "q1", "question": "Тип сайта?", "status": "open", "answer": None, "asked_at": None},
            {"id": "q2", "question": "Сроки?", "status": "open", "answer": None, "asked_at": None},
        ],
        "messages": [],
        "last_seen_message_id": 0,
        "sent_count": 0,
        "max_messages": 10,
        "quiet_hours": None,
        "summary": None,
    }
    state.update(overrides)
    return state


NOON = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def test_open_items_lists_only_unanswered():
    state = make_state()
    state["checklist"][0]["status"] = "answered"

    assert [item["id"] for item in rules.open_items(state)] == ["q2"]


def test_is_complete_when_every_item_answered_or_skipped():
    state = make_state()
    state["checklist"][0]["status"] = "answered"
    state["checklist"][1]["status"] = "skipped"

    assert rules.is_complete(state) is True


def test_is_complete_false_with_an_open_item():
    assert rules.is_complete(make_state()) is False


def test_can_send_allows_first_message():
    assert rules.can_send(make_state(), NOON) == (True, None)


def test_can_send_blocks_two_messages_in_a_row():
    state = make_state(messages=[{"id": 1, "from": "me", "text": "первый вопрос", "date": None}])

    allowed, reason = rules.can_send(state, NOON)
    assert allowed is False
    assert "waiting for the peer" in reason


def test_can_send_allows_after_peer_replied():
    state = make_state(
        messages=[
            {"id": 1, "from": "me", "text": "вопрос", "date": None},
            {"id": 2, "from": "peer", "text": "ответ", "date": None},
        ]
    )

    assert rules.can_send(state, NOON) == (True, None)


def test_can_send_blocks_at_message_limit():
    state = make_state(sent_count=10, max_messages=10)

    allowed, reason = rules.can_send(state, NOON)
    assert allowed is False
    assert "message limit" in reason


def test_can_send_blocks_when_finished_or_escalated():
    for status in ("done", "needs_human"):
        allowed, reason = rules.can_send(make_state(status=status), NOON)
        assert allowed is False
        assert status in reason


def test_quiet_hours_block_overnight_window():
    state = make_state(quiet_hours=[23, 9])

    assert rules.in_quiet_hours(state, datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)) is True
    assert rules.in_quiet_hours(state, datetime(2026, 9, 8, 23, 30, tzinfo=timezone.utc)) is True
    assert rules.in_quiet_hours(state, NOON) is False


def test_quiet_hours_off_by_default():
    assert rules.in_quiet_hours(make_state(), datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc)) is False


def test_can_send_blocks_inside_quiet_hours():
    state = make_state(quiet_hours=[23, 9])

    allowed, reason = rules.can_send(state, datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc))
    assert allowed is False
    assert "quiet hours" in reason


def test_record_sent_marks_asked_items_and_switches_to_waiting():
    state = make_state()

    rules.record_sent(state, "Тип сайта? Сроки?", message_id=7, covers=["q1", "q2"], now=NOON)

    assert state["status"] == "waiting"
    assert state["sent_count"] == 1
    assert state["messages"][-1] == {"id": 7, "from": "me", "text": "Тип сайта? Сроки?", "date": NOON.isoformat()}
    assert state["checklist"][0]["asked_at"] == NOON.isoformat()
    assert state["checklist"][0]["status"] == "open"


def test_merge_messages_deduplicates_and_sorts():
    state = make_state(messages=[{"id": 5, "from": "peer", "text": "старое", "date": None}])

    added = rules.merge_messages(
        state,
        [
            {"id": 5, "from": "peer", "text": "старое", "date": None},
            {"id": 7, "from": "peer", "text": "новое", "date": None},
            {"id": 6, "from": "me", "text": "моё", "date": None},
        ],
    )

    assert added == 2
    assert [m["id"] for m in state["messages"]] == [5, 6, 7]


def test_merge_messages_flags_attention_for_peer_replies():
    state = make_state(status="waiting")

    rules.merge_messages(state, [{"id": 9, "from": "peer", "text": "ответ", "date": None}])

    assert state["status"] == "needs_attention"


def test_merge_messages_keeps_status_for_own_messages():
    state = make_state(status="waiting")

    rules.merge_messages(state, [{"id": 9, "from": "me", "text": "моё", "date": None}])

    assert state["status"] == "waiting"


def test_delta_returns_unprocessed_peer_messages_only():
    state = make_state(
        last_seen_message_id=5,
        messages=[
            {"id": 5, "from": "peer", "text": "обработано", "date": None},
            {"id": 6, "from": "me", "text": "моё", "date": None},
            {"id": 7, "from": "peer", "text": "новое", "date": None},
        ],
    )

    assert [m["id"] for m in rules.delta(state)] == [7]


def test_mark_answers_records_answers_and_advances_seen_pointer():
    state = make_state(status="needs_attention")

    rules.mark_answers(
        state,
        [{"item_id": "q1", "status": "answered", "answer": "лендинг"}],
        seen_up_to=7,
    )

    assert state["checklist"][0]["status"] == "answered"
    assert state["checklist"][0]["answer"] == "лендинг"
    assert state["last_seen_message_id"] == 7
    assert state["status"] == "waiting"


def test_mark_answers_is_idempotent():
    state = make_state()
    marks = [{"item_id": "q1", "status": "answered", "answer": "лендинг"}]

    rules.mark_answers(state, marks, seen_up_to=7)
    rules.mark_answers(state, marks, seen_up_to=7)

    assert state["checklist"][0]["answer"] == "лендинг"
    assert state["last_seen_message_id"] == 7


def test_mark_answers_never_rewinds_seen_pointer():
    state = make_state(last_seen_message_id=9)

    rules.mark_answers(state, [], seen_up_to=4)

    assert state["last_seen_message_id"] == 9


def test_mark_answers_rejects_unknown_item():
    state = make_state()

    with pytest.raises(KeyError):
        rules.mark_answers(state, [{"item_id": "nope", "status": "answered", "answer": "x"}])


def test_escalate_sets_needs_human_with_reason():
    state = make_state(status="needs_attention")

    rules.escalate(state, "спросил про цену")

    assert state["status"] == "needs_human"
    assert state["escalation"] == "спросил про цену"


def test_finish_stores_summary_and_closes():
    state = make_state()

    rules.finish(state, "нужен лендинг за две недели")

    assert state["status"] == "done"
    assert state["summary"] == "нужен лендинг за две недели"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_interview_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'telegram_mcp.interview_rules'`

- [ ] **Step 3: Write the implementation**

```python
# src/telegram_mcp/interview_rules.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_interview_rules.py -q`
Expected: PASS — 22 passed

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/interview_rules.py tests/test_interview_rules.py
git commit -m "interview dialogue rules"
```

---

### Task 3: Дочитывание истории в клиенте

**Files:**
- Modify: `src/telegram_mcp/client.py` (новый метод рядом с `collect_messages`)

**Interfaces:**
- Consumes: `TelegramTestClient._resolve_identifier`, `_history_item` (существуют).
- Produces: `async messages_since(peer: str, after_id: int = 0, limit: int = 100) -> tuple[list[dict], int]` — список элементов истории (oldest-first, у каждого `from`: `"me"` либо `"peer"`) и `peer_id`.

- [ ] **Step 1: Add the method**

```python
    async def messages_since(
        self,
        peer: str,
        after_id: int = 0,
        limit: int = 100,
    ) -> tuple[List[Dict[str, Any]], int]:
        """History newer than after_id, oldest-first, plus the resolved peer id.

        This is the catch-up path for interviews: it reads the chat itself instead of
        trusting the listener, so a dead daemon costs speed but never correctness.
        """
        if not self.client:
            raise Exception("Not connected")

        entity = await self._resolve_identifier(peer)
        messages = await self.client.get_messages(entity, limit=min(limit, 100), min_id=after_id)

        items = []
        for msg in reversed(messages):
            item = self._history_item(msg)
            item["from"] = "me" if msg.out else "peer"
            items.append(item)
        return items, entity.id
```

- [ ] **Step 2: Verify against the live account**

Run:
```bash
poetry run python - <<'EOF'
import asyncio, os
from dotenv import load_dotenv
load_dotenv('.env')
from telegram_mcp.client import TelegramTestClient

async def main():
    client = TelegramTestClient(int(os.getenv('API_ID')), os.getenv('API_HASH'), session_string=os.getenv('SESSION_STRING'))
    await client.connect()
    items, peer_id = await client.messages_since('pythonkiller228', after_id=0, limit=5)
    print('peer_id:', peer_id)
    for item in items:
        print(item['id'], item['from'], '|', item['text'][:40])
    newest = items[-1]['id'] if items else 0
    empty, _ = await client.messages_since('pythonkiller228', after_id=newest)
    print('after newest ->', len(empty), 'items (expect 0)')
    await client.disconnect()

asyncio.run(main())
EOF
```
Expected: `peer_id: 6558694545`, сообщения по возрастанию id с пометками `me`/`peer`, второй вызов даёт `0 items`.

- [ ] **Step 3: Commit**

```bash
git add src/telegram_mcp/client.py
git commit -m "catch-up reads for interviews"
```

---

### Task 4: Тулы start / send / list

**Files:**
- Modify: `src/telegram_mcp/server.py` (схемы тулов рядом с `telegram_interview_*`, ветки в диспетчере, импорты)

**Interfaces:**
- Consumes: `interview_store.create/load/save/load_all/heartbeat_age`, `interview_rules.can_send/record_sent/open_items/is_complete`, `TelegramTestClient.send_message`, `messages_since`.
- Produces тулы: `telegram_interview_start`, `telegram_interview_send`, `telegram_interview_list`.

- [ ] **Step 1: Add imports and the three tool schemas**

В начало `server.py`, рядом с существующими импортами проекта:

```python
from . import interview_rules as rules
from . import interview_store as store
```

Схемы — перед `Tool(name="telegram_download_media"`:

```python
        Tool(
            name="telegram_interview_start",
            description=(
                "Create a brief-gathering interview in 'draft': store the goal and the checklist "
                "of questions YOU generated. Sends nothing — show the first message to the owner first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Short slug identifying the interview, e.g. 'clientx-site'"},
                    "peer": _PEER_PROPERTY,
                    "goal": {"type": "string", "description": "What the owner wants to find out, in their own words"},
                    "questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Checklist of questions to close, one per point",
                    },
                    "max_messages": {
                        "type": ["integer", "string"],
                        "description": "Stop and ask the owner after this many sent messages (default: 10)",
                        "default": 10,
                    },
                },
                "required": ["id", "peer", "goal", "questions"],
            },
        ),
        Tool(
            name="telegram_interview_send",
            description=(
                "Send a message to the interviewee and record which checklist items it asks about. "
                "Refuses when the peer has not replied yet, the message limit is reached, or the "
                "interview is finished or escalated."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Interview id"},
                    "text": {"type": "string", "description": "Exact text to send"},
                    "covers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Checklist item ids this message asks about, e.g. ['q1','q2']",
                    },
                },
                "required": ["id", "text"],
            },
        ),
        Tool(
            name="telegram_interview_list",
            description="List interviews with status, checklist progress, who spoke last, and whether the listener daemon is alive.",
            inputSchema={"type": "object", "properties": {}},
        ),
```

- [ ] **Step 2: Add the dispatcher branches**

Перед `elif name == "telegram_download_media":`:

```python
        elif name == "telegram_interview_start":
            state = store.create(
                arguments["id"],
                arguments["peer"],
                arguments["goal"],
                list(arguments["questions"]),
                max_messages=int(arguments.get("max_messages", 10)),
            )
            result = {
                "id": state["id"],
                "status": state["status"],
                "checklist": state["checklist"],
                "note": "draft created, nothing sent — show the first message to the owner, then call telegram_interview_send",
            }

        elif name == "telegram_interview_send":
            state = store.load(arguments["id"])
            allowed, reason = rules.can_send(state, datetime.now(timezone.utc))
            if not allowed:
                result = {"sent": False, "reason": reason, "status": state["status"]}
            else:
                sent = await client.send_message(arguments["text"], peer=state["peer"])
                rules.record_sent(
                    state,
                    arguments["text"],
                    message_id=sent.get("message_id") or 0,
                    covers=list(arguments.get("covers") or []),
                    now=datetime.now(timezone.utc),
                )
                store.save(state)
                result = {
                    "sent": True,
                    "status": state["status"],
                    "sent_count": state["sent_count"],
                    "open_items": [item["id"] for item in rules.open_items(state)],
                }

        elif name == "telegram_interview_list":
            age = store.heartbeat_age()
            result = {
                "listener": "never started" if age is None else (f"alive {int(age)}s ago" if age < 60 else f"dead, last seen {int(age)}s ago"),
                "interviews": [
                    {
                        "id": state["id"],
                        "peer": state["peer"],
                        "status": state["status"],
                        "escalation": state["escalation"],
                        "closed_items": sum(1 for item in state["checklist"] if item["status"] in rules.CLOSED_STATUSES),
                        "total_items": len(state["checklist"]),
                        "last_sender": rules.last_sender(state),
                        "sent_count": state["sent_count"],
                    }
                    for state in store.load_all()
                ],
            }
```

- [ ] **Step 3: Check what send_message returns and fix the message_id key if needed**

Run: `grep -n "async def send_message" -A 12 src/telegram_mcp/client.py`
Ожидание: увидеть ключ с id отправленного сообщения. Если он называется иначе (например `"id"`), поправить `sent.get("message_id")` на фактический ключ — тест из шага 4 это поймает.

- [ ] **Step 4: Verify через MCP handshake и живой вызов**

Run:
```bash
cd src && printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"p","version":"1"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | API_ID=2040 API_HASH="$(grep '^API_HASH=' ../.env | cut -d= -f2-)" SESSION_STRING="$(grep '^SESSION_STRING=' ../.env | cut -d= -f2-)" \
   poetry run python -m telegram_mcp.server 2>&1 | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    data = json.loads(line)
    if data.get('id') == 2:
        names = [t['name'] for t in data['result']['tools']]
        print('tools:', len(names))
        print('new:', [n for n in names if n.startswith('telegram_interview')])
"
```
Expected: `tools: 28`, `new: ['telegram_interview_start', 'telegram_interview_send', 'telegram_interview_list']`

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/server.py
git commit -m "interview tools: start, send, list"
```

---

### Task 5: Тулы pending / mark / escalate / finish

**Files:**
- Modify: `src/telegram_mcp/server.py`

**Interfaces:**
- Consumes: всё из Task 4 плюс `TelegramTestClient.messages_since` (Task 3), `interview_rules.merge_messages/delta/mark_answers/escalate/finish/is_complete`.
- Produces тулы: `telegram_interview_pending`, `telegram_interview_mark`, `telegram_interview_escalate`, `telegram_interview_finish`.

- [ ] **Step 1: Add the four tool schemas**

Рядом с тремя из Task 4:

```python
        Tool(
            name="telegram_interview_pending",
            description=(
                "Fetch unprocessed replies for an interview and the current checklist. Always re-reads "
                "the chat itself, so it works even if the listener daemon was down."
            ),
            inputSchema={
                "type": "object",
                "properties": {"id": {"type": "string", "description": "Interview id"}},
                "required": ["id"],
            },
        ),
        Tool(
            name="telegram_interview_mark",
            description=(
                "Record what the replies answered: close checklist items and advance the processed-message "
                "pointer. Call this after reading telegram_interview_pending."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Interview id"},
                    "marks": {
                        "type": "array",
                        "description": "One entry per checklist item you are closing",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_id": {"type": "string", "description": "Checklist item id, e.g. 'q1'"},
                                "status": {"type": "string", "enum": ["answered", "skipped"], "default": "answered"},
                                "answer": {"type": "string", "description": "The answer distilled from the reply"},
                            },
                            "required": ["item_id"],
                        },
                    },
                    "seen_up_to": {
                        "type": ["integer", "string"],
                        "description": "Highest peer message id you processed (from telegram_interview_pending)",
                    },
                },
                "required": ["id", "marks"],
            },
        ),
        Tool(
            name="telegram_interview_escalate",
            description=(
                "Hand the conversation back to the owner without answering the peer — for questions outside "
                "the checklist: price, deadlines, contracts, a call request, or a negative turn."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Interview id"},
                    "reason": {"type": "string", "description": "Why a human is needed, quoting the peer"},
                },
                "required": ["id", "reason"],
            },
        ),
        Tool(
            name="telegram_interview_finish",
            description="Close the interview with a summary; 'send_to' delivers a copy (use 'me' for Saved Messages).",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Interview id"},
                    "summary": {"type": "string", "description": "The brief you assembled from the answers"},
                    "send_to": {"type": "string", "description": "Peer to send the summary to — 'me' for Saved Messages"},
                },
                "required": ["id", "summary"],
            },
        ),
```

- [ ] **Step 2: Add the dispatcher branches**

```python
        elif name == "telegram_interview_pending":
            state = store.load(arguments["id"])
            fetched, peer_id = await client.messages_since(state["peer"], after_id=state["last_seen_message_id"])
            state["peer_id"] = peer_id
            added = rules.merge_messages(state, fetched)
            store.save(state)
            result = {
                "id": state["id"],
                "status": state["status"],
                "new_messages": added,
                "replies": rules.delta(state),
                "checklist": state["checklist"],
                "open_items": [item["id"] for item in rules.open_items(state)],
                "sent_count": state["sent_count"],
                "max_messages": state["max_messages"],
            }

        elif name == "telegram_interview_mark":
            state = store.load(arguments["id"])
            rules.mark_answers(
                state,
                list(arguments["marks"]),
                seen_up_to=_optional_int(arguments.get("seen_up_to")),
            )
            store.save(state)
            result = {
                "id": state["id"],
                "status": state["status"],
                "open_items": [item["id"] for item in rules.open_items(state)],
                "complete": rules.is_complete(state),
            }

        elif name == "telegram_interview_escalate":
            state = store.load(arguments["id"])
            rules.escalate(state, arguments["reason"])
            store.save(state)
            result = {"id": state["id"], "status": state["status"], "escalation": state["escalation"]}

        elif name == "telegram_interview_finish":
            state = store.load(arguments["id"])
            rules.finish(state, arguments["summary"])
            store.save(state)
            result = {
                "id": state["id"],
                "status": state["status"],
                "path": str(store.interview_path(state["id"])),
                "answers": [
                    {"question": item["question"], "answer": item["answer"], "status": item["status"]}
                    for item in state["checklist"]
                ],
            }
            send_to = arguments.get("send_to")
            if send_to:
                await client.send_message(arguments["summary"], peer=send_to)
                result["sent_to"] = send_to
```

- [ ] **Step 3: Verify tool registration**

Run: та же handshake-команда, что в Task 4 Step 4.
Expected: `tools: 32`, среди них все семь `telegram_interview_*`.

- [ ] **Step 4: Verify the full state machine without touching the peer**

Run:
```bash
TELEGRAM_MCP_STATE_DIR=/tmp/tg-interview-check poetry run python - <<'EOF'
from datetime import datetime, timezone
from telegram_mcp import interview_rules as rules, interview_store as store

state = store.create("check", "pythonkiller228", "проверка", ["Тип сайта?", "Сроки?"])
print("can_send draft:", rules.can_send(state, datetime.now(timezone.utc)))
rules.record_sent(state, "Тип сайта? Сроки?", 1, ["q1", "q2"], datetime.now(timezone.utc))
print("can_send after we sent:", rules.can_send(state, datetime.now(timezone.utc)))
rules.merge_messages(state, [{"id": 2, "from": "peer", "text": "лендинг, две недели", "date": None}])
print("status after reply:", state["status"], "| delta:", [m["id"] for m in rules.delta(state)])
rules.mark_answers(state, [{"item_id": "q1", "status": "answered", "answer": "лендинг"}], seen_up_to=2)
print("open after mark:", [i["id"] for i in rules.open_items(state)], "| complete:", rules.is_complete(state))
rules.mark_answers(state, [{"item_id": "q2", "status": "answered", "answer": "две недели"}])
print("complete now:", rules.is_complete(state))
store.save(state)
EOF
rm -rf /tmp/tg-interview-check
```
Expected: `can_send draft: (True, None)`; после своей отправки — `(False, 'waiting for the peer to reply first')`; статус `needs_attention`, дельта `[2]`; после первой отметки остаётся `['q2']`, `complete: False`; затем `complete now: True`.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/server.py
git commit -m "interview tools: pending, mark, escalate, finish"
```

---

### Task 6: Демон-слушатель

**Files:**
- Create: `src/telegram_mcp/listener.py`
- Modify: `pyproject.toml` (скрипт `telegram-mcp-listen`)
- Modify: `README.md` (раздел про демон)

**Interfaces:**
- Consumes: `interview_store.load_all/load/save/heartbeat_write`, `interview_rules.merge_messages`, `TelegramSessionCreator.create_from_session_string`.
- Produces: `async run(rescan_seconds: float = 15.0) -> None`, `def main() -> None` — точка входа скрипта.

- [ ] **Step 1: Write the implementation**

```python
# src/telegram_mcp/listener.py
"""Listener daemon: records incoming interview replies as they arrive.

It is deliberately dumb — it never formulates a question and never sends anything to
the peer. Claude does that through the tools. Losing this daemon costs reaction speed,
never correctness: telegram_interview_pending re-reads the chat anyway.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Dict, Set

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
        store.heartbeat_write()
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
```

- [ ] **Step 2: Register the script**

В `pyproject.toml`, в `[tool.poetry.scripts]`:

```toml
telegram-mcp-listen = "telegram_mcp.listener:main"
```

- [ ] **Step 3: Verify it starts, writes a heartbeat, and coexists with the MCP server**

Run:
```bash
poetry install
TELEGRAM_MCP_STATE_DIR=/tmp/tg-listen-check poetry run telegram-mcp-listen &
sleep 12
cat /tmp/tg-listen-check/interviews/listener.heartbeat && echo " <- heartbeat"
TELEGRAM_MCP_STATE_DIR=/tmp/tg-listen-check poetry run python -c "
from telegram_mcp import interview_store as store
print('heartbeat age:', round(store.heartbeat_age(), 1), 'seconds')
"
kill %1
rm -rf /tmp/tg-listen-check
```
Expected: `listening for 0 interview(s): []`, файл heartbeat существует, возраст меньше 15 секунд, процесс завершается по `kill` без трейсбека.

- [ ] **Step 4: Document the daemon in README**

В `README.md`, после раздела авторизации:

```markdown
### Interview listener (optional)

The interview tools work without it, but replies are then noticed only when Claude
looks. Run the listener to have replies recorded the moment they arrive:

```bash
poetry run telegram-mcp-listen
```

It records incoming replies of active interviews into `~/.telegram-mcp/interviews/`
and writes a heartbeat there. It never sends anything to the interviewee.
```

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/listener.py pyproject.toml poetry.lock README.md
git commit -m "interview listener daemon"
```

---

### Task 7: Живой прогон и документация тулов

**Files:**
- Modify: `README.md` (раздел `telegram_interview_*`)
- Test: живой диалог с `@pythonkiller228`

**Interfaces:**
- Consumes: все тулы Task 4-5, демон Task 6.
- Produces: подтверждённый рабочий цикл и документация.

- [ ] **Step 1: Run the interview end-to-end with the owner's second account**

Порядок (владелец отвечает со второго аккаунта, каждый шаг подтверждает вживую):

1. `telegram_interview_start` — id `probe-site`, peer `pythonkiller228`, goal «выяснить какой сайт нужен», questions: `["Что за сайт — лендинг, магазин, портал?", "Какие сроки?", "Есть примеры, которые нравятся?"]`. Ожидание: `status: draft`, три пункта `q1..q3`, ничего не отправлено.
2. Показать владельцу текст первого сообщения, получить ок, вызвать `telegram_interview_send` с `covers: ["q1","q2"]`. Ожидание: `sent: true`, `status: waiting`.
3. Сразу вызвать `telegram_interview_send` ещё раз. Ожидание: `sent: false`, `reason: "waiting for the peer to reply first"` — сообщение НЕ ушло.
4. Владелец отвечает со второго аккаунта, закрывая только один пункт («лендинг»). Дождаться строки `[probe-site] recorded reply <id>` в логе демона.
5. `telegram_interview_pending`. Ожидание: `new_messages: 1`, ответ в `replies`, `open_items` содержит `q2` и `q3`.
6. `telegram_interview_mark` с `q1` и `seen_up_to` из ответа, затем `telegram_interview_send` с дозадачей по `q2`/`q3`. Ожидание: `complete: false`, дозадача уходит.
7. Владелец отвечает вопросом про цену. Вызвать `telegram_interview_escalate`. Ожидание: `status: needs_human`, собеседнику НИЧЕГО не отправлено (проверить последним сообщением в чате).
8. Владелец отвечает по существу; закрыть остаток через `mark`, затем `telegram_interview_finish` с саммари и `send_to: "me"`. Ожидание: `status: done`, саммари пришло в Избранное.
9. `telegram_interview_list`. Ожидание: `probe-site` в статусе `done`, `3/3`, listener alive.

- [ ] **Step 2: Verify the daemon is not load-bearing**

Run: убить демона (`kill` процесса `telegram-mcp-listen`), ответить со второго аккаунта, затем вызвать `telegram_interview_pending` для `probe-site`.
Expected: ответ всё равно попадает в `replies` (дочитан из истории), в `telegram_interview_list` строка listener — `dead, last seen Ns ago`.

- [ ] **Step 3: Document the tools in README**

Добавить в README после раздела `telegram_export_chat`:

```markdown
#### `telegram_interview_*`
A brief-gathering interview: Claude turns a goal into a checklist, asks the peer,
waits for answers across hours or days, asks the missing bits and delivers a summary.
```
id: "clientx-site"
peer: "clientX"
goal: "выяснить какой сайт нужен"
questions: ["Что за сайт?", "Сроки?", "Референсы?"]
```
Flow: `start` (draft, nothing sent) → owner approves the wording → `send` →
`pending` (unprocessed replies) → `mark` (what they answered) → `send` again for the
gaps → `finish` (summary, `send_to: "me"` copies it to Saved Messages).
`escalate` hands the chat back to you without answering the peer — used for price,
deadlines, contracts, call requests. Guards: no two messages in a row without a reply,
`max_messages` (default 10) then `needs_human`. State lives in
`~/.telegram-mcp/interviews/<id>.json`; run `telegram-mcp-listen` for instant pickup.
```

- [ ] **Step 4: Run the whole test suite**

Run: `poetry run pytest tests -q`
Expected: PASS — 49 passed (17 существующих + 10 из Task 1 + 22 из Task 2)

- [ ] **Step 5: Commit and push**

```bash
git add README.md
git commit -m "document the interview tools"
git push origin main
```

---

## Self-Review

**Spec coverage:**
- состояние и его формат → Task 1
- ограничители (два подряд, лимит, тихие часы, эскалация) → Task 2 (правила) + Task 4-5 (тулы)
- `pending` дочитывает историю, демон не несущий → Task 3 + Task 5 Step 2, проверка в Task 7 Step 2
- 7 тулов → Task 4 (3) + Task 5 (4)
- демон + heartbeat + `interview_list` показывает живость → Task 6, отображение в Task 4
- эскалация без ответа клиенту → Task 5, живая проверка Task 7 Step 1.7
- pytest по чистой логике → Task 1-2; живой прогон с `@pythonkiller228` → Task 7
- голосовой ответ клиента: `_history_item` кладёт `media_type`, текст пустой → пункт не закроется, Claude вызовет `escalate`. Отдельного кода не требует.

**Placeholders:** нет — каждый шаг содержит либо готовый код, либо конкретную команду с ожидаемым выводом.

**Type consistency:** `store.create/save/load/load_all/heartbeat_age/interview_path`, `rules.can_send/record_sent/merge_messages/delta/mark_answers/open_items/is_complete/last_sender/escalate/finish/CLOSED_STATUSES`, `client.messages_since` — имена в Task 4-7 совпадают с объявленными в Task 1-3. `messages_since` возвращает кортеж `(items, peer_id)` и так и распаковывается в Task 5. `_optional_int` из server.py уже существует.

**Одно уточнение к спеке, внесённое в план:** состояние хранит `peer_id` (заполняется первым `pending`), потому что демон сопоставляет входящие по числовому id, а не по username. В Global Constraints это зафиксировано.
