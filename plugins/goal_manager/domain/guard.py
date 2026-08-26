"""Short-lived confirmation tokens for dangerous goal operations."""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any


class ConfirmationRequired(ValueError):
    def __init__(self, detail: str, token: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.token = token


class ConfirmationGuard:
    def __init__(self, ttl_seconds: float = 120.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._tokens: dict[str, tuple[float, str]] = {}

    def require_or_consume(self, action: str, payload: Any, token: str | None) -> None:
        fingerprint = self._fingerprint(action, payload)
        now = time.monotonic()
        self._tokens = {
            key: value for key, value in self._tokens.items() if value[0] > now
        }
        if token:
            record = self._tokens.pop(str(token), None)
            if record is not None and record[1] == fingerprint and record[0] > now:
                return
        new_token = secrets.token_urlsafe(24)
        self._tokens[new_token] = (now + self.ttl_seconds, fingerprint)
        raise ConfirmationRequired("dangerous operation requires confirmation", new_token)

    @staticmethod
    def _fingerprint(action: str, payload: Any) -> str:
        import json
        raw = json.dumps(
            {"action": action, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
