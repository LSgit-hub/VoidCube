from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import requests


EXECUTOR_ROUTE_PREFIX = "/api/executor"


@dataclass(slots=True)
class ExecutorOpsClient:
    """CLI-side helper for routing execution actions through the gateway."""

    gateway_url: str = "http://127.0.0.1:8000"
    timeout: float = 30.0

    def __post_init__(self) -> None:
        self.gateway_url = self.gateway_url.rstrip("/")

    def check_executor_health(self) -> Dict[str, Any]:
        """Verify the canonical executor path is reachable (C-04: fail-closed).

        Returns health status or raises on unreachable — the CLI must not
        silently bypass the executor when it is unavailable.
        """
        try:
            resp = requests.get(f"{self.gateway_url}/", timeout=5.0)
            return {"reachable": resp.status_code == 200, "status_code": resp.status_code}
        except Exception as exc:
            return {"reachable": False, "error": str(exc)}

    def execute_body_upgrade(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.post_executor("/body/upgrade/execute", payload or {})

    def get_body_registry(self) -> Dict[str, Any]:
        return self.get_executor("/body/registry")

    def get_active_body_target(self) -> Dict[str, Any]:
        return self.get_executor("/body/active-target")

    def list_body_slots(self) -> Dict[str, Any]:
        return self.get_executor("/body/slots")

    def get_body_slot(self, slot_id: str) -> Dict[str, Any]:
        return self.get_executor(f"/body/slots/{slot_id}")

    def prepare_body_slot(self, slot_id: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.post_executor(f"/body/slots/{slot_id}/prepare", payload or {})

    def mark_body_candidate(self, slot_id: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.post_executor(f"/body/slots/{slot_id}/candidate", payload or {})

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
        data = response.json()
        if not isinstance(data, dict):
            return {"status": "ok", "response": data}
        return data

    def _get_json(self, url: str) -> Dict[str, Any]:
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return {"status": "ok", "response": data}
        return data
