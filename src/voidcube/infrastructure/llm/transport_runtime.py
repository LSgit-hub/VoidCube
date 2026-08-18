"""Interruptible transport for OpenAI-compatible chat completions."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

import httpx

from ..providers.model_metadata import is_local_endpoint
from voidcube.infrastructure.llm.stream_response import (
    StreamChunkUpdate,
    StreamingResponseAssembler,
)


logger = logging.getLogger(__name__)

_SSE_CONNECTION_PHRASES = (
    "connection lost",
    "connection reset",
    "connection closed",
    "connection terminated",
    "network error",
    "network connection",
    "terminated",
    "peer closed",
    "broken pipe",
    "upstream connect error",
)


class _RequestClientSlot:
    """Synchronize ownership when worker and polling threads abort a request."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: Any = None

    def set(self, client: Any) -> None:
        with self._lock:
            self._client = client

    def take(self) -> Any:
        with self._lock:
            client = self._client
            self._client = None
            return client


class ChatTransport:
    """Own request threads, stream health, retries, and transport fallback."""

    def __init__(
        self,
        *,
        client_lifecycle: Any,
        base_url: Callable[[], str],
        model: Callable[[], str],
        interrupted: Callable[[], bool],
        activity: Callable[[str], None] | None = None,
        capture_rate_limits: Callable[[Any], None] | None = None,
        emit_status: Callable[[str], None] | None = None,
        emit_warning: Callable[[str], None] | None = None,
        poll_interval: float = 0.3,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._clients = client_lifecycle
        self._base_url = base_url
        self._model = model
        self._interrupted = interrupted
        self._activity = activity or (lambda _message: None)
        self._capture_rate_limits = capture_rate_limits or (lambda _response: None)
        self._emit_status = emit_status or (lambda _message: None)
        self._emit_warning = emit_warning or (lambda _message: None)
        self._poll_interval = max(0.01, float(poll_interval))
        self._clock = clock

    def complete(self, api_kwargs: dict[str, Any]) -> Any:
        """Run one non-streaming request without blocking interrupt polling."""
        if self._interrupted():
            raise InterruptedError("Agent interrupted before API call")

        result = {"response": None, "error": None}
        slot = _RequestClientSlot()

        def worker() -> None:
            try:
                client = self._clients.create_request_client(
                    reason="chat_completion_request"
                )
                slot.set(client)
                if self._interrupted():
                    raise InterruptedError("Agent interrupted during API call")
                result["response"] = client.chat.completions.create(**api_kwargs)
            except Exception as exc:
                result["error"] = exc
            finally:
                self._close_slot(slot, reason="request_complete")

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while thread.is_alive():
            thread.join(timeout=self._poll_interval)
            if self._interrupted():
                self._close_slot(slot, reason="interrupt_abort")
                raise InterruptedError("Agent interrupted during API call")
        if result["error"] is not None:
            raise result["error"]
        return result["response"]

    def stream(
        self,
        api_kwargs: dict[str, Any],
        *,
        on_update: Callable[[StreamChunkUpdate], None],
        on_first_delta: Callable[[], None] | None = None,
    ) -> Any:
        """Run a healthy streaming request with retry and non-stream fallback."""
        if self._interrupted():
            raise InterruptedError("Agent interrupted before streaming API call")

        result = {"response": None, "error": None}
        slot = _RequestClientSlot()
        last_chunk_time = {"value": self._clock()}
        delivered_visible = {"value": False}
        first_delta_fired = {"value": False}
        monitor_rebuilt_pool = {"value": False}

        def emit_update(update: StreamChunkUpdate) -> None:
            if update.starts_delivery and not first_delta_fired["value"]:
                first_delta_fired["value"] = True
                if on_first_delta is not None:
                    try:
                        on_first_delta()
                    except Exception:
                        logger.debug("First stream-delta callback failed", exc_info=True)
            try:
                on_update(update)
            except Exception:
                logger.debug("Stream update callback failed", exc_info=True)
            if update.content and update.stream_content:
                delivered_visible["value"] = True

        def stream_once() -> Any:
            base_timeout = self._request_timeout(api_kwargs)
            read_timeout = self._env_float("VOIDCUBE_STREAM_READ_TIMEOUT", 120.0)
            base_url = self._base_url()
            if (
                read_timeout == 120.0
                and base_url
                and is_local_endpoint(base_url)
            ):
                read_timeout = base_timeout
                logger.debug(
                    "Local provider detected (%s); stream read timeout raised to %.0fs",
                    base_url,
                    read_timeout,
                )
            read_timeout = min(read_timeout, base_timeout)
            stream_kwargs = {
                **api_kwargs,
                "stream": True,
                "stream_options": {"include_usage": True},
                "timeout": httpx.Timeout(
                    connect=min(30.0, base_timeout),
                    read=read_timeout,
                    write=base_timeout,
                    pool=min(30.0, base_timeout),
                ),
            }
            client = self._clients.create_request_client(
                reason="chat_completion_stream_request"
            )
            slot.set(client)
            last_chunk_time["value"] = self._clock()
            self._notify(
                self._activity,
                "waiting for provider response (streaming)",
                label="activity",
            )
            stream = client.chat.completions.create(**stream_kwargs)
            self._notify(
                self._capture_rate_limits,
                getattr(stream, "response", None),
                label="rate-limit capture",
            )

            assembler = StreamingResponseAssembler()
            first_chunk_seen = False
            for chunk in stream:
                last_chunk_time["value"] = self._clock()
                if not first_chunk_seen:
                    first_chunk_seen = True
                    self._notify(
                        self._activity,
                        "receiving stream response",
                        label="activity",
                    )
                if self._interrupted():
                    raise InterruptedError("Agent interrupted during streaming API call")
                emit_update(assembler.add(chunk))
            return assembler.build_response()

        def worker() -> None:
            max_retries = max(0, self._env_int("VOIDCUBE_STREAM_RETRIES", 2))
            try:
                for attempt in range(max_retries + 1):
                    try:
                        result["response"] = stream_once()
                        return
                    except InterruptedError as exc:
                        result["error"] = exc
                        return
                    except Exception as exc:
                        if delivered_visible["value"]:
                            logger.warning(
                                "Streaming failed after partial delivery, not retrying: %s",
                                exc,
                            )
                            result["error"] = exc
                            return

                        transient = self.is_transient_stream_error(exc)
                        if transient and attempt < max_retries:
                            recovered_by_monitor = monitor_rebuilt_pool["value"]
                            monitor_rebuilt_pool["value"] = False
                            if not recovered_by_monitor:
                                logger.info(
                                    "Streaming attempt %s/%s failed (%s: %s); "
                                    "retrying",
                                    attempt + 1,
                                    max_retries + 1,
                                    type(exc).__name__,
                                    exc,
                                )
                                self._notify(
                                    self._emit_status,
                                    "Connection to provider dropped "
                                    f"({type(exc).__name__}). Reconnecting "
                                    f"(attempt {attempt + 2}/{max_retries + 1})",
                                    label="status",
                                )
                            self._close_slot(slot, reason="stream_retry_cleanup")
                            if not recovered_by_monitor:
                                self._replace_primary(
                                    reason="stream_retry_pool_cleanup"
                                )
                            continue

                        if transient:
                            self._notify(
                                self._emit_status,
                                "Connection to provider failed after "
                                f"{max_retries + 1} attempts. Falling back to "
                                "a non-streaming request.",
                                label="status",
                            )
                            logger.warning(
                                "Streaming exhausted %s attempts: %s",
                                max_retries + 1,
                                exc,
                            )
                        elif self.is_stream_unsupported(exc):
                            self._notify(
                                self._emit_warning,
                                "Streaming is not supported for this model/provider; "
                                "falling back to non-streaming.",
                                label="warning",
                            )
                        else:
                            logger.info(
                                "Streaming failed before delivery; falling back to "
                                "non-streaming: %s",
                                exc,
                            )

                        self._close_slot(slot, reason="stream_fallback_cleanup")
                        last_chunk_time["value"] = self._clock()
                        try:
                            result["response"] = self.complete(api_kwargs)
                        except Exception as fallback_exc:
                            result["error"] = fallback_exc
                        return
            except Exception as exc:
                result["error"] = exc
            finally:
                self._close_slot(slot, reason="stream_request_complete")

        stale_timeout = self.stream_stale_timeout(api_kwargs)
        stale_abort_requested = False
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while thread.is_alive():
            thread.join(timeout=self._poll_interval)
            stale_elapsed = self._clock() - last_chunk_time["value"]
            if stale_elapsed > stale_timeout and not stale_abort_requested:
                stale_abort_requested = True
                estimated_tokens = self.estimate_context_tokens(api_kwargs)
                logger.warning(
                    "Stream stale for %.0fs (threshold %.0fs); model=%s "
                    "context=~%s tokens. Closing request client.",
                    stale_elapsed,
                    stale_timeout,
                    api_kwargs.get("model", "unknown"),
                    f"{estimated_tokens:,}",
                )
                self._notify(
                    self._emit_status,
                    f"No response from provider for {int(stale_elapsed)}s "
                    f"(model: {api_kwargs.get('model', 'unknown')}, "
                    f"context: ~{estimated_tokens:,} tokens). Reconnecting.",
                    label="status",
                )
                self._close_slot(slot, reason="stale_stream_kill")
                monitor_rebuilt_pool["value"] = self._replace_primary(
                    reason="stale_stream_pool_cleanup"
                )
                last_chunk_time["value"] = self._clock()

            if self._interrupted():
                self._close_slot(slot, reason="stream_interrupt_abort")
                raise InterruptedError("Agent interrupted during streaming API call")

        if result["error"] is not None:
            if delivered_visible["value"] and not isinstance(
                result["error"], InterruptedError
            ):
                logger.warning(
                    "Partial stream delivered; returning terminal stub: %s",
                    result["error"],
                )
                return StreamingResponseAssembler.partial_delivery_response(
                    self._model() or "unknown"
                )
            raise result["error"]
        return result["response"]

    def stream_stale_timeout(self, api_kwargs: dict[str, Any]) -> float:
        base_timeout = self._env_float("VOIDCUBE_STREAM_STALE_TIMEOUT", 180.0)
        base_url = self._base_url()
        if base_timeout == 180.0 and base_url and is_local_endpoint(base_url):
            return float("inf")
        estimated_tokens = self.estimate_context_tokens(api_kwargs)
        if estimated_tokens > 100_000:
            return max(base_timeout, 300.0)
        if estimated_tokens > 50_000:
            return max(base_timeout, 240.0)
        return base_timeout

    @staticmethod
    def estimate_context_tokens(api_kwargs: dict[str, Any]) -> int:
        return sum(len(str(value)) for value in api_kwargs.get("messages", ())) // 4

    @staticmethod
    def is_transient_stream_error(error: Exception) -> bool:
        if isinstance(
            error,
            (
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                httpx.PoolTimeout,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                ConnectionError,
            ),
        ):
            return True
        from openai import APIError

        if isinstance(error, APIError) and not getattr(error, "status_code", None):
            lowered = str(error).lower()
            return any(phrase in lowered for phrase in _SSE_CONNECTION_PHRASES)
        return False

    @staticmethod
    def is_stream_unsupported(error: Exception) -> bool:
        lowered = str(error).lower()
        return "stream" in lowered and "not supported" in lowered

    def _close_slot(self, slot: _RequestClientSlot, *, reason: str) -> None:
        client = slot.take()
        if client is None:
            return
        try:
            self._clients.close_request_client(client, reason=reason)
        except Exception:
            logger.debug(
                "Failed to close request client (%s)", reason, exc_info=True
            )

    def _replace_primary(self, *, reason: str) -> bool:
        try:
            return bool(self._clients.replace_primary(reason=reason))
        except Exception:
            logger.debug(
                "Failed to rebuild primary client (%s)", reason, exc_info=True
            )
            return False

    @staticmethod
    def _notify(callback: Callable[[Any], None], value: Any, *, label: str) -> None:
        try:
            callback(value)
        except Exception:
            logger.debug("Chat transport %s callback failed", label, exc_info=True)

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
            return default

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
            return default

    def _request_timeout(self, api_kwargs: dict[str, Any]) -> float:
        fallback = self._env_float("VOIDCUBE_API_TIMEOUT", 1800.0)
        raw = api_kwargs.get("timeout", fallback)
        try:
            return max(0.1, float(raw))
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid request timeout=%r; using %s", raw, fallback)
            return fallback
