"""Typed local client for the process-owned Memory Service.

The client deliberately knows the service endpoint, not the SQLite path.  It
binds the caller capability and ownership scope at construction time so call
sites cannot silently switch actors or domains while building a request.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


PROTOCOL_VERSION = "1"
DEFAULT_MEMORY_SERVICE_URL = "http://127.0.0.1:6001"
_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_TRANSPORT_RETRY_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
_MAX_RETRY_AFTER_SECONDS = 60.0


def _finite_non_negative(value: Any, *, field: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a finite non-negative number") from None
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return normalized


def _finite_positive(value: Any, *, field: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a finite positive number") from None
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be a finite positive number")
    if normalized <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return normalized


def _retry_after_seconds(value: Any) -> float | None:
    """Parse Retry-After as bounded seconds, accepting delta or HTTP date."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(raw)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            delay = (target - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(delay):
        return None
    return max(0.0, min(_MAX_RETRY_AFTER_SECONDS, delay))


def _transport_retry_allowed(
    method: str,
    payload: Mapping[str, Any],
    idempotency_key: str | None,
) -> bool:
    """Avoid replaying ambiguous writes unless the caller supplied a key."""
    if method.upper() in _TRANSPORT_RETRY_SAFE_METHODS:
        return True
    return bool(
        str(
            idempotency_key
            or payload.get("idempotency_key")
            or payload.get("write_id")
            or ""
        ).strip()
    )


class MemoryClientError(RuntimeError):
    """Base error for typed Memory Service calls."""


class MemoryServiceUnavailable(MemoryClientError):
    """The owner service could not be reached before the deadline."""


class MemoryProtocolError(MemoryClientError):
    """The request or response violated the local protocol contract."""


@dataclass(frozen=True, slots=True)
class MemoryClientIdentity:
    """Fixed capability and scope attached to every request."""

    actor: str
    owner_id: str
    workspace_id: str
    memory_domain: str

    def __post_init__(self) -> None:
        for name in ("actor", "owner_id", "workspace_id", "memory_domain"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"Memory client {name} is required")


class MemoryClient:
    """Small JSON client whose only remote peer is the Memory Service."""

    def __init__(
        self,
        base_url: str = DEFAULT_MEMORY_SERVICE_URL,
        *,
        identity: MemoryClientIdentity,
        timeout_seconds: float = 2.0,
        service_token: str | None = None,
        max_retries: int = 1,
        retry_base_seconds: float = 0.05,
    ) -> None:
        normalized = str(base_url or "").strip().rstrip("/")
        parsed_url = urlsplit(normalized)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("Memory Service URL must use http or https")
        self.base_url = normalized
        self.identity = identity
        self.timeout_seconds = max(
            0.1,
            _finite_positive(timeout_seconds, field="Memory client timeout"),
        )
        self.service_token = str(service_token or os.getenv("MEMORY_SERVICE_TOKEN") or "").strip()
        self.max_retries = max(0, min(5, int(max_retries)))
        self.retry_base_seconds = _finite_non_negative(
            retry_base_seconds, field="Memory client retry base"
        )

    def request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        identity_session_id: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Call a domain endpoint with the client's fixed identity.

        Caller-provided identity fields must either be absent or exactly match
        the bound identity.  This prevents a helper from impersonating another
        actor by copying a request body from a different memory domain.
        """
        request_payload = dict(payload or {})
        self._validate_or_bind(request_payload, "memory_actor", self.identity.actor)
        self._validate_or_bind(request_payload, "owner_id", self.identity.owner_id)
        self._validate_or_bind(request_payload, "workspace_id", self.identity.workspace_id)
        self._validate_or_bind(request_payload, "memory_domain", self.identity.memory_domain)
        request_payload.update(
            {
                "memory_actor": self.identity.actor,
                "owner_id": self.identity.owner_id,
                "workspace_id": self.identity.workspace_id,
                "memory_domain": self.identity.memory_domain,
            }
        )

        resolved_path = "/" + str(path or "").lstrip("/")
        url = f"{self.base_url}{resolved_path}"
        upper_method = method.upper()
        if upper_method in {"GET", "HEAD"} and request_payload:
            url = f"{url}?{urlencode(request_payload, doseq=True)}"
            body = None
        else:
            body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-VoidCube-Protocol-Version": PROTOCOL_VERSION,
            "X-VoidCube-Memory-Actor": self.identity.actor,
            "X-VoidCube-Owner-Id": self.identity.owner_id,
            "X-VoidCube-Workspace-Id": self.identity.workspace_id,
            "X-VoidCube-Request-Id": str(request_id or uuid.uuid4()),
        }
        session_id = str(identity_session_id or "").strip()
        if session_id:
            headers["X-VoidCube-Session-Id"] = session_id
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        if self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"

        attempts = self.max_retries + 1
        retry_transport = _transport_retry_allowed(
            upper_method, request_payload, idempotency_key
        )
        for attempt in range(attempts):
            request = Request(url, data=body, headers=headers, method=upper_method)
            retry_delay = None
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                parsed = json.loads(raw or "{}")
                if not isinstance(parsed, dict):
                    raise MemoryProtocolError("Memory Service returned a non-object response")
                return parsed
            except HTTPError as exc:
                if exc.code not in _RETRYABLE_HTTP_STATUSES or attempt + 1 >= attempts:
                    raise MemoryProtocolError(
                        f"Memory Service HTTP error {exc.code}"
                    ) from exc
                retry_delay = _retry_after_seconds(
                    exc.headers.get("Retry-After") if exc.headers else None
                )
            except (URLError, TimeoutError, OSError) as exc:
                if not retry_transport or attempt + 1 >= attempts:
                    raise MemoryServiceUnavailable("Memory Service is unavailable") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MemoryProtocolError("Memory Service returned invalid JSON") from exc
            delay = (
                retry_delay
                if retry_delay is not None
                else self.retry_base_seconds * (2**attempt)
            )
            if delay:
                time.sleep(delay)
        raise MemoryServiceUnavailable("Memory Service request exhausted retries")

    def get_compressed(
        self,
        memory_id: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Read one authorized compressed memory by opaque id.

        The client identity remains the source of authorization.  Callers
        cannot supply owner, workspace or domain through this convenience
        method.
        """
        normalized = str(memory_id or "").strip()
        if not normalized:
            raise ValueError("memory_id is required")
        return self.request_json(
            "GET",
            f"/compressed/{quote(normalized, safe='')}",
            identity_session_id=session_id,
        )

    @staticmethod
    def _validate_or_bind(payload: dict[str, Any], key: str, expected: str) -> None:
        supplied = payload.get(key)
        if supplied is not None and str(supplied).strip() != expected:
            raise MemoryProtocolError(
                f"Memory request {key} does not match the bound client identity"
            )


class AsyncMemoryClient:
    """aiohttp-backed variant for Supervisor and other async services."""

    def __init__(
        self,
        base_url: str = DEFAULT_MEMORY_SERVICE_URL,
        *,
        identity: MemoryClientIdentity,
        timeout_seconds: float = 3.0,
        service_token: str | None = None,
        max_retries: int = 1,
        retry_base_seconds: float = 0.05,
    ) -> None:
        normalized = str(base_url or "").strip().rstrip("/")
        parsed_url = urlsplit(normalized)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("Memory Service URL must use http or https")
        self.base_url = normalized
        self.identity = identity
        self.timeout_seconds = max(
            0.1,
            _finite_positive(timeout_seconds, field="Memory client timeout"),
        )
        self.service_token = str(service_token or os.getenv("MEMORY_SERVICE_TOKEN") or "").strip()
        self.max_retries = max(0, min(5, int(max_retries)))
        self.retry_base_seconds = _finite_non_negative(
            retry_base_seconds, field="Memory client retry base"
        )

    async def request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        identity_session_id: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        import aiohttp

        request_payload = dict(payload or {})
        MemoryClient._validate_or_bind(request_payload, "memory_actor", self.identity.actor)
        MemoryClient._validate_or_bind(request_payload, "owner_id", self.identity.owner_id)
        MemoryClient._validate_or_bind(request_payload, "workspace_id", self.identity.workspace_id)
        MemoryClient._validate_or_bind(request_payload, "memory_domain", self.identity.memory_domain)
        request_payload.update(
            {
                "memory_actor": self.identity.actor,
                "owner_id": self.identity.owner_id,
                "workspace_id": self.identity.workspace_id,
                "memory_domain": self.identity.memory_domain,
            }
        )
        headers = {
            "Accept": "application/json",
            "X-VoidCube-Protocol-Version": PROTOCOL_VERSION,
            "X-VoidCube-Memory-Actor": self.identity.actor,
            "X-VoidCube-Owner-Id": self.identity.owner_id,
            "X-VoidCube-Workspace-Id": self.identity.workspace_id,
            "X-VoidCube-Request-Id": str(request_id or uuid.uuid4()),
        }
        session_id = str(identity_session_id or "").strip()
        if session_id:
            headers["X-VoidCube-Session-Id"] = session_id
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        if self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"

        url = f"{self.base_url}/{str(path or '').lstrip('/')}"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        attempts = self.max_retries + 1
        retry_transport = _transport_retry_allowed(
            method, request_payload, idempotency_key
        )
        parsed: Any = None
        for attempt in range(attempts):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    if method.upper() in {"GET", "HEAD"}:
                        response_context = session.request(
                            method.upper(),
                            url,
                            params=request_payload,
                            headers=headers,
                        )
                    else:
                        response_context = session.request(
                            method.upper(),
                            url,
                            json=request_payload,
                            headers=headers,
                        )
                    async with response_context as response:
                        if response.status >= 400:
                            detail = (await response.text())[:500]
                            if (
                                response.status in _RETRYABLE_HTTP_STATUSES
                                and attempt + 1 < attempts
                            ):
                                retry_delay = _retry_after_seconds(
                                    response.headers.get("Retry-After")
                                )
                                delay = (
                                    retry_delay
                                    if retry_delay is not None
                                    else self.retry_base_seconds * (2**attempt)
                                )
                                if delay:
                                    await asyncio.sleep(delay)
                                continue
                            raise MemoryProtocolError(
                                f"Memory Service HTTP error {response.status}: {detail}"
                            )
                        try:
                            parsed = await response.json()
                        except Exception as exc:
                            if (
                                isinstance(exc, (ValueError, UnicodeDecodeError))
                                or exc.__class__.__name__ == "ContentTypeError"
                            ):
                                raise MemoryProtocolError(
                                    "Memory Service returned invalid JSON"
                                ) from exc
                            raise
                        break
            except MemoryClientError:
                raise
            except (ValueError, UnicodeDecodeError) as exc:
                raise MemoryProtocolError(
                    "Memory Service returned invalid JSON"
                ) from exc
            except Exception as exc:
                if not retry_transport or attempt + 1 >= attempts:
                    raise MemoryServiceUnavailable(
                        "Memory Service is unavailable"
                    ) from exc
                if self.retry_base_seconds:
                    await asyncio.sleep(self.retry_base_seconds * (2**attempt))
        if not isinstance(parsed, dict):
            raise MemoryProtocolError("Memory Service returned a non-object response")
        return parsed

    async def get_compressed(
        self,
        memory_id: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Read one authorized compressed memory by opaque id."""
        normalized = str(memory_id or "").strip()
        if not normalized:
            raise ValueError("memory_id is required")
        return await self.request_json(
            "GET",
            f"/compressed/{quote(normalized, safe='')}",
            identity_session_id=session_id,
        )


__all__ = [
    "DEFAULT_MEMORY_SERVICE_URL",
    "AsyncMemoryClient",
    "MemoryClient",
    "MemoryClientError",
    "MemoryClientIdentity",
    "MemoryProtocolError",
    "MemoryServiceUnavailable",
    "PROTOCOL_VERSION",
]
