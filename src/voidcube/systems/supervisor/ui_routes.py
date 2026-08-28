"""Thin HTTP route adapter for the Supervisor web UI."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("supervisor.ui")


@dataclass(frozen=True, slots=True)
class SupervisorUIRoutePorts:
    app: FastAPI
    enabled: bool
    ui_path: str
    get_ui: Callable[..., Any]
    get_state: Callable[..., Any]
    get_events: Callable[..., Any]
    get_api_b_thinking_events: Callable[..., Any]
    get_voice_levels: Callable[..., Any]
    get_media_events: Callable[..., Any]
    enqueue_media: Callable[..., Any]
    enqueue_media_playlist: Callable[..., Any]
    get_identity_archive: Callable[..., Any]
    get_identity_turns: Callable[..., Any]
    get_evolution_audit: Callable[..., Any]
    get_evolution_candidates: Callable[..., Any]
    consent_evolution_candidate: Callable[..., Any]
    control_media: Callable[..., Any] | None = None
    list_accounts: Callable[..., Any] | None = None
    add_account: Callable[..., Any] | None = None
    delete_account: Callable[..., Any] | None = None
    verify_account: Callable[..., Any] | None = None
    get_delivery_events: Callable[..., Any] | None = None
    push_delivery: Callable[..., Any] | None = None
    control_delivery: Callable[..., Any] | None = None
    upload_delivery_asset: Callable[..., Any] | None = None
    get_delivery_asset: Callable[..., Any] | None = None


def mount_supervisor_ui_routes(ports: SupervisorUIRoutePorts) -> None:
    if not ports.enabled:
        return
    app = ports.app
    app.add_api_route(ports.ui_path, ports.get_ui, methods=["GET"])
    app.add_api_route("/ui/state", ports.get_state, methods=["GET"])
    app.add_api_route("/ui/events", ports.get_events, methods=["GET"])
    app.add_api_route(
        "/ui/api-b-thinking-events",
        ports.get_api_b_thinking_events,
        methods=["GET"],
    )
    app.add_api_route("/ui/voice-levels", ports.get_voice_levels, methods=["GET"])
    app.add_api_route("/ui/media-events", ports.get_media_events, methods=["GET"])
    app.add_api_route("/ui/media/enqueue", ports.enqueue_media, methods=["POST"])
    app.add_api_route("/ui/media/playlist", ports.enqueue_media_playlist, methods=["POST"])
    if ports.control_media is not None:
        app.add_api_route("/ui/media/control", ports.control_media, methods=["POST"])
    if ports.get_delivery_events is not None:
        app.add_api_route("/ui/delivery-events", ports.get_delivery_events, methods=["GET"])
    if ports.push_delivery is not None:
        app.add_api_route("/ui/delivery/push", ports.push_delivery, methods=["POST"])
    if ports.control_delivery is not None:
        app.add_api_route("/ui/delivery/control", ports.control_delivery, methods=["POST"])
    if ports.upload_delivery_asset is not None:
        app.add_api_route(
            "/ui/delivery/assets", ports.upload_delivery_asset, methods=["POST"]
        )
    if ports.get_delivery_asset is not None:
        app.add_api_route(
            "/ui/delivery/assets/{artifact_id}/{filename}",
            ports.get_delivery_asset,
            methods=["GET"],
        )
    app.add_api_route(
        "/ui/identity/archive",
        ports.get_identity_archive,
        methods=["GET"],
    )
    app.add_api_route(
        "/ui/identity/turns",
        ports.get_identity_turns,
        methods=["GET"],
    )
    app.add_api_route(
        "/ui/evolution-promotions",
        ports.get_evolution_audit,
        methods=["GET"],
    )
    app.add_api_route(
        "/ui/evolution-promotion-candidates",
        ports.get_evolution_candidates,
        methods=["GET"],
    )
    app.add_api_route(
        "/ui/evolution-promotion-candidates/{candidate_id}/consent",
        ports.consent_evolution_candidate,
        methods=["POST"],
    )
    if ports.list_accounts is not None:
        app.add_api_route("/ui/accounts", ports.list_accounts, methods=["GET"])
    if ports.add_account is not None:
        app.add_api_route("/ui/accounts", ports.add_account, methods=["POST"])
    if ports.delete_account is not None:
        app.add_api_route("/ui/accounts/{account_id}", ports.delete_account, methods=["DELETE"])
    if ports.verify_account is not None:
        app.add_api_route("/ui/accounts/{account_id}/verify", ports.verify_account, methods=["POST"])


def mount_plugin_web_routes(app: FastAPI) -> None:
    """Mount static UIs declared by enabled plugins (plugins/*/plugin.json web 段).

    插件通过清单声明：
      "web": {"mount_path": "/goal-manager", "static_dir": "web/dist", "entry": "index.html"}

    目录缺失或挂载失败只记日志，不阻断 Supervisor 启动（插件故障隔离）。
    """
    from ...extensions.plugins.registry import find_plugin_web_uis

    mounted_paths: set[str] = set()
    existing_paths = {
        str(getattr(route, "path", "")).rstrip("/") or "/"
        for route in app.routes
        if getattr(route, "path", None)
    }
    for web_ui in find_plugin_web_uis():
        static_dir = Path(web_ui["static_dir"])
        mount_path = str(web_ui["mount_path"]).rstrip("/") or "/"
        if mount_path in mounted_paths or _plugin_mount_conflicts(mount_path, existing_paths):
            logger.warning("插件 %s web 挂载路径冲突: %s", web_ui["name"], mount_path)
            continue
        if not static_dir.is_dir():
            logger.warning(
                "插件 %s web 静态目录不存在: %s", web_ui["name"], static_dir
            )
            continue
        try:
            app.mount(
                mount_path,
                StaticFiles(directory=str(static_dir), html=True),
                name=f"plugin-web-{web_ui['name']}",
            )
            mounted_paths.add(mount_path)
            logger.info(
                "挂载插件 %s web UI %s -> %s",
                web_ui["name"],
                mount_path,
                static_dir,
            )
        except Exception as exc:
            logger.warning("挂载插件 %s web UI 失败: %s", web_ui["name"], exc)


def _plugin_mount_conflicts(mount_path: str, existing_paths: set[str]) -> bool:
    for path in existing_paths:
        if path == mount_path:
            return True
        if path.startswith(f"{mount_path}/"):
            return True
    return False


__all__ = [
    "SupervisorUIRoutePorts",
    "mount_supervisor_ui_routes",
    "mount_plugin_web_routes",
]
