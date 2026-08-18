"""Authoritative execution-lease validation through the local gateway."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ...infrastructure.gateway.presence import default_gateway_url


class StaleExecutionLeaseError(RuntimeError):
    code = "stale_execution_lease"


def validate_execution_lease(
    *,
    task_id: str,
    generation: int,
    attempt_id: str,
    owner_session_id: str,
    gateway_base: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "generation": int(generation),
            "attempt_id": str(attempt_id),
            "owner_session_id": str(owner_session_id),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        (
            f"{(gateway_base or default_gateway_url()).rstrip('/')}"
            f"/v1/tasks/{task_id}/lease/validate"
        ),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = _read_http_error_detail(exc)
        if exc.code == 409 and detail.get("code") == StaleExecutionLeaseError.code:
            raise StaleExecutionLeaseError(
                str(detail.get("message") or StaleExecutionLeaseError.code)
            ) from exc
        raise RuntimeError(
            f"execution lease validation failed with HTTP {exc.code}: "
            f"{detail.get('message') or detail or exc.reason}"
        ) from exc
    if not isinstance(result, dict) or result.get("valid") is not True:
        raise RuntimeError("execution lease validator returned an invalid response")
    return result


def _read_http_error_detail(exc: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        payload = json.loads(exc.read())
    except (json.JSONDecodeError, OSError, TypeError, UnicodeDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    detail = payload.get("detail", payload)
    return dict(detail) if isinstance(detail, dict) else {"message": str(detail)}
