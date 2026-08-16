"""Run isolated multi-process Memory outbox HTTP and recovery smoke checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import uvicorn

from plugins.memory.mem import MemMemoryProvider
from plugins.memory.mem.outbox import MemoryWriteOutbox
from systems.gateway.internal_gateway import GatewayConfig, InternalGateway
from memai.application.memory_service import MemoryService, MemoryServiceConfig


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _start_uvicorn(app: Any, port: int) -> tuple[uvicorn.Server, asyncio.Task]:
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
    for _ in range(160):
        if server.started:
            return server, task
        await asyncio.sleep(0.05)
    server.should_exit = True
    await task
    raise RuntimeError(f"Uvicorn server did not start on port {port}")


async def _stop_uvicorn(
    server: uvicorn.Server | None,
    task: asyncio.Task | None,
) -> None:
    if server is not None:
        server.should_exit = True
    if task is not None:
        await asyncio.wait_for(task, timeout=15)


async def _wait_for_memory_registration(gateway_url: str) -> None:
    deadline = asyncio.get_running_loop().time() + 20
    async with httpx.AsyncClient(timeout=2.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            response = await client.get(f"{gateway_url}/admin/services")
            services = response.json().get("services", [])
            if any(
                item.get("service_type") == "memory" and item.get("healthy")
                for item in services
            ):
                return
            await asyncio.sleep(0.1)
    raise RuntimeError("Memory Service was not registered as healthy")


def _run_child(arguments: list[str], *, timeout: float = 30.0) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Outbox smoke child failed ({completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    return json.loads(completed.stdout.strip())


def _run_children_concurrently(
    argument_sets: list[list[str]],
    *,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    processes = [
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for arguments in argument_sets
    ]
    results: list[dict[str, Any]] = []
    for process in processes:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("Outbox smoke child timed out") from None
        if process.returncode != 0:
            raise RuntimeError(
                f"Outbox smoke child failed ({process.returncode}): {stderr.strip()}"
            )
        results.append(json.loads(stdout.strip()))
    return results


def _consume_child(
    path: str,
    worker_id: str,
    *,
    lease_seconds: float,
    retry_base_seconds: float,
) -> dict[str, Any]:
    outbox = MemoryWriteOutbox(
        path,
        lease_seconds=lease_seconds,
        retry_base_seconds=retry_base_seconds,
    )
    claimed: list[str] = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        item = outbox.next_due()
        if item is None:
            if outbox.pending_count() == 0:
                break
            time.sleep(0.01)
            continue
        write_id = str(item["write_id"])
        claimed.append(write_id)
        outbox.mark_delivered(write_id)
    return {"worker_id": worker_id, "claimed": claimed}


def _report_child(path: str, gateway_url: str, session_id: str) -> dict[str, Any]:
    provider = MemMemoryProvider()
    provider._gateway_url = gateway_url
    provider._request_timeout_seconds = 5.0
    provider._session_id = session_id
    provider._outbox = MemoryWriteOutbox(path)
    provider._outbox.enqueue(
        {
            "write_id": f"{session_id}-write",
            "session_id": session_id,
            "user_content": "HTTP smoke question",
            "assistant_content": "HTTP smoke answer",
        }
    )
    provider._report_outbox_health_if_due(force=True)
    return {
        "session_id": session_id,
        "outbox_id": provider._outbox.outbox_id,
        "status": provider.outbox_status(),
    }


async def run_http_health_smoke() -> dict[str, Any]:
    """Exercise two provider processes through real Gateway and Memory sockets."""
    gateway_port = _free_port()
    memory_port = _free_port()
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    with tempfile.TemporaryDirectory(prefix="voidcube-http-smoke-") as temp:
        gateway = InternalGateway(
            GatewayConfig(host="127.0.0.1", port=gateway_port)
        )
        memory = MemoryService(
            MemoryServiceConfig(
                host="127.0.0.1",
                port=memory_port,
                db_path=str(Path(temp) / "memory.db"),
                gateway_address=gateway_url,
                gateway_registration_check_interval=1,
                compression_interval=3600,
            )
        )
        memory._check_llm_health = AsyncMock(return_value=None)
        gateway_server = gateway_task = memory_server = memory_task = None
        try:
            gateway_server, gateway_task = await _start_uvicorn(
                gateway.app,
                gateway_port,
            )
            memory_server, memory_task = await _start_uvicorn(
                memory.app,
                memory_port,
            )
            await _wait_for_memory_registration(gateway_url)
            reports = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        _run_child,
                        [
                            "--child-report",
                            str(Path(temp) / f"agent-{index}" / "outbox.sqlite3"),
                            gateway_url,
                            f"http-agent-{index}",
                        ],
                    )
                    for index in range(2)
                )
            )
            async with httpx.AsyncClient(timeout=5.0) as client:
                registration = await client.post(
                    f"{gateway_url}/v1/sessions/register",
                    json={
                        "session_id": "http-health-reader",
                        "source": "outbox_smoke",
                        "owner_id": "local-user",
                        "workspace_id": "default",
                    },
                )
                registration.raise_for_status()
                health = await client.get(
                    f"{gateway_url}/api/mem/health",
                    headers={
                        "X-VoidCube-Session-Id": "http-health-reader",
                        "X-VoidCube-Session-Token": registration.json()[
                            "session_token"
                        ],
                    },
                )
                health.raise_for_status()
            payload = health.json()
            agent_outbox = payload["agent_outbox"]
            if agent_outbox["reporter_count"] != 2:
                raise RuntimeError(f"Expected two outbox reporters: {agent_outbox}")
            if agent_outbox["pending_count"] != 2:
                raise RuntimeError(f"Expected two pending writes: {agent_outbox}")
            if agent_outbox["dead_letter_count"] != 0:
                raise RuntimeError(f"Unexpected dead letters: {agent_outbox}")
            return {
                "provider_reports": reports,
                "memory_status": payload["status"],
                "agent_outbox": {
                    key: agent_outbox[key]
                    for key in (
                        "reporter_count",
                        "pending_count",
                        "dead_letter_count",
                        "status",
                    )
                },
            }
        finally:
            await _stop_uvicorn(memory_server, memory_task)
            await _stop_uvicorn(gateway_server, gateway_task)


def run_recovery_soak(
    *,
    duration_seconds: float = 180.0,
    interval_seconds: float = 5.0,
    batch_size: int = 20,
) -> dict[str, Any]:
    """Repeat backlog, retry, dead-letter, requeue, and drain cycles."""
    duration = max(0.0, float(duration_seconds))
    interval = max(0.0, float(interval_seconds))
    bounded_batch = max(2, int(batch_size))
    cycles = writes = duplicate_claims = 0
    deadline = time.monotonic() + duration
    with tempfile.TemporaryDirectory(prefix="voidcube-outbox-soak-") as temp:
        path = str(Path(temp) / "outbox.sqlite3")
        outbox = MemoryWriteOutbox(
            path,
            max_attempts=2,
            lease_seconds=2,
            retry_base_seconds=0.01,
            retry_max_seconds=0.02,
        )
        first_cycle = True
        while first_cycle or time.monotonic() < deadline:
            first_cycle = False
            cycle = cycles
            retry_id = f"cycle-{cycle}-retry"
            outbox.enqueue(
                {
                    "write_id": retry_id,
                    "session_id": "soak",
                    "user_content": "retry",
                    "assistant_content": "retry",
                }
            )
            claimed = outbox.next_due()
            if claimed is None or claimed["write_id"] != retry_id:
                raise RuntimeError("Retry probe was not claimed")
            outbox.mark_failed(retry_id, attempts=1, error="transient failure")
            time.sleep(0.012)
            retried = outbox.next_due()
            if retried is None or retried["write_id"] != retry_id:
                raise RuntimeError("Retry probe did not become due")
            outbox.mark_failed(retry_id, attempts=2, error="permanent failure")
            if outbox.health_snapshot()["dead_letter_count"] != 1:
                raise RuntimeError("Retry probe did not enter the dead letter queue")
            if not outbox.requeue_dead_letter(retry_id):
                raise RuntimeError("Dead letter could not be requeued")

            for index in range(bounded_batch):
                outbox.enqueue(
                    {
                        "write_id": f"cycle-{cycle}-write-{index}",
                        "session_id": f"soak-{index % 2}",
                        "user_content": "question",
                        "assistant_content": "answer",
                    }
                )
            worker_results = _run_children_concurrently(
                [
                    [
                        "--child-consume",
                        path,
                        f"worker-{index}",
                        "2",
                        "0.01",
                    ]
                    for index in range(2)
                ]
            )
            claimed_ids = [
                write_id
                for result in worker_results
                for write_id in result["claimed"]
            ]
            duplicate_claims += len(claimed_ids) - len(set(claimed_ids))
            expected = bounded_batch + 1
            if len(claimed_ids) != expected or len(set(claimed_ids)) != expected:
                raise RuntimeError(f"Unexpected drain result: {worker_results}")
            health = outbox.health_snapshot()
            if any(
                int(health[key]) != 0
                for key in ("pending_count", "inflight_count", "dead_letter_count")
            ):
                raise RuntimeError(f"Outbox did not drain cleanly: {health}")
            cycles += 1
            writes += expected
            remaining = deadline - time.monotonic()
            if remaining > 0 and interval:
                time.sleep(min(interval, remaining))
        return {
            "duration_seconds": duration,
            "cycles": cycles,
            "writes_delivered": writes,
            "duplicate_claims": duplicate_claims,
            "final_outbox": outbox.health_snapshot(),
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("http", "soak", "all"), default="all")
    parser.add_argument("--duration-seconds", type=float, default=180.0)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--child-report", nargs=3, metavar=("PATH", "GATEWAY", "SESSION"))
    parser.add_argument(
        "--child-consume",
        nargs=4,
        metavar=("PATH", "WORKER", "LEASE_SECONDS", "RETRY_BASE_SECONDS"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.child_report:
        print(json.dumps(_report_child(*args.child_report), ensure_ascii=False))
        return 0
    if args.child_consume:
        path, worker, lease, retry = args.child_consume
        print(
            json.dumps(
                _consume_child(
                    path,
                    worker,
                    lease_seconds=float(lease),
                    retry_base_seconds=float(retry),
                ),
                ensure_ascii=False,
            )
        )
        return 0

    result: dict[str, Any] = {}
    if args.mode in {"http", "all"}:
        result["http"] = asyncio.run(run_http_health_smoke())
    if args.mode in {"soak", "all"}:
        result["soak"] = run_recovery_soak(
            duration_seconds=args.duration_seconds,
            interval_seconds=args.interval_seconds,
            batch_size=args.batch_size,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
