"""HTTP adapter for Supervisor scheduled-task operations."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from ...application.scheduling.scheduled_executor import ScheduledRequestRejected
from .presence import default_gateway_url


class SupervisorScheduledTaskClient:
    def __init__(self, *, base_url: str | None = None, timeout_seconds: float = 10.0) -> None:
        self.base_url = (base_url or default_gateway_url()).rstrip("/")
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/api/supervisor{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ScheduledRequestRejected(
                exc.code, f"HTTP {exc.code}: {exc.reason}"
            ) from exc
        return dict(decoded) if isinstance(decoded, dict) else {}

    def get(self, path: str) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/api/supervisor{path}",
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ScheduledRequestRejected(
                exc.code, f"HTTP {exc.code}: {exc.reason}"
            ) from exc
        return dict(decoded) if isinstance(decoded, dict) else {}


__all__ = ["SupervisorScheduledTaskClient"]
