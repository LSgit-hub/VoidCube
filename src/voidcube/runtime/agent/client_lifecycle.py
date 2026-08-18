"""OpenAI-compatible chat client ownership and connection cleanup."""

from __future__ import annotations

import logging
import socket
import threading
from typing import Any, Callable, Mapping
from unittest.mock import Mock

from ...domain.contracts.integration_policy import require_active_integration


logger = logging.getLogger(__name__)


class ChatClientLifecycle:
    """Own the shared chat client and worker-local request clients."""

    def __init__(
        self,
        *,
        client_kwargs: Mapping[str, Any],
        provider: Callable[[], str],
        model: Callable[[], str],
        base_url: Callable[[], str],
        client_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._client_kwargs = dict(client_kwargs)
        self._provider = provider
        self._model = model
        self._base_url = base_url
        self._client_factory = client_factory
        self._lock = threading.RLock()
        self._primary: Any = None

    @property
    def primary(self) -> Any:
        with self._lock:
            return self._primary

    def snapshot_kwargs(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._client_kwargs)

    def log_context(self) -> str:
        thread = threading.current_thread()
        return (
            f"thread={thread.name}:{thread.ident} provider={self._provider()} "
            f"base_url={self._base_url()} model={self._model()}"
        )

    def initialize_primary(self, *, reason: str) -> Any:
        with self._lock:
            if self._primary is not None and not self.is_closed(self._primary):
                return self._primary
            client = self._create_client(
                dict(self._client_kwargs),
                reason=reason,
                shared=True,
            )
            self._primary = client
            return client

    def configure(self, client_kwargs: Mapping[str, Any], *, reason: str) -> bool:
        """Build and adopt a shared client for a new runtime configuration."""
        return self._replace(client_kwargs, reason=reason)

    def adopt(
        self,
        client: Any,
        client_kwargs: Mapping[str, Any],
        *,
        reason: str,
    ) -> None:
        """Adopt an already-resolved provider client as the shared client."""
        with self._lock:
            old_client = self._primary
            self._client_kwargs = dict(client_kwargs)
            self._primary = client
        if old_client is not client:
            self.close_client(old_client, reason=f"adopt:{reason}", shared=True)

    def replace_primary(self, *, reason: str) -> bool:
        return self._replace(None, reason=reason)

    def ensure_primary(self, *, reason: str) -> Any:
        require_active_integration(
            self._provider(),
            self._model(),
            self._base_url(),
        )
        with self._lock:
            client = self._primary
            if client is not None and not self.is_closed(client):
                return client

        logger.warning(
            "Detected closed shared chat client; recreating before use (%s) %s",
            reason,
            self.log_context(),
        )
        if not self.replace_primary(reason=f"recreate_closed:{reason}"):
            raise RuntimeError("Failed to recreate closed chat client")
        return self.primary

    def create_request_client(self, *, reason: str) -> Any:
        primary = self.ensure_primary(reason=reason)
        if isinstance(primary, Mock):
            return primary
        return self._create_client(
            self.snapshot_kwargs(),
            reason=reason,
            shared=False,
        )

    def close_request_client(self, client: Any, *, reason: str) -> None:
        self.close_client(client, reason=reason, shared=False)

    def close_primary(self, *, reason: str) -> None:
        with self._lock:
            client = self._primary
            self._primary = None
        self.close_client(client, reason=reason, shared=True)

    def active_api_key(self) -> Any:
        client = self.primary
        if client is not None:
            try:
                return getattr(client, "api_key", None)
            except Exception as exc:
                logger.debug("Could not extract API key from chat client: %s", exc)
        return self.snapshot_kwargs().get("api_key")

    def cleanup_dead_connections(self) -> bool:
        """Rebuild the primary client if its pool contains a dead socket."""
        client = self.primary
        if client is None:
            return False
        try:
            connections = self._connections(client)
            dead_count = sum(1 for connection in connections if self._socket_is_dead(connection))
            if dead_count:
                logger.warning(
                    "Found %d dead connection(s) in client pool; rebuilding client",
                    dead_count,
                )
                return self.replace_primary(reason="dead_connection_cleanup")
        except Exception as exc:
            logger.debug("Dead connection check error: %s", exc)
        return False

    @staticmethod
    def is_closed(client: Any) -> bool:
        """Handle SDK method and httpx property forms of ``is_closed``."""
        if isinstance(client, Mock):
            return False
        is_closed = getattr(client, "is_closed", None)
        if is_closed is not None:
            if callable(is_closed):
                if is_closed():
                    return True
            elif bool(is_closed):
                return True
        http_client = getattr(client, "_client", None)
        return bool(http_client is not None and getattr(http_client, "is_closed", False))

    @classmethod
    def force_close_tcp_sockets(cls, client: Any) -> int:
        """Force-close pooled TCP sockets before SDK-level shutdown."""
        closed = 0
        try:
            for connection in cls._connections(client):
                sock = cls._connection_socket(connection)
                if sock is None:
                    continue
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
                closed += 1
        except Exception as exc:
            logger.debug("Force-close TCP sockets sweep error: %s", exc)
        return closed

    def close_client(self, client: Any, *, reason: str, shared: bool) -> None:
        if client is None:
            return
        force_closed = self.force_close_tcp_sockets(client)
        try:
            client.close()
            logger.info(
                "Chat client closed (%s, shared=%s, tcp_force_closed=%d) %s",
                reason,
                shared,
                force_closed,
                self.log_context(),
            )
        except Exception as exc:
            logger.debug(
                "Chat client close failed (%s, shared=%s) %s error=%s",
                reason,
                shared,
                self.log_context(),
                exc,
            )

    def _create_client(
        self,
        client_kwargs: dict[str, Any],
        *,
        reason: str,
        shared: bool,
    ) -> Any:
        if self._client_factory is not None:
            client = self._client_factory(dict(client_kwargs))
        else:
            from openai import OpenAI

            client = OpenAI(**client_kwargs)
        logger.info(
            "Chat client created (%s, shared=%s) %s",
            reason,
            shared,
            self.log_context(),
        )
        return client

    def _replace(
        self,
        client_kwargs: Mapping[str, Any] | None,
        *,
        reason: str,
    ) -> bool:
        """Serialize configuration snapshots with primary-client replacement."""
        try:
            with self._lock:
                next_kwargs = dict(
                    self._client_kwargs if client_kwargs is None else client_kwargs
                )
                new_client = self._create_client(
                    next_kwargs,
                    reason=reason,
                    shared=True,
                )
                old_client = self._primary
                self._client_kwargs = next_kwargs
                self._primary = new_client
        except Exception as exc:
            logger.warning(
                "Failed to rebuild shared chat client (%s) %s error=%s",
                reason,
                self.log_context(),
                exc,
            )
            return False

        if old_client is not new_client:
            self.close_client(old_client, reason=f"replace:{reason}", shared=True)
        return True

    @staticmethod
    def _connections(client: Any) -> list[Any]:
        http_client = getattr(client, "_client", None)
        transport = getattr(http_client, "_transport", None)
        pool = getattr(transport, "_pool", None)
        if pool is None:
            return []
        return list(
            getattr(pool, "_connections", None)
            or getattr(pool, "_pool", None)
            or []
        )

    @staticmethod
    def _connection_socket(connection: Any) -> Any:
        stream = getattr(connection, "_network_stream", None) or getattr(
            connection, "_stream", None
        )
        if stream is None:
            return None
        sock = getattr(stream, "_sock", None)
        if sock is not None:
            return sock
        nested_stream = getattr(stream, "stream", None)
        return getattr(nested_stream, "_sock", None)

    @classmethod
    def _socket_is_dead(cls, connection: Any) -> bool:
        sock = cls._connection_socket(connection)
        if sock is None:
            return False
        try:
            sock.setblocking(False)
            flags = socket.MSG_PEEK | getattr(socket, "MSG_DONTWAIT", 0)
            return sock.recv(1, flags) == b""
        except BlockingIOError:
            return False
        except OSError:
            return True
        finally:
            try:
                sock.setblocking(True)
            except OSError:
                pass
