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
        self._flush_on_stop = True

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self.runtime.start()
            if self.runtime.status().state != "running":
                return False
            self._stop.clear()
            self._flush_on_stop = True
            self._thread = threading.Thread(
                target=self._run,
                name="voidcube-perception",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, *, flush: bool = True, timeout_seconds: float = 5.0) -> bool:
        """Request shutdown. False means the original worker is still draining."""
        with self._lock:
            thread = self._thread
            self._flush_on_stop = flush
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout_seconds)))
        with self._lock:
            if thread is not None and thread.is_alive():
                return False
            if self._thread is thread:
                self._thread = None
        return True

    def status(self) -> PerceptionWorkerStatus:
        thread = self._thread
        return PerceptionWorkerStatus(
            running=thread is not None and thread.is_alive(),
            iterations=self._iterations,
            last_error=self._last_error,
        )

    def _run(self) -> None:
        try:
            self._sample()
        finally:
            try:
                self.runtime.stop(flush=self._flush_on_stop)
            except Exception as exc:
                self._last_error = type(exc).__name__
            finally:
                source = getattr(getattr(self.runtime, "loop", None), "frame_source", None)
                close = getattr(source, "close", None)
                if callable(close):
                    close()  # mss handles belong to the capture thread.

    def _sample(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.runtime.step()
                self._iterations += 1
                self._last_error = None
            except Exception as exc:  # pragma: no cover - backend-specific
                self._last_error = type(exc).__name__
                if self.on_error is not None:
                    try:
                        self.on_error(exc)
                    except Exception:
                        pass
            self._stop.wait(max(0.0, self.interval_seconds - (time.monotonic() - started)))


__all__ = ["PerceptionWorker", "PerceptionWorkerStatus"]
