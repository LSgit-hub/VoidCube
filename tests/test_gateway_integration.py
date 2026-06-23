from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
import pytest_asyncio
import aiohttp

from systems.gateway import (
    InternalGateway,
    GatewayConfig,
    GatewayAgentAdapter,
    AgentProxy,
    create_agent,
    is_gateway_available,
)
from systems.supervisor.supervisor import (
    AgentInstance,
    Supervisor,
    SupervisorConfig,
    SupervisorExecutionConfig,
)


def _seed_probe_ready_body_repo(repo_root: Path) -> None:
    (repo_root / "systems" / "agent").mkdir(parents=True)
    source_agent = Path(__file__).resolve().parents[1] / "systems" / "agent" / "run_agent_instance.py"
    (repo_root / "systems" / "agent" / "run_agent_instance.py").write_text(
        source_agent.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo_root / "run_agent.py").write_text("print('agent entrypoint')\n", encoding="utf-8")
    (repo_root / "config.yaml").write_text("model: test\n", encoding="utf-8")
    (repo_root / "tools").mkdir(exist_ok=True)
    (repo_root / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (repo_root / "model_tools.py").write_text("# probe smoke\n", encoding="utf-8")


pytestmark = pytest.mark.asyncio


class TestGatewayIntegration:
    @pytest_asyncio.fixture
    async def gateway(self, unused_tcp_port):
        config = GatewayConfig(host="127.0.0.1", port=unused_tcp_port)
        gateway = InternalGateway(config)
        gateway_task = asyncio.create_task(gateway.start())

        gateway_url = f"http://{config.host}:{config.port}"
        for _ in range(50):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{gateway_url}/") as response:
                        if response.status == 200:
                            break
            except aiohttp.ClientError:
                await asyncio.sleep(0.1)
        else:
            gateway_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await gateway_task
            raise AssertionError("Gateway test server did not become ready in time")

        yield gateway

        gateway_task.cancel()
        try:
            await gateway_task
        except asyncio.CancelledError:
            pass

    @pytest_asyncio.fixture
    async def adapter(self, gateway):
        gateway_url = f"http://{gateway.config.host}:{gateway.config.port}"
        adapter = GatewayAgentAdapter(gateway_url)
        yield adapter
        await adapter.close()

    async def test_gateway_health_check(self, gateway, adapter):
        result = await adapter.health_check()
        
        assert result["status"] == "healthy"
        assert "gateway_id" in result
        assert "timestamp" in result
        assert "request_count" in result
        assert "registered_services" in result
        assert "active_body" in result
        assert "body_slots" in result
        assert "body_routing" in result

    async def test_service_registration(self, gateway, adapter):
        session = await adapter._get_session()
        
        response = await session.post(
            f"{adapter.gateway_url}/register",
            json={
                "service_name": "test-agent",
                "service_type": "agent",
                "address": "http://localhost:8080",
                "metadata": {"slot_id": "slot-A", "body_version": "bootstrap"},
            },
        )
        
        assert response.status == 201
        data = await response.json()
        assert "service_id" in data
        assert data["status"] == "registered"
        
        health_result = await adapter.health_check()
        assert health_result["registered_services"]["agents"] == 1

    async def test_agent_query_proxy(self, gateway, adapter):
        session = await adapter._get_session()
        
        await session.post(
            f"{adapter.gateway_url}/register",
            json={
                "service_name": "test-agent",
                "service_type": "agent",
                "address": "http://localhost:8080",
            },
        )
        
        try:
            result = await adapter.agent_query([{"role": "user", "content": "Hello"}])
            assert "session_id" in result
            assert "response" in result
            assert "metadata" in result
        except Exception:
            pass

    async def test_session_management(self, gateway, adapter):
        session = await adapter._get_session()
        await session.post(
            f"{adapter.gateway_url}/register",
            json={
                "service_name": "test-agent",
                "service_type": "agent",
                "address": "http://localhost:8080",
            },
        )
        try:
            await adapter.agent_query([{"role": "user", "content": "Hello"}])
        except Exception:
            pass
        session_info = await adapter.get_session_info()
        assert "session_id" in session_info
        assert "active_body_service_id" in session_info

    async def test_body_activation(self, gateway):
        gateway_url = f"http://{gateway.config.host}:{gateway.config.port}"
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{gateway_url}/register",
                json={
                    "service_id": "test-body-slot-A",
                    "service_name": "agent-slot-A",
                    "service_type": "agent",
                    "address": "http://localhost:8081",
                    "metadata": {"slot_id": "slot-A", "body_version": "bootstrap"},
                },
            )
            
            response = await session.post(
                f"{gateway_url}/admin/body/activate",
                json={"slot_id": "slot-A"},
            )
            
            assert response.status == 200
            data = await response.json()
            assert data["status"] == "activated"
            assert data["active_body"]["slot_id"] == "slot-A"
            
            status_response = await session.get(f"{gateway_url}/admin/body/status")
            status_data = await status_response.json()
            assert status_data["active_body"]["slot_id"] == "slot-A"

    async def test_supervisor_agent_registration_syncs_gateway_active_body_and_trace_activity(
        self,
        gateway,
        tmp_path,
    ):
        gateway_url = f"http://{gateway.config.host}:{gateway.config.port}"
        supervisor = Supervisor(
            SupervisorConfig(
                execution=SupervisorExecutionConfig(
                    git_repo_path=str(tmp_path),
                    gateway_address=gateway_url,
                    agent_base_port=9100,
                ),
                ui_enabled=False,
            )
        )
        slot_a_agent = AgentInstance(
            instance_id="agent-slot-A-instance",
            name="agent-slot-A",
            port=9101,
            status="running",
            healthy=True,
            version="v1",
            slot_id="slot-A",
        )
        slot_b_agent = AgentInstance(
            instance_id="agent-slot-B-instance",
            name="agent-slot-B",
            port=9102,
            status="running",
            healthy=True,
            version="v2",
            slot_id="slot-B",
        )
        supervisor._agents[slot_a_agent.instance_id] = slot_a_agent
        supervisor._agents[slot_b_agent.instance_id] = slot_b_agent

        service_a = await supervisor._register_agent_with_gateway(slot_a_agent)
        service_b = await supervisor._register_agent_with_gateway(slot_b_agent)
        activation = await supervisor._sync_gateway_body_activation(slot_b_agent.instance_id)

        assert service_a
        assert service_b
        assert slot_a_agent.gateway_service_id == service_a
        assert slot_b_agent.gateway_service_id == service_b
        assert activation["status"] == "activated"
        assert activation["active_body"]["slot_id"] == "slot-B"

        async with aiohttp.ClientSession() as session:
            health_response = await session.get(f"{gateway_url}/")
            body_status_response = await session.get(f"{gateway_url}/admin/body/status")
            routes_response = await session.get(f"{gateway_url}/admin/routes")
            touch_response = await session.post(
                f"{gateway_url}/admin/activity/touch",
                json={
                    "activity_kind": "self_evolution_execute",
                    "source_service": "executor",
                    "metadata": {
                        "trace_id": "trace-full-service-1",
                        "task_id": "task-full-service-1",
                        "decision_id": "decision-full-service-1",
                        "task_type": "self_evolution",
                        "kind": "body_switch",
                    },
                },
            )
            activity_log_response = await session.get(
                f"{gateway_url}/admin/activity/log",
                params={"trace_id": "trace-full-service-1"},
            )

            health = await health_response.json()
            body_status = await body_status_response.json()
            routes = await routes_response.json()
            touch = await touch_response.json()
            activity_log = await activity_log_response.json()

        assert health["active_body"]["slot_id"] == "slot-B"
        assert health["body_routing"]["api_route_target_instance"] == service_b
        assert body_status["active_body"]["service_id"] == service_b
        assert body_status["body_routing"]["active_slot_id"] == "slot-B"
        assert any(
            slot["slot_id"] == "slot-A" and slot["lifecycle_state"] == "draining"
            for slot in body_status["body_slots"]
        )
        api_route = next(route for route in routes["routes"] if route["path_prefix"] == "/api/")
        agent_route = next(route for route in routes["routes"] if route["path_prefix"] == "/agent/")
        assert api_route["target_instance"] == service_b
        assert agent_route["target_instance"] == service_b
        assert touch["status"] == "updated"
        assert activity_log["count"] == 1
        assert activity_log["events"][0]["activity_kind"] == "self_evolution_execute"
        assert activity_log["events"][0]["metadata"]["trace_id"] == "trace-full-service-1"
        assert activity_log["events"][0]["metadata"]["task_family"] == "body_switch"

    async def test_real_agent_process_serves_gateway_user_query_and_trace_activity(
        self,
        gateway,
        unused_tcp_port_factory,
        tmp_path,
    ):
        gateway_url = f"http://{gateway.config.host}:{gateway.config.port}"
        agent_port = unused_tcp_port_factory()
        runtime_root = tmp_path / "agent-runtime"
        logs_root = tmp_path / "agent-logs"
        worktree_root = tmp_path / "agent-worktree"
        worktree_root.mkdir()
        agent_script = (
            Path(__file__).resolve().parents[1]
            / "systems"
            / "agent"
            / "run_agent_instance.py"
        )
        env = os.environ.copy()
        env.pop("DEEPSEEK_API_KEY", None)
        env.update(
            {
                "GATEWAY_ADDRESS": gateway_url,
                "VOIDCUBE_ACTIVE_SLOT": "slot-B",
                "VOIDCUBE_BODY_WORKTREE": str(worktree_root),
                "VOIDCUBE_BODY_RUNTIME": str(runtime_root),
                "VOIDCUBE_BODY_LOGS": str(logs_root),
                "VOIDCUBE_BODY_VERSION": "v-real-agent",
            }
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(agent_script),
            "--host",
            "127.0.0.1",
            "--port",
            str(agent_port),
            "--gateway",
            gateway_url,
            env=env,
        )

        try:
            async with aiohttp.ClientSession() as session:
                for _ in range(80):
                    try:
                        async with session.get(f"http://127.0.0.1:{agent_port}/health") as response:
                            if response.status == 200:
                                health = await response.json()
                                if health["slot_id"] == "slot-B":
                                    break
                    except aiohttp.ClientError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("Agent process did not become healthy")

                for _ in range(80):
                    async with session.get(f"{gateway_url}/admin/body/status") as response:
                        status = await response.json()
                    active = status.get("active_body") or {}
                    if active.get("slot_id") == "slot-B":
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("Agent process did not register with gateway")

                query_response = await session.post(
                    f"{gateway_url}/v1/agent/query",
                    json={
                        "session_id": "real-agent-session-1",
                        "messages": [
                            {"role": "user", "content": "hello real runtime"},
                        ],
                        "metadata": {
                            "trace_id": "trace-real-agent-1",
                            "task_type": "user",
                        },
                    },
                )
                query = await query_response.json()
                log_response = await session.get(
                    f"{gateway_url}/admin/activity/log",
                    params={"trace_id": "trace-real-agent-1"},
                )
                activity_log = await log_response.json()

            assert query_response.status == 200
            assert query["session_id"] == "real-agent-session-1"
            assert query["response"]["slot_id"] == "slot-B"
            assert "hello real runtime" in query["response"]["response"]
            assert activity_log["count"] == 1
            assert activity_log["events"][0]["activity_kind"] == "user_request"
            assert activity_log["events"][0]["metadata"]["trace_id"] == "trace-real-agent-1"
            assert activity_log["events"][0]["session_id"] == "real-agent-session-1"
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

    async def test_body_upgrade_pipeline_starts_real_agent_and_syncs_gateway_active_body(
        self,
        gateway,
        tmp_path,
    ):
        _seed_probe_ready_body_repo(tmp_path)
        gateway_url = f"http://{gateway.config.host}:{gateway.config.port}"
        supervisor = Supervisor(
            SupervisorConfig(
                execution=SupervisorExecutionConfig(
                    git_repo_path=str(tmp_path),
                    gateway_address=gateway_url,
                    agent_base_port=9300,
                    probe_watch_window_seconds=30,
                ),
                ui_enabled=False,
            )
        )

        patched_env = {key: value for key, value in os.environ.items() if key != "DEEPSEEK_API_KEY"}
        with patch.dict(os.environ, patched_env, clear=True):
            result = await supervisor._execution_facade.execute_body_upgrade(
                {
                    "body_version": "v-real-pipeline",
                    "trace_id": "trace-real-pipeline-1",
                    "decision_id": "decision-real-pipeline-1",
                    "start_agent": True,
                    "wait_for_new_agent_healthy": True,
                    "new_agent_health_timeout": 10,
                    "start_agent_request": {},
                    "execution_request": {
                        "trace_id": "trace-real-pipeline-1",
                        "decision_id": "decision-real-pipeline-1",
                        "task_type": "self_evolution",
                        "kind": "body_switch",
                        "git_lineage": {
                            "candidate_commit": "pipeline-bbb222",
                            "rollback_commit": "pipeline-aaa111",
                            "changed_files": ["agent/stream_handler.py"],
                        },
                    },
                }
            )
        started_instance_id = result["started_agent"]["instance_id"]

        try:
            async with aiohttp.ClientSession() as session:
                for _ in range(80):
                    async with session.get(f"{gateway_url}/admin/body/status") as response:
                        body_status = await response.json()
                    active = body_status.get("active_body") or {}
                    if active.get("slot_id") == "slot-B":
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("Gateway did not activate the upgraded agent body")

                query_response = await session.post(
                    f"{gateway_url}/v1/agent/query",
                    json={
                        "session_id": "real-pipeline-session-1",
                        "messages": [
                            {"role": "user", "content": "hello upgraded body"},
                        ],
                        "metadata": {
                            "trace_id": "trace-real-pipeline-1",
                            "task_type": "user",
                        },
                    },
                )
                query = await query_response.json()
                activity_log_response = await session.get(
                    f"{gateway_url}/admin/activity/log",
                    params={"trace_id": "trace-real-pipeline-1"},
                )
                activity_log = await activity_log_response.json()

            assert result["status"] == "upgrade_executed"
            assert result["slot_id"] == "slot-B"
            assert result["started_agent"]["status"] == "started"
            assert result["gateway_activation"]["status"] == "activated"
            assert result["gateway_activation"]["active_body"]["slot_id"] == "slot-B"
            assert result["active_target"]["slot_id"] == "slot-B"
            assert query_response.status == 200
            assert query["response"]["slot_id"] == "slot-B"
            assert "hello upgraded body" in query["response"]["response"]
            activity_kinds = [event["activity_kind"] for event in activity_log["events"]]
            assert "user_request" in activity_kinds
        finally:
            await supervisor._execution_facade.stop_agent(started_instance_id)

    async def test_body_upgrade_real_agent_watch_window_pass_recycles_retired_slot(
        self,
        gateway,
        tmp_path,
    ):
        _seed_probe_ready_body_repo(tmp_path)
        gateway_url = f"http://{gateway.config.host}:{gateway.config.port}"
        supervisor = Supervisor(
            SupervisorConfig(
                execution=SupervisorExecutionConfig(
                    git_repo_path=str(tmp_path),
                    gateway_address=gateway_url,
                    agent_base_port=9400,
                    probe_watch_window_seconds=30,
                ),
                ui_enabled=False,
            )
        )
        patched_env = {key: value for key, value in os.environ.items() if key != "DEEPSEEK_API_KEY"}

        supervisor._body_registry.prepare_slot_workspace("slot-A", source_path=tmp_path)
        supervisor._body_registry.write_active_body_pointer("slot-A")
        with patch.dict(os.environ, patched_env, clear=True):
            old_start = await supervisor._execution_facade.start_managed_agent({})
            upgrade = await supervisor._execution_facade.execute_body_upgrade(
                {
                    "body_version": "v-watch-pass",
                    "trace_id": "trace-watch-pass-real-1",
                    "decision_id": "decision-watch-pass-real-1",
                    "start_agent": True,
                    "wait_for_new_agent_healthy": True,
                    "new_agent_health_timeout": 10,
                    "execution_request": {
                        "trace_id": "trace-watch-pass-real-1",
                        "decision_id": "decision-watch-pass-real-1",
                        "task_type": "self_evolution",
                        "kind": "body_switch",
                        "git_lineage": {
                            "candidate_commit": "watch-bbb222",
                            "rollback_commit": "watch-aaa111",
                            "changed_files": ["agent/stream_handler.py"],
                        },
                    },
                }
            )
        old_instance_id = old_start["instance_id"]
        new_instance_id = upgrade["started_agent"]["instance_id"]

        try:
            watch = await supervisor._execution_facade.evaluate_watch_window(
                {
                    "instance_id": new_instance_id,
                    "healthy_override": True,
                    "metrics": {"reason": "real_agent_watch_pass_smoke"},
                }
            )

            registry = await supervisor.get_body_registry()
            old_agent = supervisor._agents[old_instance_id]
            new_agent = supervisor._agents[new_instance_id]

            assert upgrade["status"] == "upgrade_executed"
            assert upgrade["previous_active_slot"] == "slot-A"
            assert upgrade["retired_slot"] == "slot-A"
            assert upgrade["gateway_activation"]["active_body"]["slot_id"] == "slot-B"
            assert watch["status"] == "watch_window_evaluated"
            assert watch["governor_response"]["decision"] == "approve"
            assert watch["execution_followup"] == {
                "action": "retired_slot_recycled",
                "slot_id": "slot-A",
                "stopped_instance_ids": [old_instance_id],
            }
            assert registry["registry"]["active_slot"] == "slot-B"
            assert registry["registry"]["shell_slot"] == "slot-A"
            assert registry["registry"]["retired_slot"] is None
            assert registry["slots"]["slot-A"]["body_state"] == "shell"
            assert registry["slots"]["slot-B"]["body_state"] == "active"
            assert old_agent.status in ("stopped", "exited"), f"expected stopped/exited, got {old_agent.status}"
            assert old_agent.pid is None
            assert new_agent.status == "running"
        finally:
            if supervisor._agents.get(new_instance_id) and supervisor._agents[new_instance_id].status == "running":
                await supervisor._execution_facade.stop_agent(new_instance_id)

    async def test_body_upgrade_real_agent_watch_window_failure_rolls_back_gateway_active_body(
        self,
        gateway,
        tmp_path,
    ):
        _seed_probe_ready_body_repo(tmp_path)
        gateway_url = f"http://{gateway.config.host}:{gateway.config.port}"
        supervisor = Supervisor(
            SupervisorConfig(
                execution=SupervisorExecutionConfig(
                    git_repo_path=str(tmp_path),
                    gateway_address=gateway_url,
                    agent_base_port=9500,
                    probe_watch_window_seconds=30,
                ),
                ui_enabled=False,
            )
        )
        patched_env = {key: value for key, value in os.environ.items() if key != "DEEPSEEK_API_KEY"}

        supervisor._body_registry.prepare_slot_workspace("slot-A", source_path=tmp_path)
        supervisor._body_registry.write_active_body_pointer("slot-A")
        with patch.dict(os.environ, patched_env, clear=True):
            old_start = await supervisor._execution_facade.start_managed_agent({})
            upgrade = await supervisor._execution_facade.execute_body_upgrade(
                {
                    "body_version": "v-watch-rollback",
                    "trace_id": "trace-watch-rollback-real-1",
                    "decision_id": "decision-watch-rollback-real-1",
                    "start_agent": True,
                    "wait_for_new_agent_healthy": True,
                    "new_agent_health_timeout": 10,
                    "execution_request": {
                        "trace_id": "trace-watch-rollback-real-1",
                        "decision_id": "decision-watch-rollback-real-1",
                        "task_type": "self_evolution",
                        "kind": "body_switch",
                        "git_lineage": {
                            "candidate_commit": "watch-rollback-bbb222",
                            "rollback_commit": "watch-rollback-aaa111",
                            "changed_files": ["agent/stream_handler.py"],
                        },
                    },
                }
            )
        old_instance_id = old_start["instance_id"]
        new_instance_id = upgrade["started_agent"]["instance_id"]

        try:
            watch = await supervisor._execution_facade.evaluate_watch_window(
                {
                    "instance_id": new_instance_id,
                    "healthy_override": False,
                    "metrics": {"reason": "real_agent_watch_failure_smoke"},
                }
            )

            async with aiohttp.ClientSession() as session:
                body_status_response = await session.get(f"{gateway_url}/admin/body/status")
                body_status = await body_status_response.json()
                query_response = await session.post(
                    f"{gateway_url}/v1/agent/query",
                    json={
                        "session_id": "watch-rollback-session-1",
                        "messages": [
                            {"role": "user", "content": "hello restored body"},
                        ],
                        "metadata": {
                            "trace_id": "trace-watch-rollback-real-1",
                            "task_type": "user",
                        },
                    },
                )
                query = await query_response.json()
                activity_log_response = await session.get(
                    f"{gateway_url}/admin/activity/log",
                    params={"trace_id": "trace-watch-rollback-real-1"},
                )
                activity_log = await activity_log_response.json()

            registry = await supervisor.get_body_registry()
            old_agent = supervisor._agents[old_instance_id]
            new_agent = supervisor._agents[new_instance_id]

            assert upgrade["status"] == "upgrade_executed"
            assert upgrade["gateway_activation"]["active_body"]["slot_id"] == "slot-B"
            assert watch["status"] == "watch_window_evaluated"
            assert watch["governor_response"]["decision"] == "rollback_required"
            assert watch["execution_followup"]["action"] == "failed_slot_drained"
            assert watch["execution_followup"]["slot_id"] == "slot-B"
            assert watch["execution_followup"]["restored_slot_id"] == "slot-A"
            assert watch["execution_followup"]["restored_instance_id"] == old_instance_id
            assert watch["execution_followup"]["gateway_activation"]["status"] == "activated"
            assert watch["execution_followup"]["gateway_activation"]["active_body"]["slot_id"] == "slot-A"
            assert watch["execution_followup"]["stopped_instance_ids"] == [new_instance_id]
            assert registry["registry"]["active_slot"] == "slot-A"
            assert registry["registry"]["retired_slot"] == "slot-B"
            assert registry["slots"]["slot-A"]["body_state"] == "active"
            assert registry["slots"]["slot-B"]["body_state"] == "retired"
            assert body_status_response.status == 200
            assert body_status["active_body"]["slot_id"] == "slot-A"
            assert query_response.status == 200
            assert query["response"]["slot_id"] == "slot-A"
            assert "hello restored body" in query["response"]["response"]
            assert "user_request" in [event["activity_kind"] for event in activity_log["events"]]
            assert old_agent.status == "running"
            assert new_agent.status == "stopped"
            assert new_agent.pid is None
        finally:
            if supervisor._agents.get(new_instance_id) and supervisor._agents[new_instance_id].status == "running":
                await supervisor._execution_facade.stop_agent(new_instance_id)
            if supervisor._agents.get(old_instance_id) and supervisor._agents[old_instance_id].status == "running":
                await supervisor._execution_facade.stop_agent(old_instance_id)


class TestAgentProxy:
    def test_create_proxy_local_mode(self):
        proxy = AgentProxy(mode="local")
        
        assert proxy.mode == "local"
        assert proxy.gateway_url == "http://localhost:6000"

    def test_create_proxy_gateway_mode(self):
        proxy = AgentProxy(mode="gateway", gateway_url="http://localhost:6001")
        
        assert proxy.mode == "gateway"
        assert proxy.gateway_url == "http://localhost:6001"

    def test_is_gateway_available(self):
        result = is_gateway_available("http://localhost:12345", timeout=1)
        
        assert isinstance(result, bool)

    def test_create_agent_function(self):
        agent = create_agent(
            mode="local",
            gateway_url="http://localhost:6000",
            model="test-model",
        )
        
        assert isinstance(agent, AgentProxy)
        assert agent.mode == "local"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
