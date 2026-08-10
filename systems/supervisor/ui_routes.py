"""Thin HTTP route adapter for the Supervisor web UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI


@dataclass(frozen=True, slots=True)
class SupervisorUIRoutePorts:
    app: FastAPI
    enabled: bool
    ui_path: str
    get_ui: Callable[..., Any]
    get_state: Callable[..., Any]
    get_events: Callable[..., Any]
    get_voice_levels: Callable[..., Any]
    get_media_events: Callable[..., Any]
    enqueue_media: Callable[..., Any]
    enqueue_media_playlist: Callable[..., Any]
    get_identity_archive: Callable[..., Any]
    get_identity_turns: Callable[..., Any]
    get_evolution_audit: Callable[..., Any]
    get_evolution_candidates: Callable[..., Any]
    consent_evolution_candidate: Callable[..., Any]
    verify_identity_experience: Callable[..., Any]
    control_media: Callable[..., Any] | None = None
    list_accounts: Callable[..., Any] | None = None
    add_account: Callable[..., Any] | None = None
    delete_account: Callable[..., Any] | None = None
    verify_account: Callable[..., Any] | None = None


def mount_supervisor_ui_routes(ports: SupervisorUIRoutePorts) -> None:
    if not ports.enabled:
        return

    app = ports.app
    app.add_api_route(ports.ui_path, ports.get_ui, methods=["GET"])
    app.add_api_route("/ui/state", ports.get_state, methods=["GET"])
    app.add_api_route("/ui/events", ports.get_events, methods=["GET"])
    app.add_api_route("/ui/voice-levels", ports.get_voice_levels, methods=["GET"])
    app.add_api_route("/ui/media-events", ports.get_media_events, methods=["GET"])
    app.add_api_route("/ui/media/enqueue", ports.enqueue_media, methods=["POST"])
    app.add_api_route("/ui/media/playlist", ports.enqueue_media_playlist, methods=["POST"])
    if ports.control_media is not None:
        app.add_api_route("/ui/media/control", ports.control_media, methods=["POST"])
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
    app.add_api_route(
        "/ui/identity/experiences/verify",
        ports.verify_identity_experience,
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


__all__ = ["SupervisorUIRoutePorts", "mount_supervisor_ui_routes"]
