from voidcube.interfaces.cli.agent_initialization_runtime import (
    CliAgentInitializationPorts,
    CliAgentInitializationRuntime,
)


def test_agent_initialization_runtime_forwards_runtime_and_host_ports():
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return "agent"

    result = CliAgentInitializationRuntime(
        CliAgentInitializationPorts(
            agent_factory=factory,
            runtime={
                "api_key": "key",
                "base_url": "https://example.test/v1",
                "provider": "custom",
                "command": "runner",
                "args": ("--stdio",),
                "credential_pool": "pool",
            },
            model="model",
            max_iterations=90,
            enabled_toolsets=["web"],
            verbose_logging=True,
            quiet_mode=False,
            ephemeral_system_prompt=None,
            prefill_messages=None,
            reasoning_config=None,
            service_tier=None,
            request_overrides={"temperature": 0},
            providers_allowed=None,
            providers_ignored=None,
            providers_order=None,
            provider_sort=None,
            provider_require_parameters=False,
            provider_data_collection=None,
            session_id="session",
            platform="cli",
            session_db="db",
            clarification_sink="clarification",
            reasoning_callback="reasoning",
            fallback_providers=[],
            thinking_callback="thinking",
            checkpoints_enabled=True,
            checkpoint_max_snapshots=5,
            pass_session_id=True,
            tool_event_sink="events",
            stream_delta_callback="stream",
            tool_gen_callback="tool-gen",
        )
    ).create()

    assert result == "agent"
    assert captured["model"] == "model"
    assert captured["api_key"] == "key"
    assert captured["acp_args"] == ["--stdio"]
    assert captured["request_overrides"] == {"temperature": 0}
    assert captured["session_id"] == "session"
    assert captured["tool_gen_callback"] == "tool-gen"
