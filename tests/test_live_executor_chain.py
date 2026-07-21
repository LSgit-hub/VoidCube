from __future__ import annotations

import asyncio
from pathlib import Path
import socket
import subprocess

import httpx
import pytest
import uvicorn

from systems.gateway.internal_gateway import GatewayConfig, InternalGateway
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
    return Supervisor(
        SupervisorConfig(
            host="127.0.0.1",
            port=port,
            execution=SupervisorExecutionConfig(
                gateway_address=gateway_url,
                git_repo_path=str(repo_path),
            ),
            service_runtime=SupervisorServiceRuntimeConfig(
                health_check_interval=health_check_interval,
                governor_llm_advisory_enabled=False,
                endogenous_drive_enabled=False,
            ),
        )
    )


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
