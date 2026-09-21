"""Lazy screen-perception owner for the room's explicit switch."""

from __future__ import annotations

from threading import RLock
from typing import Callable

from ..perception import PerceptionQueryService, PerceptionRuntime, PerceptionWorker


def create_runtime() -> PerceptionRuntime:
    from ...infrastructure.config.runtime_paths import get_VoidCube_home
    from ..perception import build_default_perception_runtime

    return build_default_perception_runtime(
        str(get_VoidCube_home() / "runtime" / "perception" / "timeline.sqlite3")
    )


class PerceptionControl:
    def __init__(self, *, factory: Callable[[], PerceptionRuntime] = create_runtime):
        self.factory = factory
        self.service: PerceptionQueryService | None = None
        self.worker: PerceptionWorker | None = None
        self._stopping = False
        self._authorized = False
        self._lock = RLock()

    def install(self, service: PerceptionQueryService) -> None:
        with self._lock:
            if self.worker and self.worker.status().running:
                raise RuntimeError("perception worker is still running")
            self.service = service
            self.worker = PerceptionWorker(service.runtime)
            self._stopping = False

    def status(self) -> dict:
        with self._lock:
            worker = self.worker.status() if self.worker else None
            running = bool(worker and worker.running)
            return {
                "state": "stopping" if running and self._stopping else "running" if running else "stopped",
                "enabled": running and not self._stopping,
                "last_error": worker.last_error if worker else None,
                "iterations": worker.iterations if worker else 0,
                "authorized": self._authorized,
                # 健康状态独立于开关生命周期，退避不代表停止或撤销授权。
                "health": worker.health if worker else "idle",
                "backoff": worker.backoff if worker else False,
                "backoff_seconds": worker.backoff_seconds if worker else 0.0,
                "retry_after_seconds": worker.retry_after_seconds if worker else 0.0,
                "consecutive_failures": worker.consecutive_failures if worker else 0,
                "total_failures": worker.total_failures if worker else 0,
                "last_success_at": worker.last_success_at if worker else None,
                "last_error_at": worker.last_error_at if worker else None,
                "active_error": worker.active_error if worker else None,
                "last_failure": worker.last_failure if worker else None,
            }

    def start(self) -> dict:
        with self._lock:
            if self.worker and self.worker.status().running:
                if self._stopping:
                    raise RuntimeError("perception is still stopping")
                return self.status()
            if self.service is None:
                self.install(PerceptionQueryService(self.factory()))
            self.service.runtime.authorize()
            self._authorized = True
            self.service.runtime.resume()
            self._stopping = False
            self.worker.start()
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            if self.worker:
                self._stopping = True
                self.worker.stop(timeout_seconds=0)
            return self.status()

    def drain(self, *, timeout_seconds: float = 600) -> None:
        self.stop()
        if self.worker and not self.worker.stop(timeout_seconds=timeout_seconds):
            raise RuntimeError("perception shutdown timed out")

    def close(self) -> None:
        self.drain()
        if self.service and self.service.store:
            self.service.store.close()
