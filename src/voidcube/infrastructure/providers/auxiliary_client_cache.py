"""Lifecycle-safe cache for auxiliary OpenAI-compatible clients.

The cache deliberately knows nothing about provider credentials or routing.  A
resolver callback supplies those concerns, which keeps event-loop cleanup out
of the auxiliary router and makes the lifecycle policy reusable by gateways
and CLI hosts.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from typing import Any, Callable, Mapping


def force_close_async_httpx(client: Any) -> None:
    """Mark an async httpx transport closed without touching its event loop."""
    try:
        from httpx._client import ClientState

        inner = getattr(client, "_client", None)
        if inner is not None and not getattr(inner, "is_closed", True):
            inner._state = ClientState.CLOSED
    except Exception:
        # The OpenAI/httpx internals are optional implementation details.
        pass


def neuter_async_httpx_del() -> None:
    """Disable OpenAI's loop-unsafe async client finalizer."""
    try:
        from openai._base_client import AsyncHttpxClientWrapper

        AsyncHttpxClientWrapper.__del__ = lambda self: None  # type: ignore[assignment]
    except (ImportError, AttributeError):
        pass


class AuxiliaryClientCache:
    """Cache clients while preventing cross-event-loop async reuse."""

    def __init__(self) -> None:
        self.entries: dict[tuple, tuple[Any, Any, Any]] = {}
        self.lock = threading.Lock()

    @staticmethod
    def _loop(async_mode: bool) -> tuple[int, Any]:
        if not async_mode:
            return 0, None
        try:
            current = asyncio.get_event_loop()
        except RuntimeError:
            return 0, None
        return id(current), current

    def get_or_create(
        self,
        provider: str,
        *,
        model: str | None = None,
        async_mode: bool = False,
        base_url: str | None = None,
        api_key: str | None = None,
        main_runtime: Mapping[str, Any] | None = None,
        resolve_client: Callable[..., tuple[Any, Any]] | None = None,
        normalize_runtime: Callable[[Mapping[str, Any] | None], Mapping[str, Any]] | None = None,
        runtime_fields: tuple[str, ...] = (),
        compat_model: Callable[[Any, str | None, str | None], str | None] | None = None,
    ) -> tuple[Any, Any]:
        if resolve_client is None:
            raise ValueError("resolve_client callback is required")
        loop_id, current_loop = self._loop(async_mode)
        runtime = normalize_runtime(main_runtime) if normalize_runtime else dict(main_runtime or {})
        runtime_key = (
            tuple(runtime.get(field, "") for field in runtime_fields)
            if provider == "auto"
            else ()
        )
        cache_key = (provider, async_mode, base_url or "", api_key or "", loop_id, runtime_key)

        with self.lock:
            cached = self.entries.get(cache_key)
            if cached is not None:
                cached_client, cached_default, cached_loop = cached
                if async_mode and cached_loop is not None and cached_loop.is_closed():
                    force_close_async_httpx(cached_client)
                    del self.entries[cache_key]
                else:
                    if compat_model is not None:
                        model = compat_model(cached_client, model, cached_default)
                    return cached_client, model or cached_default

        client, default_model = resolve_client(
            provider,
            model,
            async_mode,
            explicit_base_url=base_url,
            explicit_api_key=api_key,
            main_runtime=runtime,
        )
        if client is None:
            return None, None

        with self.lock:
            existing = self.entries.get(cache_key)
            if existing is None:
                self.entries[cache_key] = (client, default_model, current_loop)
            else:
                client, default_model, _ = existing
        return client, model or default_model

    def shutdown(self) -> None:
        """Close cached clients before their owning event loop is destroyed."""
        with self.lock:
            for client, _default, _loop in list(self.entries.values()):
                if client is None:
                    continue
                force_close_async_httpx(client)
                try:
                    close = getattr(client, "close", None)
                    if close and not inspect.iscoroutinefunction(close):
                        close()
                except Exception:
                    pass
            self.entries.clear()

    def cleanup_stale_async(self) -> None:
        """Remove cached async clients whose event loop has already closed."""
        with self.lock:
            stale = []
            for key, (client, _default, cached_loop) in self.entries.items():
                if cached_loop is not None and cached_loop.is_closed():
                    force_close_async_httpx(client)
                    stale.append(key)
            for key in stale:
                del self.entries[key]


__all__ = [
    "AuxiliaryClientCache",
    "force_close_async_httpx",
    "neuter_async_httpx_del",
]
