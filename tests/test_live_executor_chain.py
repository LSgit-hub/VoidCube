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


@pytest.mark.asyncio
@pytest.mark.integration
async def test_live_gateway_executor_propagates_body_integrity_degraded(tmp_path: Path):
    _create_git_repo(tmp_path)
    gateway_port = _free_port()
    supervisor_port = _free_port()
    gateway_url = f"http://127.0.0.1:{gateway_port}"

    gateway = InternalGateway(GatewayConfig(host="127.0.0.1", port=gateway_port))
    supervisor = Supervisor(
        SupervisorConfig(
            host="127.0.0.1",
            port=supervisor_port,
            execution=SupervisorExecutionConfig(
                gateway_address=gateway_url,
                git_repo_path=str(tmp_path),
            ),
            service_runtime=SupervisorServiceRuntimeConfig(
                governor_llm_advisory_enabled=False,
                autonomous_chain_start_on_boot=False,
                endogenous_drive_enabled=False,
            ),
        )
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
        for server in (supervisor_server, gateway_server):
            if server is not None:
                server.should_exit = True
        for task in (supervisor_task, gateway_task):
            if task is not None:
                await asyncio.wait_for(task, timeout=15)
