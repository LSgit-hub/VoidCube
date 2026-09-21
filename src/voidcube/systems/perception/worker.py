"""Explicit background sampler for the local perception runtime."""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .runtime import PerceptionRuntime

logger = logging.getLogger(__name__)


def _seconds(value: float, name: str, *, allow_zero: bool = False) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{name} must be finite and {'nonnegative' if allow_zero else 'positive'}")
    return value


@dataclass(frozen=True, slots=True)
class PerceptionWorkerStatus:
    running: bool
    iterations: int
    last_error: str | None
    consecutive_failures: int = 0
    total_failures: int = 0
    last_success_at: float | None = None
    last_error_at: float | None = None
    health: str = "idle"
    backoff: bool = False
    backoff_seconds: float = 0.0
    retry_after_seconds: float = 0.0
    active_error: str | None = None
    last_failure: str | None = None


class PerceptionWorker:
    """Run runtime steps in one daemon thread only after explicit start."""

    def __init__(
        self,
        runtime: PerceptionRuntime,
        *,
        interval_seconds: float = 0.5,
        on_error: Callable[[Exception], None] | None = None,
        backoff_initial_seconds: float = 1.0,
        backoff_max_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.runtime = runtime
        self.interval_seconds = _seconds(interval_seconds, "interval_seconds")
        self.backoff_initial_seconds = _seconds(backoff_initial_seconds, "backoff_initial_seconds")
        self.backoff_max_seconds = _seconds(backoff_max_seconds, "backoff_max_seconds")
        if self.backoff_initial_seconds > self.backoff_max_seconds:
            raise ValueError("backoff_initial_seconds must not exceed backoff_max_seconds")
        self.on_error = on_error
        self._clock = clock
        self._wall_clock = wall_clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._iterations = 0
        self._last_error: str | None = None
        self._last_failure: str | None = None
        self._consecutive_failures = 0
        self._total_failures = 0
        self._last_success_at: float | None = None
        self._last_error_at: float | None = None
        self._health = "idle"
        self._backoff_seconds = 0.0
        self._retry_at: float | None = None
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
        # 零超时用于控制层非阻塞停止；其余时间参数必须为有限正数。
        timeout = _seconds(timeout_seconds, "timeout_seconds", allow_zero=True)
        with self._lock:
            thread = self._thread
            self._flush_on_stop = flush
            self._stop.set()
            self._retry_at = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        with self._lock:
            if thread is not None and thread.is_alive():
                return False
            if self._thread is thread:
                self._thread = None
        return True

    def status(self) -> PerceptionWorkerStatus:
        # 所有观测字段在同一把锁内读取，避免拼接出不同迭代的状态。
        with self._lock:
            thread = self._thread
            retry = max(0.0, self._retry_at - self._clock()) if self._retry_at is not None else 0.0
            return PerceptionWorkerStatus(
                running=thread is not None and thread.is_alive(),
                iterations=self._iterations,
                last_error=self._last_error,
                consecutive_failures=self._consecutive_failures,
                total_failures=self._total_failures,
                last_success_at=self._last_success_at,
                last_error_at=self._last_error_at,
                health=self._health,
                backoff=self._retry_at is not None,
                backoff_seconds=self._backoff_seconds if self._retry_at is not None else 0.0,
                retry_after_seconds=retry,
                active_error=self._last_error,
                last_failure=self._last_failure,
            )

    @staticmethod
    def _log_error(stage: str, exc: Exception) -> None:
        # 不记录异常原文、堆栈或运行时内容，避免截图和模型响应泄漏。
        logger.warning("perception worker failure stage=%s type=%s", stage, type(exc).__name__)

    def _failure(self, exc: Exception, stage: str) -> None:
        with self._lock:
            self._last_error = self._last_failure = type(exc).__name__
            self._last_error_at = self._wall_clock()
            self._consecutive_failures += 1
            self._total_failures += 1
            self._health = "degraded"
        self._log_error(stage, exc)

    def _run(self) -> None:
        try:
            self._sample()
        finally:
            with self._lock:
                flush = self._flush_on_stop
                self._retry_at = None
            try:
                self.runtime.stop(flush=flush)
            except Exception as exc:
                self._failure(exc, "flush" if flush else "stop")
            finally:
                try:
                    source = getattr(getattr(self.runtime, "loop", None), "frame_source", None)
                    close = getattr(source, "close", None)
                    if callable(close):
                        close()  # 截图句柄必须在采集线程内关闭。
                except Exception as exc:
                    self._failure(exc, "close")

    def _sample(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            with self._lock:
                self._retry_at = None
            try:
                self.runtime.step()
            except Exception as exc:
                self._failure(exc, "step")
                with self._lock:
                    # 逐次倍增并封顶，不计算无界指数，避免长期失败导致溢出。
                    self._backoff_seconds = min(
                        self.backoff_max_seconds,
                        self._backoff_seconds * 2 if self._backoff_seconds else self.backoff_initial_seconds,
                    )
                    delay = self._backoff_seconds
                    if not self._stop.is_set():
                        self._retry_at = self._clock() + delay
                if self.on_error is not None:
                    try:
                        self.on_error(exc)
                    except Exception as callback_exc:
                        self._log_error("on_error", callback_exc)
                with self._lock:
                    delay = max(0.0, self._retry_at - self._clock()) if self._retry_at is not None else 0.0
            else:
                with self._lock:
                    self._iterations += 1
                    self._last_error = None
                    self._consecutive_failures = 0
                    self._last_success_at = self._wall_clock()
                    self._health = "healthy"
                    self._backoff_seconds = 0.0
                    # 恢复时只清除活动错误，保留历史错误及时间戳。
                delay = max(0.0, self.interval_seconds - (self._clock() - started))
            # 事件等待允许 stop 立即中断退避，不使用不可中断的 sleep。
            self._stop.wait(delay)


__all__ = ["PerceptionWorker", "PerceptionWorkerStatus"]
