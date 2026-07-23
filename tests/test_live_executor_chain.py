from __future__ import annotations

import asyncio
from pathlib import Path
import socket
import subprocess
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn

from systems.gateway.internal_gateway import GatewayConfig, InternalGateway
from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import MemoryService
from plugins.memory.mem import MemMemoryProvider
from systems.supervisor.supervisor import (
    Supervisor,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _create_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text("# isolated executor chain\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "--initial-branch", "master"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=voidcube-test@example.invalid",
            "-c",
            "user.name=VoidCube isolated test",
            "commit",
            "-m",
            "isolated-baseline",
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _create_supervisor(
    repo_path: Path,
    *,
    gateway_url: str,
    port: int,
    health_check_interval: int = 30,
) -> Supervisor:
    config = SupervisorConfig(
            host="127.0.0.1",
            port=port,
            execution=SupervisorExecutionConfig(
                gateway_address=gateway_url,
                git_repo_path=str(repo_path),
            ),
            soul_store_path=str(repo_path / ".soul-runtime"),
            service_runtime=SupervisorServiceRuntimeConfig(
                health_check_interval=health_check_interval,
                governor_llm_advisory_enabled=False,
                endogenous_drive_enabled=False,
            ),
    )
    config.body_runtime.state_root = str(repo_path / "body-state")
    return Supervisor(config)


async def _start_uvicorn(app, port: int):
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            lifespan="on",
        )
    )
    task = asyncio.create_task(server.serve())
    for _ in range(120):
        if server.started:
            return server, task
        await asyncio.sleep(0.05)
    server.should_exit = True
    await task
    raise AssertionError(f"Uvicorn server did not start on port {port}")


async def _stop_uvicorn(server, task) -> None:
    if server is not None:
        server.should_exit = True
    if task is not None:
        await asyncio.wait_for(task, timeout=15)


async def _wait_for_gateway_service_types(
    gateway_url: str,
    expected: set[str],
    *,
    timeout: float = 15.0,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    async with httpx.AsyncClient(timeout=2.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await client.get(f"{gateway_url}/admin/services")
                if response.status_code == 200:
                    payload = response.json()
                    service_types = {
                        item["service_type"] for item in payload["services"]
                    }
                    if expected.issubset(service_types):
                        return payload
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise AssertionError(
        f"Gateway did not register service types {sorted(expected)} within {timeout}s"
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_live_gateway_executor_propagates_body_integrity_degraded(tmp_path: Path):
    _create_git_repo(tmp_path)
    gateway_port = _free_port()
    supervisor_port = _free_port()
    gateway_url = f"http://127.0.0.1:{gateway_port}"

    gateway = InternalGateway(GatewayConfig(host="127.0.0.1", port=gateway_port))
    supervisor = _create_supervisor(
        tmp_path,
        gateway_url=gateway_url,
        port=supervisor_port,
    )

    gateway_server = gateway_task = supervisor_server = supervisor_task = None
    try:
        gateway_server, gateway_task = await _start_uvicorn(gateway.app, gateway_port)
        supervisor_server, supervisor_task = await _start_uvicorn(
            supervisor.app,
            supervisor_port,
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            services = (await client.get(f"{gateway_url}/admin/services")).json()
            service_types = {item["service_type"] for item in services["services"]}
            assert {"supervisor", "executor"}.issubset(service_types)

            healthy = (
                await client.get(f"{gateway_url}/api/executor/health")
            ).json()
            assert healthy["status"] == "healthy"
            assert healthy["body_runtime"]["healthy"] is True

            manifest = supervisor._body_registry.slot_worktree_manifest_path("slot-A")
            original_manifest = manifest.read_text(encoding="utf-8")
            manifest.unlink()
            try:
                degraded_health = (
                    await client.get(f"{gateway_url}/api/executor/health")
                ).json()
                degraded_registry = (
                    await client.get(f"{gateway_url}/api/executor/body/registry")
                ).json()
            finally:
                manifest.write_text(original_manifest, encoding="utf-8")

            assert degraded_health["status"] == "degraded"
            assert degraded_health["body_runtime"]["healthy"] is False
            assert degraded_health["body_runtime"]["violations"][0]["code"] == (
                "slot_not_materialized"
            )
            assert degraded_registry["integrity"]["healthy"] is False
            assert degraded_registry["integrity"]["violations"][0]["code"] == (
                "slot_not_materialized"
            )
            assert manifest.exists()

            recovered = (
                await client.get(f"{gateway_url}/api/executor/health")
            ).json()
            assert recovered["status"] == "healthy"
            assert recovered["body_runtime"]["healthy"] is True
    finally:
        await _stop_uvicorn(supervisor_server, supervisor_task)
        await _stop_uvicorn(gateway_server, gateway_task)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.operational
async def test_live_three_service_lifespan_registration_recovery_and_shutdown(
    tmp_path: Path,
):
    _create_git_repo(tmp_path)
    gateway_port = _free_port()
    memory_port = _free_port()
    supervisor_port = _free_port()
    gateway_url = f"http://127.0.0.1:{gateway_port}"

    gateway = InternalGateway(GatewayConfig(host="127.0.0.1", port=gateway_port))
    memory = MemoryService(
        MemoryServiceConfig(
            host="127.0.0.1",
            port=memory_port,
            db_path=str(tmp_path / "memory" / "memory.db"),
            gateway_address=gateway_url,
            gateway_registration_check_interval=1,
            compression_interval=3600,
        )
    )
    memory._check_llm_health = AsyncMock(return_value=None)  # type: ignore[method-assign]
    supervisor = _create_supervisor(
        tmp_path,
        gateway_url=gateway_url,
        port=supervisor_port,
        health_check_interval=1,
    )

    gateway_server = gateway_task = None
    memory_server = memory_task = None
    supervisor_server = supervisor_task = None
    try:
        gateway_server, gateway_task = await _start_uvicorn(gateway.app, gateway_port)
        memory_server, memory_task = await _start_uvicorn(memory.app, memory_port)
        supervisor_server, supervisor_task = await _start_uvicorn(
            supervisor.app,
            supervisor_port,
        )

        services = await _wait_for_gateway_service_types(
            gateway_url,
            {"memory", "supervisor", "executor"},
        )
        memory_registration = next(
            item for item in services["services"] if item["service_type"] == "memory"
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            proxied_health = (await client.get(f"{gateway_url}/api/mem/")).json()
            assert proxied_health["service"] == "memory-service"
            assert proxied_health["gateway_registration"]["healthy"] is True

            trace_id = "live-three-service-trace"
            touched = await client.post(
                f"{gateway_url}/admin/activity/touch",
                json={
                    "activity_kind": "memory_task",
                    "source_service": "memory",
                    "metadata": {"trace_id": trace_id},
                },
            )
            assert touched.status_code == 200
            trace = (await client.get(f"{gateway_url}/admin/traces/{trace_id}")).json()
            assert trace["trace_id"] == trace_id
            assert trace["count"] == 1
            assert trace["events"][0]["metadata"]["trace_id"] == trace_id

            tier1_stats = await supervisor._fetch_tier1_stats()
            assert tier1_stats.get("memory_unavailable") is not True

            provider = MemMemoryProvider()
            provider._initialized = True
            provider._gateway_url = gateway_url
            provider._request_timeout_seconds = 5.0
            await asyncio.to_thread(
                provider._write_turn_pair,
                {
                    "session_id": "live agent session",
                    "user_content": "remember this question",
                    "assistant_content": "remember this answer",
                    "write_id": "live-write-1",
                },
            )
            turns = (
                await client.get(
                    f"{gateway_url}/api/mem/sessions/live%20agent%20session/turns"
                )
            ).json()
            assert [turn["speaker"] for turn in turns["turns"]] == ["user", "agent"]
            assert [turn["text"] for turn in turns["turns"]] == [
                "remember this question",
                "remember this answer",
            ]

            removed = await client.delete(
                f"{gateway_url}/admin/services/{memory_registration['service_id']}"
            )
            assert removed.status_code == 200

        restored = await _wait_for_gateway_service_types(gateway_url, {"memory"})
        restored_memory = next(
            item for item in restored["services"] if item["service_type"] == "memory"
        )
        assert restored_memory["service_id"] != memory_registration["service_id"]
        assert memory._gateway_service_id == restored_memory["service_id"]
        assert memory._gateway_registration_healthy is True

        await _stop_uvicorn(memory_server, memory_task)
        memory_server = memory_task = None
        assert memory._gateway_registration_task is not None
        assert memory._gateway_registration_task.done()
        assert memory._compression_task is not None
        assert memory._compression_task.done()

        unavailable = await supervisor._fetch_tier1_stats()
        assert unavailable["memory_unavailable"] is True
        assert unavailable["memory_active"] is False
        assert unavailable["memory_unavailable_reason"] == "ClientConnectorError"
    finally:
        await _stop_uvicorn(supervisor_server, supervisor_task)
        await _stop_uvicorn(memory_server, memory_task)
        await _stop_uvicorn(gateway_server, gateway_task)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.operational
async def test_live_supervisor_reregisters_executor_after_gateway_restart(tmp_path: Path):
    _create_git_repo(tmp_path)
    gateway_port = _free_port()
    supervisor_port = _free_port()
    gateway_url = f"http://127.0.0.1:{gateway_port}"

    first_gateway = InternalGateway(
        GatewayConfig(host="127.0.0.1", port=gateway_port)
    )
    supervisor = _create_supervisor(
        tmp_path,
        gateway_url=gateway_url,
        port=supervisor_port,
        health_check_interval=1,
    )

    gateway_server = gateway_task = supervisor_server = supervisor_task = None
    replacement_server = replacement_task = None
    try:
        gateway_server, gateway_task = await _start_uvicorn(
            first_gateway.app,
            gateway_port,
        )
        supervisor_server, supervisor_task = await _start_uvicorn(
            supervisor.app,
            supervisor_port,
        )

        first_services = await _wait_for_gateway_service_types(
            gateway_url,
            {"supervisor", "executor"},
        )
        first_ids = {
            item["service_type"]: item["service_id"]
            for item in first_services["services"]
            if item["service_type"] in {"supervisor", "executor"}
        }

        await _stop_uvicorn(gateway_server, gateway_task)
        gateway_server = gateway_task = None

        replacement_gateway = InternalGateway(
            GatewayConfig(host="127.0.0.1", port=gateway_port)
        )
        replacement_server, replacement_task = await _start_uvicorn(
            replacement_gateway.app,
            gateway_port,
        )
        second_services = await _wait_for_gateway_service_types(
            gateway_url,
            {"supervisor", "executor"},
        )
        second_ids = {
            item["service_type"]: item["service_id"]
            for item in second_services["services"]
            if item["service_type"] in {"supervisor", "executor"}
        }

        assert second_ids["supervisor"] != first_ids["supervisor"]
        assert second_ids["executor"] != first_ids["executor"]
        assert supervisor._gateway_service_id == second_ids["supervisor"]
        assert supervisor._gateway_executor_service_id == second_ids["executor"]

        async with httpx.AsyncClient(timeout=10.0) as client:
            executor_health = (
                await client.get(f"{gateway_url}/api/executor/health")
            ).json()
        assert executor_health["status"] == "healthy"
        assert executor_health["body_runtime"]["healthy"] is True
    finally:
        await _stop_uvicorn(supervisor_server, supervisor_task)
        await _stop_uvicorn(replacement_server, replacement_task)
        await _stop_uvicorn(gateway_server, gateway_task)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("removed_service_type", ["executor", "supervisor"])
async def test_live_supervisor_restores_only_missing_gateway_registration(
    tmp_path: Path,
    removed_service_type: str,
):
    _create_git_repo(tmp_path)
    gateway_port = _free_port()
    supervisor_port = _free_port()
    gateway_url = f"http://127.0.0.1:{gateway_port}"

    gateway = InternalGateway(GatewayConfig(host="127.0.0.1", port=gateway_port))
    supervisor = _create_supervisor(
        tmp_path,
        gateway_url=gateway_url,
        port=supervisor_port,
        health_check_interval=1,
    )

    gateway_server = gateway_task = supervisor_server = supervisor_task = None
    try:
        gateway_server, gateway_task = await _start_uvicorn(gateway.app, gateway_port)
        supervisor_server, supervisor_task = await _start_uvicorn(
            supervisor.app,
            supervisor_port,
        )

        first_services = await _wait_for_gateway_service_types(
            gateway_url,
            {"supervisor", "executor"},
        )
        first_by_type = {
            item["service_type"]: item
            for item in first_services["services"]
            if item["service_type"] in {"supervisor", "executor"}
        }
        first_ids = {
            service_type: item["service_id"]
            for service_type, item in first_by_type.items()
        }
        retained_service_type = (
            "supervisor" if removed_service_type == "executor" else "executor"
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            removed = await client.delete(
                f"{gateway_url}/admin/services/{first_ids[removed_service_type]}"
            )
            assert removed.status_code == 200

            deadline = asyncio.get_running_loop().time() + 15
            restored_services = None
            while asyncio.get_running_loop().time() < deadline:
                payload = (
                    await client.get(f"{gateway_url}/admin/services")
                ).json()
                by_type = {
                    item["service_type"]: item
                    for item in payload["services"]
                    if item["service_type"] in {"supervisor", "executor"}
                }
                if (
                    removed_service_type in by_type
                    and by_type[removed_service_type]["service_id"]
                    != first_ids[removed_service_type]
                ):
                    restored_services = payload
                    break
                await asyncio.sleep(0.1)

            assert restored_services is not None
            relevant = [
                item
                for item in restored_services["services"]
                if item["service_type"] in {"supervisor", "executor"}
            ]
            restored_by_type = {item["service_type"]: item for item in relevant}
            assert len(relevant) == 2
            assert restored_by_type[retained_service_type]["service_id"] == (
                first_ids[retained_service_type]
            )
            assert restored_by_type[removed_service_type]["service_id"] != (
                first_ids[removed_service_type]
            )
            assert supervisor._gateway_service_id == (
                restored_by_type["supervisor"]["service_id"]
            )
            assert supervisor._gateway_executor_service_id == (
                restored_by_type["executor"]["service_id"]
            )

            executor_health = (
                await client.get(f"{gateway_url}/api/executor/health")
            ).json()
            assert executor_health["status"] == "healthy"
            assert executor_health["body_runtime"]["healthy"] is True
    finally:
        await _stop_uvicorn(supervisor_server, supervisor_task)
        await _stop_uvicorn(gateway_server, gateway_task)
