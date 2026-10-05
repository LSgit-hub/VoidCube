"""CLI-side HTTP adapter for Supervisor voice controls."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from typing import Any, Dict
from urllib.parse import urlsplit

from .presence import default_gateway_url


class SupervisorVoiceClientError(RuntimeError):
    """Raised when the Supervisor voice endpoint cannot be reached."""


class SupervisorVoiceClient:
    """Route terminal voice controls through the Gateway to Supervisor."""

    def __init__(self, *, base_url: str | None = None, timeout_seconds: float = 60.0) -> None:
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
            raise ValueError("Supervisor voice timeout must be a finite positive number") from None
        if not math.isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError("Supervisor voice timeout must be a finite positive number")
        self.base_url = normalized_url
        self.timeout_seconds = normalized_timeout

    def status(self) -> Dict[str, Any]:
        return self._request_json("GET", "/voice/status", {})

    def set_microphone(self, enabled: bool) -> Dict[str, Any]:
        return self._request_json("POST", "/voice/microphone", {"enabled": bool(enabled)})

    def start_session(self, *, session_id: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if session_id:
            payload["session_id"] = session_id
        return self._request_json("POST", "/voice/session/start", payload)

    def interrupt_session(self) -> Dict[str, Any]:
        return self._request_json("POST", "/voice/session/interrupt", {})

    def start_continuous(self, *, session_id: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if session_id:
            payload["session_id"] = session_id
        return self._request_json("POST", "/voice/continuous/start", payload)

    def stop_continuous(self) -> Dict[str, Any]:
        return self._request_json("POST", "/voice/continuous/stop", {})

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        data = None
        headers: Dict[str, str] = {}
        if method != "GET":
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}/api/supervisor{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise SupervisorVoiceClientError(f"HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:
            raise SupervisorVoiceClientError(str(exc)) from exc
        if not isinstance(decoded, dict):
            raise SupervisorVoiceClientError("Supervisor voice returned a non-object response")
        return decoded


__all__ = ["SupervisorVoiceClient", "SupervisorVoiceClientError"]
