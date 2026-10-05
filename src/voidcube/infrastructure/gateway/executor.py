from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict
from urllib.parse import quote, urlsplit

import requests


EXECUTOR_ROUTE_PREFIX = "/api/executor"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:6000"


class ExecutorOpsClientError(RuntimeError):
    """Raised when the executor endpoint violates its response contract."""


def default_gateway_url() -> str:
    """Resolve the canonical Gateway address used by the service launcher."""
    try:
        from ..config.system import get_config

        gateway = get_config().gateway
        return f"http://{gateway.host}:{gateway.port}"
    except Exception:
        return DEFAULT_GATEWAY_URL


@dataclass(slots=True)
class ExecutorOpsClient:
    """CLI-side helper for routing execution actions through the gateway."""

    gateway_url: str = ""
    timeout: float = 30.0

    def __post_init__(self) -> None:
        normalized_url = (self.gateway_url.strip() or default_gateway_url()).rstrip("/")
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
            normalized_timeout = float(self.timeout)
        except (TypeError, ValueError):
            raise ValueError("Executor timeout must be a finite positive number") from None
        if not math.isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError("Executor timeout must be a finite positive number")
        self.gateway_url = normalized_url
        self.timeout = normalized_timeout

    def execute_body_upgrade(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.post_executor("/body/upgrade/execute", payload or {})

    def confirm_body_switch(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.post_executor("/body/switch/consent", payload or {})

    def get_body_registry(self) -> Dict[str, Any]:
        return self.get_executor("/body/registry")

    def get_active_body_target(self) -> Dict[str, Any]:
        return self.get_executor("/body/active-target")

    def list_body_slots(self) -> Dict[str, Any]:
        return self.get_executor("/body/slots")

    def get_body_slot(self, slot_id: str) -> Dict[str, Any]:
        return self.get_executor(f"/body/slots/{self._slot_segment(slot_id)}")

    def prepare_body_slot(self, slot_id: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.post_executor(f"/body/slots/{self._slot_segment(slot_id)}/prepare", payload or {})

    def mark_body_candidate(self, slot_id: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.post_executor(f"/body/slots/{self._slot_segment(slot_id)}/candidate", payload or {})

    @staticmethod
    def _slot_segment(slot_id: str) -> str:
        raw = str(slot_id or "")
        if "/" in raw or "\\" in raw:
            raise ValueError("slot_id cannot contain path separators")
        if not raw:
            raise ValueError("slot_id is required")
        return quote(raw, safe="")

    def run_body_probe(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.post_executor("/body/probe/run", payload)

    def post_executor(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized_path = "/" + path.lstrip("/")
        executor_url = f"{self.gateway_url}{EXECUTOR_ROUTE_PREFIX}{normalized_path}"
        return self._post_json(executor_url, payload)

    def get_executor(self, path: str) -> Dict[str, Any]:
        normalized_path = "/" + path.lstrip("/")
        executor_url = f"{self.gateway_url}{EXECUTOR_ROUTE_PREFIX}{normalized_path}"
        return self._get_json(executor_url)

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise ExecutorOpsClientError("Executor Service returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ExecutorOpsClientError("Executor Service returned a non-object response")
        return data

    def _get_json(self, url: str) -> Dict[str, Any]:
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise ExecutorOpsClientError("Executor Service returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ExecutorOpsClientError("Executor Service returned a non-object response")
        return data
