"""HTTP adapter for Supervisor scheduled-task operations."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from typing import Any, Dict
from urllib.parse import urlsplit

from ...application.scheduling.scheduled_executor import ScheduledRequestRejected
from .presence import default_gateway_url


class SupervisorScheduledTaskClient:
    def __init__(self, *, base_url: str | None = None, timeout_seconds: float = 10.0) -> None:
        normalized_url = (base_url or default_gateway_url()).strip().rstrip("/")
        parsed_url = urlsplit(normalized_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("Gateway URL must use http or https")
        try:
            normalized_timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            raise ValueError("Scheduled task timeout must be a finite positive number") from None
        if not math.isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError("Scheduled task timeout must be a finite positive number")
        self.base_url = normalized_url
        self.timeout_seconds = normalized_timeout

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
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScheduledRequestRejected(502, "Supervisor returned invalid JSON") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ScheduledRequestRejected(503, "Supervisor is unavailable") from exc
        if not isinstance(decoded, dict):
            raise ScheduledRequestRejected(502, "Supervisor returned a non-object response")
        return decoded

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
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScheduledRequestRejected(502, "Supervisor returned invalid JSON") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ScheduledRequestRejected(503, "Supervisor is unavailable") from exc
        if not isinstance(decoded, dict):
            raise ScheduledRequestRejected(502, "Supervisor returned a non-object response")
        return decoded


__all__ = ["SupervisorScheduledTaskClient"]
