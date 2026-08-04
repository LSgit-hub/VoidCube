"""Gateway session lookup used by agent-pull task recovery."""

from __future__ import annotations

from typing import Any, Dict

import aiohttp
from fastapi import HTTPException


class AutonomousTaskOwnerSessionService:
    """Read agent session ownership without depending on Supervisor state."""

    def __init__(self, *, gateway_address: str) -> None:
        self._gateway_address = gateway_address.rstrip("/")

    async def fetch(self, session_id: str) -> Dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=5)
        url = f"{self._gateway_address}/v1/sessions/{session_id}"
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status == 404:
                    return {"session_id": session_id, "missing": True}
                if response.status >= 400:
                    raise HTTPException(
                        status_code=503,
                        detail=f"网关 owner 会话查询失败，返回状态 {response.status}",
                    )
                payload = await response.json()
        if not isinstance(payload, dict):
            return {}
        payload.setdefault("session_id", session_id)
        payload.setdefault("missing", False)
        return payload


__all__ = ["AutonomousTaskOwnerSessionService"]
