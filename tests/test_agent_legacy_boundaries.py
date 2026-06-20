from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.agent.run_agent_instance import AgentConfig, AgentInstance


@pytest.mark.unit
def test_agent_does_not_expose_legacy_self_improve_route(tmp_path):
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
            body_version="bootstrap",
        )
    )

    route_paths = {route.path for route in agent.app.routes}

    assert "/self-improve" not in route_paths
