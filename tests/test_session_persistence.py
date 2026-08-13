from __future__ import annotations

import json
from datetime import datetime

import pytest

from run_agent import AIAgent
from agent.session_persistence import (
    SessionPersistence,
    apply_user_message_override,
    clean_session_content,
)


pytestmark = [pytest.mark.smoke, pytest.mark.unit]


class _SessionDB:
    def __init__(self) -> None:
        self.ensured: list[tuple[str, str, str]] = []
        self.messages: list[dict] = []
        self.sequences: dict[str, int] = {}

    def ensure_session(self, session_id: str, *, source: str, model: str) -> None:
        self.ensured.append((session_id, source, model))

    def get_flush_sequence(self, session_id: str) -> int:
        return self.sequences.get(session_id, 0)

    @staticmethod
    def stable_message_id(session_id, sequence_no, message) -> str:
        del message
        return f"msg:{session_id}:{sequence_no}"

    def append_messages_batch(self, session_id: str, messages: list[dict]) -> None:
        self.messages.extend({"session_id": session_id, **message} for message in messages)
        if messages:
            self.sequences[session_id] = max(message["sequence_no"] for message in messages)

    def get_messages_as_conversation(self, session_id: str) -> list[dict]:
        return [
            {
                key: value
                for key, value in message.items()
                if key not in {"session_id", "sequence_no", "message_id"}
                and value is not None
            }
            for message in self.messages
            if message["session_id"] == session_id
        ]


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


def test_sqlite_flush_sequence_prevents_duplicate_db_writes(tmp_path):
    session_db = _SessionDB()
    persistence, _ = _persistence(tmp_path, session_db=session_db)
    history = [{"role": "user", "content": "already stored"}]
    messages = [*history, {"role": "assistant", "content": "new"}]
    session_db.sequences["session-1"] = 1

    persistence.persist(messages, history)
    persistence.persist(messages, history)

    assert [message["content"] for message in session_db.messages] == ["new"]


def test_json_mirror_failure_does_not_rollback_sqlite_batch(tmp_path, monkeypatch):
    session_db = _SessionDB()
    persistence, _ = _persistence(tmp_path, session_db=session_db)
    monkeypatch.setattr(
        persistence,
        "save_log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    persistence.persist([{"role": "user", "content": "committed"}])

    assert [message["content"] for message in session_db.messages] == ["committed"]
    assert session_db.sequences["session-1"] == 1


def test_new_session_flush_sequence_starts_from_first_message(tmp_path):
    session_db = _SessionDB()
    persistence, state = _persistence(tmp_path, session_db=session_db)
    persistence.persist([{"role": "user", "content": "old"}])

    state["session_id"] = "session-2"
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


def test_explicit_history_mutation_can_replace_longer_transcript(tmp_path):
    persistence, _ = _persistence(tmp_path)
    persistence.save_log(
        [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]
    )

    persistence.save_log(
        [{"role": "user", "content": "replacement"}],
        allow_truncate=True,
    )

    saved = json.loads(persistence.session_log_file.read_text(encoding="utf-8"))
    assert saved["messages"] == [{"role": "user", "content": "replacement"}]


def test_explicit_history_mutation_can_clear_transcript(tmp_path):
    persistence, _ = _persistence(tmp_path)
    persistence.save_log(
        [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]
    )

    persistence.save_log([], allow_truncate=True)

    saved = json.loads(persistence.session_log_file.read_text(encoding="utf-8"))
    assert saved["message_count"] == 0
    assert saved["messages"] == []
    assert persistence.messages == []


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


def test_agent_activate_session_resets_runtime(monkeypatch):
    class _Persistence:
        def __init__(self) -> None:
            self.session_start = datetime(2026, 7, 1)

    class _TodoStore:
        pass

    agent = AIAgent.__new__(AIAgent)
    agent.session_id = "old-session"
    agent.session_start = datetime(2026, 7, 1)
    agent._session_persistence = _Persistence()
    agent._todo_store = object()
    agent._cached_system_prompt = "cached"
    monkeypatch.setattr("tools.todo_tool.TodoStore", _TodoStore)
    started_at = datetime(2026, 7, 29, 20, 3, 0)

    agent.activate_session("new-session", session_start=started_at)

    assert agent.session_id == "new-session"
    assert agent.session_start == started_at
    assert agent._session_persistence.session_start == started_at
    assert isinstance(agent._todo_store, _TodoStore)
    assert agent._cached_system_prompt is None
    assert agent.session_total_tokens == 0


def test_agent_refreshes_json_mirror_after_history_mutation() -> None:
    calls: list[bool] = []
    persistence = type(
        "Persistence",
        (),
        {
            "refresh_json_mirror": lambda self: calls.append(True)
        },
    )()
    agent = AIAgent.__new__(AIAgent)
    agent._session_persistence = persistence
    history = [{"role": "user", "content": "remaining"}]

    agent.replace_persisted_session_history(history)

    assert calls == [True]


def test_agent_persists_compressed_continuation_history() -> None:
    calls: list[list[dict]] = []
    persistence = type(
        "Persistence",
        (),
        {"persist": lambda self, messages: calls.append(messages)},
    )()
    agent = AIAgent.__new__(AIAgent)
    agent._session_persistence = persistence
    history = [{"role": "assistant", "content": "summary"}]

    agent.persist_compressed_session_history(history)

    assert calls == [history]
