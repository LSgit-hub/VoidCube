"""Terminal adapter for the canonical asynchronous voice transport."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
import inspect
import threading
from typing import Any, TypeVar


T = TypeVar("T")
VoiceManagerFactory = Callable[[], Any]


class VoiceTtsAdapter:
    """Bridge terminal operations to one canonical voice manager event loop."""

    def __init__(
        self,
        *,
        manager_factory: VoiceManagerFactory | None = None,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self._manager_factory = manager_factory or _create_voice_manager
        self._thread_factory = thread_factory
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._manager: Any = None
        self._startup_error: BaseException | None = None

    def status(self) -> dict[str, Any]:
        try:
            payload = self._call(lambda manager: manager.status())
        except Exception as exc:
            return {
                "status": "unavailable",
                "reason": f"voice_transport_unavailable:{type(exc).__name__}",
            }
        configured = bool(payload.get("tts_configured"))
        enabled = bool(payload.get("enabled"))
        playback_available = bool(payload.get("playback_available"))
        if enabled and configured and playback_available:
            state = "available"
            reason = "ready"
        elif not enabled:
            state = "unavailable"
            reason = "voice_output_disabled"
        elif not configured:
            state = "unavailable"
            reason = "tts_not_configured"
        else:
            state = "unavailable"
            reason = "playback_unavailable"
        return {"status": state, "reason": reason, "voice": payload}

    def realtime_status(self) -> dict[str, Any]:
        """Return the manager's non-blocking view state for terminal rendering."""
        return self._call(lambda manager: manager.realtime_status())

    def enable(self) -> dict[str, Any]:
        return self._call(lambda manager: manager.set_enabled(True))

    def disable(self) -> dict[str, Any]:
        return self._call(lambda manager: manager.set_enabled(False))

    def transcribe_once(self, *, session_id: str = "") -> dict[str, Any]:
        async def operation(manager: Any) -> dict[str, Any]:
            return await manager.transcribe_once(session_id=session_id)

        return self._call(operation)

    def speak(self, text: str, *, reason: str = "terminal_command") -> dict[str, Any]:
        message = str(text or "").strip()
        if not message:
            return {"status": "invalid", "reason": "text_is_empty"}

        async def operation(manager: Any) -> dict[str, Any]:
            return await manager.speak_text(message, reason=reason)

        return self._call(operation)

    def interrupt(self) -> dict[str, Any]:
        try:
            return self._call(lambda manager: manager.interrupt())
        except Exception as exc:
            return {
                "status": "unavailable",
                "reason": f"voice_transport_unavailable:{type(exc).__name__}",
            }

    def close(self) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread
        if thread is None:
            return
        if loop is not None:
            try:
                self._call(lambda manager: manager.interrupt())
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)
        if thread.is_alive():
            return
        with self._lock:
            self._loop = None
            self._thread = None
            self._manager = None
            self._ready.clear()
            self._startup_error = None

    def _call(self, operation: Callable[[Any], T | Awaitable[T]]) -> T:
        self._ensure_started()
        with self._lock:
            loop = self._loop
        if loop is None:
            raise RuntimeError("voice adapter event loop is not running")

        async def invoke() -> T:
            result = operation(self._manager)
            if inspect.isawaitable(result):
                return await result
            return result  # type: ignore[return-value]

        future: Future[T] = asyncio.run_coroutine_threadsafe(invoke(), loop)
        return future.result()

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None:
                thread = self._thread
            else:
                self._ready.clear()
                self._startup_error = None
                thread = self._thread_factory(
                    target=self._run_loop,
                    name="voidcube-voice-tts",
                    daemon=True,
                )
                self._thread = thread
                thread.start()
        self._ready.wait(timeout=5.0)
        if not self._ready.is_set():
            raise RuntimeError("voice adapter startup timed out")
        if self._startup_error is not None:
            raise RuntimeError("voice manager startup failed") from self._startup_error

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
        try:
            self._manager = self._manager_factory()
        except BaseException as exc:
            self._startup_error = exc
        finally:
            self._ready.set()
        if self._startup_error is not None:
            loop.close()
            with self._lock:
                self._loop = None
            return
        try:
            loop.run_forever()
        finally:
            loop.close()
            with self._lock:
                self._loop = None


def _create_voice_manager() -> Any:
    from systems.voice import VoiceConfig, VoiceSessionManager

    return VoiceSessionManager(VoiceConfig.from_env())


__all__ = ["VoiceTtsAdapter"]
