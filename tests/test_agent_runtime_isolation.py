from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.agent.run_agent_instance import AgentConfig, AgentInstance


@pytest.mark.unit
def test_agent_instance_bootstraps_slot_runtime_directories(tmp_path):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7001,
            active_slot="slot-B",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="v2",
        )
    )

    paths = agent._runtime_paths
    assert Path(paths["runtime_root"]).exists()
    assert Path(paths["logs_root"]).exists()
    assert Path(paths["sessions_root"]).exists()
    assert Path(paths["cache_root"]).exists()
    assert Path(paths["state_root"]).exists()

    manifest = json.loads(Path(paths["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["slot_id"] == "slot-B"
    assert manifest["body_version"] == "v2"
    assert manifest["body_worktree"] == str(worktree_root)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_health_check_reports_body_runtime_identity(tmp_path):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7002,
            active_slot="slot-A",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="bootstrap",
        )
    )

    result = await agent.health_check()

    assert result["service_name"] == "agent-slot-A"
    assert result["slot_id"] == "slot-A"
    assert result["body_version"] == "bootstrap"
    assert result["body_runtime"] == str(runtime_root)
    assert result["enable_task_polling"] is False
    assert result["runtime_paths"]["sessions_root"].endswith("sessions")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_chat_persists_session_snapshot_into_slot_runtime(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7003,
            active_slot="slot-A",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="v3",
        )
    )

    async def fake_generate_response(message: str, context: dict) -> str:
        return f"echo:{message}"

    monkeypatch.setattr(agent, "_generate_response", fake_generate_response)

    result = await agent.handle_chat(
        {
            "message": "hello body",
            "session_id": "session-1",
            "context": {"source": "test"},
        }
    )

    assert result["slot_id"] == "slot-A"
    snapshot = runtime_root / "sessions" / "session-1.json"
    assert snapshot.exists()
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["slot_id"] == "slot-A"
    assert payload["body_version"] == "v3"
    assert payload["data"]["messages"][0]["content"] == "hello body"
    assert payload["data"]["messages"][1]["content"] == "echo:hello body"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_exposes_gateway_query_and_chat_completion_surfaces(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7004,
            active_slot="slot-B",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="v4",
        )
    )

    async def fake_generate_response(message: str, context: dict) -> str:
        return f"gateway-echo:{message}"

    monkeypatch.setattr(agent, "_generate_response", fake_generate_response)
    monkeypatch.setattr(
        agent,
        "_resolve_active_runtime",
        lambda: {
            "api_key": "runtime-key",
            "base_url": "https://runtime-base.example/v1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "api_mode": "chat_completions",
        },
    )

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "id": "cmpl-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            }

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *_args, **_kwargs):
            return _FakeResponse()

    monkeypatch.setattr("aiohttp.ClientSession", lambda: _FakeSession())

    query = await agent.handle_agent_query(
        {
            "session_id": "gateway-session-1",
            "messages": [
                {"role": "system", "content": "ignored"},
                {"role": "user", "content": "hello through gateway"},
            ],
        }
    )
    completion = await agent.handle_chat_completions(
        {
            "messages": [
                {"role": "user", "content": "hello completion"},
            ],
        }
    )

    assert query["response"] == "gateway-echo:hello through gateway"
    assert query["slot_id"] == "slot-B"
    assert query["body_version"] == "v4"
    assert query["agent_id"] == "agent-slot-B"
    assert "choices" in completion and len(completion["choices"]) > 0
    assert "message" in completion["choices"][0]
    assert "content" in completion["choices"][0]["message"]
    assert isinstance(completion["choices"][0]["message"]["content"], str)
    assert len(completion["choices"][0]["message"]["content"]) > 0
    assert completion["slot_id"] == "slot-B"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_task_polling_updates_learning_task_lifecycle_via_gateway(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7005,
            active_slot="slot-A",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="v5",
            enable_task_polling=True,
        )
    )

    updates = []

    sleep_calls = 0

    async def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    async def fake_execute(task: dict) -> dict:
        assert task["task_id"] == "learn-1"
        return {"status": "completed", "reason": "ok", "api_calls": 2, "tool_events": []}

    async def fake_update(task_id: str, *, decision: str, reason: str, actor: str, context=None) -> bool:
        updates.append(
            {
                "task_id": task_id,
                "decision": decision,
                "reason": reason,
                "actor": actor,
                "context": dict(context or {}),
            }
        )
        return True

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "tasks": [
                    {
                        "task_id": "learn-1",
                        "title": "Learn task",
                        "summary": "Investigate",
                        "governance_task_type": "self_learning",
                    }
                ]
            }

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, *_args, **_kwargs):
            return _FakeResponse()

    monkeypatch.setattr("aiohttp.ClientSession", lambda: _FakeSession())
    monkeypatch.setattr("systems.agent.run_agent_instance.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(agent, "_execute_approved_task", fake_execute)
    monkeypatch.setattr(agent, "_update_gateway_task_decision", fake_update)

    await asyncio.wait_for(agent._task_polling_loop(), timeout=1)

    assert [item["decision"] for item in updates] == ["running", "completed"]
    assert updates[0]["task_id"] == "learn-1"
    assert updates[0]["context"]["source"] == "agent_task_polling"
    assert updates[1]["context"]["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_chat_completions_uses_canonical_runtime_provider_config(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7006,
            active_slot="slot-A",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="v6",
        )
    )

    captured = {}

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "id": "cmpl-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            }

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["json"] = dict(json or {})
            return _FakeResponse()

    monkeypatch.setattr(
        "VoidCube_cli.config.load_config",
        lambda: {
            "runtime": {"active_provider": "agnesai"},
            "providers": {
                "agnesai": {
                    "selected_model": "agnes-2.0-flash",
                    "base_url": "https://config-base.example/v1",
                }
            },
        },
    )
    monkeypatch.setattr(
        "VoidCube_cli.runtime_provider.resolve_runtime_provider",
        lambda requested=None, explicit_api_key=None, explicit_base_url=None: {
            "provider": "agnesai",
            "api_mode": "chat_completions",
            "base_url": "https://runtime-base.example/v1",
            "api_key": "runtime-key",
        },
    )
    monkeypatch.setattr("aiohttp.ClientSession", lambda: _FakeSession())

    result = await agent.handle_chat_completions(
        {
            "messages": [{"role": "user", "content": "hello runtime"}],
        }
    )

    assert captured["url"] == "https://runtime-base.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer runtime-key"
    assert captured["json"]["model"] == "agnes-2.0-flash"
    assert result["slot_id"] == "slot-A"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_chat_completions_ignores_request_model_override_when_runtime_model_exists(
    tmp_path, monkeypatch
):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7010,
            active_slot="slot-A",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="v10",
        )
    )

    captured = {}

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "id": "cmpl-2",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            }

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["json"] = dict(json or {})
            return _FakeResponse()

    monkeypatch.setattr(
        agent,
        "_resolve_active_runtime",
        lambda: {
            "provider": "agnesai",
            "api_mode": "chat_completions",
            "base_url": "https://runtime-base.example/v1",
            "api_key": "runtime-key",
            "model": "agnes-2.0-flash",
        },
    )
    monkeypatch.setattr("aiohttp.ClientSession", lambda: _FakeSession())

    await agent.handle_chat_completions(
        {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "hello runtime"}],
        }
    )

    assert captured["json"]["model"] == "agnes-2.0-flash"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_executes_learning_task_with_main_agent_not_delegate_child(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7007,
            active_slot="slot-A",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="v7",
        )
    )

    built = {}

    class _FakeTaskAgent:
        model = "agnes-2.0-flash"

        def run_conversation(self, *, user_message, task_id):
            built["user_message"] = user_message
            built["task_id"] = task_id
            return {
                "final_response": '{"technology_evaluations": []}',
                "messages": [
                    {
                        "role": "tool",
                        "name": "web_search",
                        "tool_args": '{"q":"agent execution"}',
                        "content": "ok",
                    }
                ],
                "api_calls": 1,
                "failed": False,
                "partial": False,
            }

    async def fake_set_scene(scene_payload):
        built.setdefault("scenes", []).append(dict(scene_payload))
        return dict(scene_payload)

    monkeypatch.setattr(
        agent,
        "_build_task_agent",
        lambda task_id, execution_kind, toolsets: _FakeTaskAgent(),
    )
    monkeypatch.setattr(agent, "set_agent_scene", fake_set_scene)

    result = await agent._execute_approved_task(
        {
            "task_id": "learn-main-1",
            "title": "Learn directly",
            "summary": "Investigate direct execution path",
            "governance_task_type": "self_learning",
        }
    )

    assert result["status"] == "completed"
    assert result["model"] == "agnes-2.0-flash"
    assert result["tool_events"][0]["tool"] == "web_search"
    assert result["parsed_output"] == {"technology_evaluations": []}
    assert built["task_id"] == "agent-task-learn-main-1"
    assert built["scenes"][0]["scene"] == "learning"
    assert built["scenes"][-1]["scene"] == "idle"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_task_polling_fetches_learning_and_body_improvement_tasks(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7008,
            active_slot="slot-A",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="v8",
            enable_task_polling=True,
        )
    )

    calls = []

    class _FakeResponse:
        def __init__(self, payload):
            self.status = 200
            self._payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return self._payload

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, _url, params=None, timeout=None):
            del timeout
            calls.append(dict(params or {}))
            if params == {"task_type": "self_learning"}:
                return _FakeResponse(
                    {"tasks": [{"task_id": "learn-1", "governance_task_type": "self_learning"}]}
                )
            return _FakeResponse(
                {"tasks": [{"task_id": "improve-1", "execution_kind": "body_improvement"}]}
            )

    monkeypatch.setattr("aiohttp.ClientSession", lambda: _FakeSession())

    tasks = await agent._fetch_agent_executable_tasks()

    assert calls == [{"task_type": "self_learning"}, {"execution_kind": "body_improvement"}]
    assert [task["task_id"] for task in tasks] == ["learn-1", "improve-1"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_lifespan_does_not_start_task_polling_by_default(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7011,
            active_slot="slot-A",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="v11",
        )
    )

    started = {"called": False}

    async def fake_register():
        return "svc-1"

    async def fake_poll():
        started["called"] = True

    monkeypatch.setattr(agent, "register_with_gateway", fake_register)
    monkeypatch.setattr(agent, "_task_polling_loop", fake_poll)

    async with agent.app.router.lifespan_context(agent.app):
        assert agent._task_polling_task is None

    assert started["called"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_executes_body_improvement_task_with_code_editing_scene(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    logs_root = tmp_path / "logs"
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()

    agent = AgentInstance(
        AgentConfig(
            port=7009,
            active_slot="slot-A",
            body_worktree=str(worktree_root),
            body_runtime=str(runtime_root),
            body_logs=str(logs_root),
            body_version="v9",
        )
    )

    built = {}

    class _FakeTaskAgent:
        model = "agnes-2.0-flash"

        def run_conversation(self, *, user_message, task_id):
            built["user_message"] = user_message
            built["task_id"] = task_id
            return {
                "final_response": '{"changed_files": ["tools/example.py"], "implementation_summary": "updated"}',
                "messages": [],
                "api_calls": 1,
                "failed": False,
                "partial": False,
            }

    async def fake_set_scene(scene_payload):
        built.setdefault("scenes", []).append(dict(scene_payload))
        return dict(scene_payload)

    monkeypatch.setattr(agent, "_build_task_agent", lambda task_id, execution_kind, toolsets: _FakeTaskAgent())
    monkeypatch.setattr(agent, "set_agent_scene", fake_set_scene)

    result = await agent._execute_approved_task(
        {
            "task_id": "improve-main-1",
            "title": "Improve shell body",
            "summary": "Refactor a tool implementation",
            "governance_task_type": "self_evolution",
            "execution_kind": "body_improvement",
            "constraints": {"worktree_path": "F:/worktree", "editable_dirs": ["tools/"]},
        }
    )

    assert result["status"] == "completed"
    assert result["parsed_output"]["changed_files"] == ["tools/example.py"]
    assert "[AUTO Body Improvement Task]" in built["user_message"]
    assert built["scenes"][0]["scene"] == "code_editing"
    assert built["scenes"][-1]["scene"] == "idle"
