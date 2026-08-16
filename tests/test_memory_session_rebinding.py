from __future__ import annotations

from datetime import datetime

import pytest

from agent.memory_manager import MemoryManager
from plugins.memory.mem import MemMemoryProvider
from run_agent import AIAgent


pytestmark = [pytest.mark.smoke, pytest.mark.unit]


class _Persistence:
    session_start = datetime(2026, 7, 1)

    def set_session_id(self, session_id: str) -> None:
        self.session_id = session_id


class _TodoStore:
    pass


def _resume_with_memory(monkeypatch):
    calls = []
    provider = MemMemoryProvider()
    provider._initialized = True
    provider.bind_session("before-resume")
    monkeypatch.setattr(
        provider,
        "_request_json",
        lambda method, path, payload=None: calls.append((method, path, payload)) or {},
    )
    manager = MemoryManager()
    manager.add_provider(provider)

    agent = AIAgent.__new__(AIAgent)
    agent.session_id = "before-resume"
    agent.session_start = datetime(2026, 7, 1)
    agent._session_persistence = _Persistence()
    agent._memory_manager = manager
    agent._cached_system_prompt = "cached"
    monkeypatch.setattr("tools.todo_tool.TodoStore", _TodoStore)

    agent.activate_session(
        "resumed-session",
        session_start=datetime(2026, 7, 29, 20, 3),
    )
    return manager, provider, calls


def test_resume_rebinds_mem_search_to_target_session(monkeypatch):
    manager, provider, calls = _resume_with_memory(monkeypatch)

    manager.handle_tool_call("mem_search", {"query": "deployment"})

    assert provider._session_id == "resumed-session"
    assert calls == [
        (
            "POST",
            "/recall",
            {
                "query": "deployment",
                "current_session_id": "resumed-session",
                "request_source": "tool",
                "owner_id": "local-user",
                "workspace_id": "default",
                "memory_domain": "agent_interaction",
            },
        )
    ]


def test_resume_rebinds_mem_remember_evidence_to_target_session(monkeypatch):
    manager, provider, calls = _resume_with_memory(monkeypatch)

    manager.handle_tool_call(
        "mem_remember",
        {
            "title": "Deployment complete",
            "summary": "The resumed session verified deployment.",
            "evidence_refs": ["turn:resumed-turn"],
        },
    )

    assert provider._session_id == "resumed-session"
    assert calls[0][1] == "/remember"
    assert calls[0][2]["evidence_refs"] == [
        "turn:resumed-turn",
        "session:resumed-session",
    ]
    assert "session:before-resume" not in calls[0][2]["evidence_refs"]
