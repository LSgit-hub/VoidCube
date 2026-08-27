"""CLI-side HTTP adapter for Supervisor voice controls."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from .presence import default_gateway_url


class SupervisorVoiceClientError(RuntimeError):
    """Raised when the Supervisor voice endpoint cannot be reached."""


class SupervisorVoiceClient:
    """Route terminal voice controls through the Gateway to Supervisor."""

    def __init__(self, *, base_url: str | None = None, timeout_seconds: float = 60.0) -> None:
        self.base_url = (base_url or default_gateway_url()).rstrip("/")
        self.timeout_seconds = max(0.1, float(timeout_seconds))

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
        return dict(decoded) if isinstance(decoded, dict) else {}


__all__ = ["SupervisorVoiceClient", "SupervisorVoiceClientError"]
