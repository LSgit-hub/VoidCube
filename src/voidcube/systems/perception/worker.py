"""Explicit background sampler for the local perception runtime."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .runtime import PerceptionRuntime


@dataclass(frozen=True, slots=True)
class PerceptionWorkerStatus:
    running: bool
    iterations: int
    last_error: str | None


class PerceptionWorker:
    """Run runtime steps in one daemon thread only after explicit start."""

    def __init__(
        self,
        runtime: PerceptionRuntime,
        *,
        interval_seconds: float = 0.5,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.interval_seconds = max(0.05, float(interval_seconds))
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._iterations = 0
        self._last_error: str | None = None

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self.runtime.start()
            if self.runtime.status().state != "running":
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="voidcube-perception",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, *, flush: bool = True, timeout_seconds: float = 5.0) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout_seconds)))
        with self._lock:
            self._thread = None
        self.runtime.stop(flush=flush)

    def status(self) -> PerceptionWorkerStatus:
        thread = self._thread
        return PerceptionWorkerStatus(
            running=thread is not None and thread.is_alive(),
            iterations=self._iterations,
            last_error=self._last_error,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.runtime.step()
                self._iterations += 1
                self._last_error = None
            except Exception as exc:  # pragma: no cover - backend-specific
                self._last_error = f"{type(exc).__name__}: {exc}"
                if self.on_error is not None:
                    try:
                        self.on_error(exc)
                    except Exception:
                        pass
            self._stop.wait(max(0.0, self.interval_seconds - (time.monotonic() - started)))


__all__ = ["PerceptionWorker", "PerceptionWorkerStatus"]
