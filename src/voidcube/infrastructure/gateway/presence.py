"""Shared Gateway presence client used by application frontends."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request


DEFAULT_GATEWAY_URL = "http://127.0.0.1:6000"


def default_gateway_url() -> str:
    """Resolve the configured Gateway address without depending on a frontend."""
    try:
        from ..config.system import get_config

        gateway = get_config().gateway
        return f"http://{gateway.host}:{gateway.port}"
    except Exception:
        return DEFAULT_GATEWAY_URL


def gateway_auth_headers(auth_token: str = "") -> dict[str, str]:
    """Return the configured Gateway control-plane authentication headers."""
    token = str(auth_token or "").strip() or str(
        os.getenv("GATEWAY_AUTH_TOKEN") or ""
    ).strip()
    if not token:
        try:
            from ..config.system import get_config

            token = str(get_config().gateway.auth_token or "").strip()
        except Exception:
            token = ""
    return {"Authorization": f"Bearer {token}"} if token else {}


@dataclass(frozen=True, slots=True)
class GatewayPresenceClient:
    """Best-effort client for session registration and scene reporting."""

    base_url: str = ""
    auth_token: str = ""

    @property
    def url(self) -> str:
        return (self.base_url.strip() or default_gateway_url()).rstrip("/")

    def is_running(
        self,
        timeout: float = 0.3,
        *,
        socket_factory: Callable[..., Any] | None = None,
    ) -> bool:
        parsed = urlsplit(self.url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        connection = None
        try:
            socket_factory = socket_factory or socket.socket
            connection = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
            connection.settimeout(timeout)
            connection.connect((host, port))
            return True
        except OSError:
            return False
        finally:
            if connection is not None:
                connection.close()

    def register_session(
        self,
        session_id: str,
        model: str,
        provider: str,
        *,
        source: str,
        owner_id: str | None = None,
        workspace_id: str | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> bool:
        payload = {
            "session_id": session_id,
            "model": model,
            "provider": provider,
            "source": str(source or "").strip(),
        }
        if owner_id is not None:
            payload["owner_id"] = str(owner_id)
        if workspace_id is not None:
            payload["workspace_id"] = str(workspace_id)
        return self._post_json(
            "/v1/sessions/register",
            payload,
            timeout=3,
            include_auth=True,
            opener=opener,
        )

    def push_agent_scene(
        self,
        scene: str,
        *,
        source_service: str,
        session_id: str | None = None,
        task_id: str | None = None,
        execution_kind: str | None = None,
        subagent_summary: Mapping[str, Any] | None = None,
        agent_role: str | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> bool:
        normalized_scene = str(scene or "").strip().lower()
        if not normalized_scene:
            return False

        metadata: dict[str, Any] = {"scene": normalized_scene}
        if task_id:
            metadata["task_id"] = task_id
        if execution_kind:
            metadata["execution_kind"] = execution_kind

        normalized_role = str(agent_role or "").strip().lower()
        if normalized_role in {"supervisor_task", "user_chat"}:
            metadata["agent_role"] = normalized_role
        if isinstance(subagent_summary, Mapping):
            foreground_count = max(0, int(subagent_summary.get("foreground_count") or 0))
            background_count = max(0, int(subagent_summary.get("background_count") or 0))
            total_count = max(
                foreground_count + background_count,
                int(subagent_summary.get("total_count") or 0),
            )
            metadata.update(
                {
                    "subagent_foreground_count": foreground_count,
                    "subagent_background_count": background_count,
                    "subagent_total_count": total_count,
                }
            )
            for source_key, target_key in (
                ("focus_task_id", "subagent_focus_task_id"),
                ("focus_tool", "subagent_focus_tool"),
                ("focus_preview", "subagent_focus_preview"),
            ):
                value = str(subagent_summary.get(source_key) or "").strip()
                if value:
                    metadata[target_key] = value

        return self._post_json(
            "/admin/activity/touch",
            {
                "activity_kind": "agent_scene",
                "source_service": str(source_service or "").strip(),
                "session_id": session_id,
                "metadata": metadata,
            },
            timeout=3,
            include_auth=True,
            opener=opener,
        )

    def _post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout: float,
        include_auth: bool,
        opener: Callable[..., Any] | None,
    ) -> bool:
        headers = {"Content-Type": "application/json"}
        if include_auth:
            headers.update(gateway_auth_headers(self.auth_token))
        request = Request(
            f"{self.url}/{path.lstrip('/')}",
            data=json.dumps(dict(payload)).encode(),
            headers=headers,
            method="POST",
        )
        try:
            if opener is None:
                from urllib.request import urlopen

                opener = urlopen
            opener(request, timeout=timeout)
            return True
        except Exception:
            return False


gateway_presence = GatewayPresenceClient()


def is_gateway_running(timeout: float = 0.3) -> bool:
    return gateway_presence.is_running(timeout)


def register_session(
    session_id: str,
    model: str,
    provider: str,
    *,
    source: str,
    owner_id: str | None = None,
    workspace_id: str | None = None,
) -> bool:
    return gateway_presence.register_session(
        session_id,
        model,
        provider,
        source=source,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )


def push_agent_scene(scene: str, **kwargs: Any) -> bool:
    return gateway_presence.push_agent_scene(scene, **kwargs)
