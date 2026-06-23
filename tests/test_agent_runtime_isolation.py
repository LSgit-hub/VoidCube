from __future__ import annotations

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
