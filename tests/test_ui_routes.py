from fastapi import FastAPI

from voidcube.systems.supervisor.ui_routes import (
    SupervisorUIRoutePorts,
    mount_supervisor_ui_routes,
)


def _ports(app: FastAPI, *, enabled: bool) -> SupervisorUIRoutePorts:
    callback = lambda **_kwargs: None
    return SupervisorUIRoutePorts(
        app=app,
        enabled=enabled,
        ui_path="/ui",
        get_ui=callback,
        get_state=callback,
        get_events=callback,
        get_api_b_thinking_events=callback,
        get_voice_levels=callback,
        get_media_events=callback,
        enqueue_media=callback,
        enqueue_media_playlist=callback,
        get_identity_archive=callback,
        get_identity_turns=callback,
        get_evolution_audit=callback,
        get_evolution_candidates=callback,
        consent_evolution_candidate=callback,
        control_media=callback,
        get_delivery_events=callback,
        push_delivery=callback,
        control_delivery=callback,
        upload_delivery_asset=callback,
        get_delivery_asset=callback,
    )


def test_ui_routes_mount_only_when_enabled() -> None:
    app = FastAPI()
    mount_supervisor_ui_routes(_ports(app, enabled=False))
    assert {route.path for route in app.routes} == {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

    mount_supervisor_ui_routes(_ports(app, enabled=True))
    paths = {route.path for route in app.routes}
    assert "/ui" in paths
    assert "/ui/state" in paths
    assert "/ui/events" in paths
    assert "/ui/api-b-thinking-events" in paths
    assert "/ui/media/enqueue" in paths
    assert "/ui/media/playlist" in paths
    assert "/ui/media/control" in paths
    assert "/ui/delivery-events" in paths
    assert "/ui/delivery/push" in paths
    assert "/ui/delivery/control" in paths
    assert "/ui/delivery/assets" in paths
    assert "/ui/delivery/assets/{artifact_id}/{filename}" in paths
    assert "/ui/evolution-promotion-candidates/{candidate_id}/consent" in paths
    assert "/ui/accounts/import" not in paths
