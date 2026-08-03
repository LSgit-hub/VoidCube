import VoidCube_cli.cli_turn_agent_route_runtime as route_module
from VoidCube_cli.cli_turn_agent_route_runtime import (
    CliTurnAgentRoutePorts,
    CliTurnAgentRouteRuntime,
)


def test_route_runtime_projects_runtime_credentials_and_clears_overrides_without_tier(monkeypatch):
    captured = {}

    def resolve(message, routing, credentials):
        captured.update(message=message, routing=routing, credentials=credentials)
        return {"model": "resolved-model"}

    monkeypatch.setattr(route_module, "resolve_turn_route", resolve)
    route = CliTurnAgentRouteRuntime(
        CliTurnAgentRoutePorts(
            smart_model_routing={"enabled": True},
            runtime_credentials={
                "model": "configured",
                "provider": "custom",
                "args": ("--local",),
                "credential_pool": "pool",
            },
            service_tier=None,
        )
    ).resolve("hello")

    assert captured["message"] == "hello"
    assert captured["routing"] == {"enabled": True}
    assert captured["credentials"]["args"] == ["--local"]
    assert route == {"model": "resolved-model", "request_overrides": None}


def test_route_runtime_adds_fast_mode_overrides(monkeypatch):
    monkeypatch.setattr(
        route_module,
        "resolve_turn_route",
        lambda *_args: {"model": "fast-model"},
    )
    monkeypatch.setattr(
        route_module,
        "resolve_fast_mode_overrides",
        lambda model: {"service_tier": "fast", "model": model},
    )

    route = CliTurnAgentRouteRuntime(
        CliTurnAgentRoutePorts(
            smart_model_routing=None,
            runtime_credentials={"model": "configured"},
            service_tier="fast",
        )
    ).resolve("question")

    assert route["request_overrides"] == {
        "service_tier": "fast",
        "model": "fast-model",
    }
