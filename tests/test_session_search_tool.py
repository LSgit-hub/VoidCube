from __future__ import annotations

import json

import pytest

from agent.tool_execution import PreparedToolCall
from run_agent import AIAgent
from tools.session_search_tool import SESSION_SEARCH_SCHEMA, session_search
from tools.toolsets import resolve_toolset


pytestmark = [pytest.mark.smoke, pytest.mark.unit]


class _SessionDB:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def search_messages(self, **kwargs):
        self.calls.append(kwargs)
        return [
            {"session_id": "current", "role": "user", "snippet": "first"},
            {"session_id": "previous", "role": "assistant", "snippet": "second"},
        ]


def test_schema_and_toolset_expose_the_same_session_search_contract():
    assert SESSION_SEARCH_SCHEMA["name"] == "session_search"
    assert "session_search" in resolve_toolset("session_search")
    assert "role_filter" in SESSION_SEARCH_SCHEMA["parameters"]["properties"]


def test_session_search_calls_database_and_marks_current_session():
    db = _SessionDB()

    result = json.loads(
        session_search(
            "deployment",
            db=db,
            role_filter=["user", "assistant"],
            limit=100,
            current_session_id="current",
        )
    )

    assert db.calls == [
        {
            "query": "deployment",
            "role_filter": ["user", "assistant"],
            "limit": 50,
        }
    ]
    assert result["success"] is True
    assert result["count"] == 2
    assert [item["is_current_session"] for item in result["results"]] == [True, False]


def test_session_search_rejects_empty_query_without_calling_database():
    db = _SessionDB()

    result = json.loads(session_search("  ", db=db))

    assert result == {"success": False, "error": "query is required"}
    assert db.calls == []


def test_agent_route_injects_session_database_and_current_session():
    db = _SessionDB()
    agent = AIAgent.__new__(AIAgent)
    agent._session_db = db
    agent.session_id = "current"
    call = PreparedToolCall(
        source=None,
        position=1,
        call_id="call-session-search",
        name="session_search",
        arguments={"query": "deployment", "role_filter": ["assistant"]},
    )

    result = json.loads(
        agent._route_tool_call(call, messages=[], effective_task_id="task-search")
    )

    assert db.calls == [
        {"query": "deployment", "role_filter": ["assistant"], "limit": 10}
    ]
    assert result["results"][0]["is_current_session"] is True