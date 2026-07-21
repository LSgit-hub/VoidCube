from __future__ import annotations

import json
from datetime import datetime

import pytest

from run_agent import AIAgent
from agent.session_persistence import (
    SessionPersistence,
    apply_user_message_override,
    clean_session_content,
    messages_before_last_assistant,
)


pytestmark = [pytest.mark.smoke, pytest.mark.unit]


class _SessionDB:
    def __init__(self) -> None:
        self.ensured: list[tuple[str, str, str]] = []
        self.messages: list[dict] = []

    def ensure_session(self, session_id: str, *, source: str, model: str) -> None:
        self.ensured.append((session_id, source, model))

    def append_message(self, **message) -> None:
        self.messages.append(message)


def _persistence(tmp_path, *, session_db=None, enabled=True, override=(None, None)):
    state = {
        "session_id": "session-1",
        "model": "model-a",
        "base_url": "https://api.example/v1",
        "platform": "cli",
        "system_prompt": "system prompt",
        "tools": [{"type": "function", "function": {"name": "read_file"}}],
        "override": override,
    }
    persistence = SessionPersistence(
        enabled=enabled,
        logs_dir=tmp_path / "sessions",
        session_db=session_db,
        session_start=datetime(2026, 7, 21, 12, 0, 0),
        session_id=lambda: state["session_id"],
        model=lambda: state["model"],
        base_url=lambda: state["base_url"],
        platform=lambda: state["platform"],
        system_prompt=lambda: state["system_prompt"],
        tools=lambda: state["tools"],
        user_message_override=lambda: state["override"],
    )
    return persistence, state


def test_apply_user_message_override_only_changes_target_user_message():
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "api-only prefix"},
    ]

    apply_user_message_override(messages, 1, "original user text")
    apply_user_message_override(messages, 0, "must not replace system")

    assert messages == [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "original user text"},
    ]


def test_messages_before_last_assistant_returns_independent_prefix():
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "first"},
        {"role": "tool", "content": "result"},
        {"role": "assistant", "content": "second"},
    ]

    prefix = messages_before_last_assistant(messages)

    assert prefix == messages[:3]
    assert prefix is not messages


def test_persist_restores_user_text_and_flushes_only_new_messages(tmp_path):
    session_db = _SessionDB()
    persistence, _ = _persistence(
        tmp_path,
        session_db=session_db,
        override=(0, "original user text"),
    )
    messages = [{"role": "user", "content": "temporary API text"}]

    persistence.persist(messages)
    messages.append(
        {
            "role": "assistant",
            "content": " answer ",
            "reasoning": "checked",
            "reasoning_details": [{"type": "summary", "text": "checked"}],
        }
    )
    persistence.persist(messages)

    assert messages[0]["content"] == "original user text"
    assert [message["role"] for message in session_db.messages] == [
        "user",
        "assistant",
    ]
    assert session_db.messages[-1]["reasoning"] == "checked"
    saved = json.loads(persistence.session_log_file.read_text(encoding="utf-8"))
    assert saved["message_count"] == 2
    assert saved["messages"][0] == messages[0]
    assert saved["messages"][1] == {**messages[1], "content": "answer"}


def test_conversation_history_and_cursor_prevent_duplicate_db_writes(tmp_path):
    session_db = _SessionDB()
    persistence, _ = _persistence(tmp_path, session_db=session_db)
    history = [{"role": "user", "content": "already stored"}]
    messages = [*history, {"role": "assistant", "content": "new"}]

    persistence.persist(messages, history)
    persistence.persist(messages, history)

    assert [message["content"] for message in session_db.messages] == ["new"]


def test_reset_flush_cursor_writes_new_session_from_first_message(tmp_path):
    session_db = _SessionDB()
    persistence, state = _persistence(tmp_path, session_db=session_db)
    persistence.persist([{"role": "user", "content": "old"}])

    state["session_id"] = "session-2"
    persistence.reset_flush_cursor()
    persistence.persist([{"role": "user", "content": "new"}])

    assert [message["session_id"] for message in session_db.messages] == [
        "session-1",
        "session-2",
    ]


def test_save_log_does_not_replace_longer_existing_transcript(tmp_path):
    persistence, _ = _persistence(tmp_path)
    long_history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
    ]
    persistence.save_log(long_history)

    persistence.save_log([{"role": "user", "content": "partial"}])

    saved = json.loads(persistence.session_log_file.read_text(encoding="utf-8"))
    assert saved["messages"] == long_history


def test_disabled_persistence_does_not_write_or_mutate_messages(tmp_path):
    persistence, _ = _persistence(
        tmp_path,
        enabled=False,
        override=(0, "original"),
    )
    messages = [{"role": "user", "content": "api-only"}]

    persistence.persist(messages)
    persistence.save_log(messages)
    persistence.flush_to_db(messages)

    assert messages[0]["content"] == "api-only"
    assert persistence.messages == []
    assert not persistence.session_log_file.exists()


def test_clean_session_content_normalizes_think_block_spacing():
    assert clean_session_content("before\n\n<think>x</think>\n\nanswer") == (
        "before\n<think>x</think>\nanswer"
    )


def test_agent_class_does_not_keep_legacy_session_persistence_methods():
    assert not hasattr(AIAgent, "_persist_session")
    assert not hasattr(AIAgent, "_flush_messages_to_session_db")
    assert not hasattr(AIAgent, "_save_session_log")
