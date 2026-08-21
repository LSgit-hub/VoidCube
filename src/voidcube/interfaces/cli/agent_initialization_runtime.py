"""Construct an agent from explicit CLI-provided initialization ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True, slots=True)
class CliAgentInitializationPorts:
    """Agent factory inputs supplied by the CLI host."""

    agent_factory: Callable[..., Any]
    runtime: Mapping[str, Any]
    model: str
    max_iterations: int
    enabled_toolsets: Optional[Sequence[str]]
    verbose_logging: bool
    quiet_mode: bool
    ephemeral_system_prompt: Optional[str]
    prefill_messages: Optional[Sequence[Mapping[str, Any]]]
    reasoning_config: Any
    service_tier: Any
    request_overrides: Optional[Mapping[str, Any]]
    providers_allowed: Any
    providers_ignored: Any
    providers_order: Any
    provider_sort: Any
    provider_require_parameters: bool
    provider_data_collection: Any
    session_id: str
    platform: str
    session_db: Any
    clarification_sink: Any
    reasoning_callback: Any
    fallback_providers: Any
    thinking_callback: Any
    checkpoints_enabled: bool
    checkpoint_max_snapshots: int
    pass_session_id: bool
    tool_event_sink: Any
    stream_delta_callback: Any
    tool_gen_callback: Any
    persist_session: Optional[bool] = None
    skip_memory: Optional[bool] = None
    skip_context_files: Optional[bool] = None
    autonomous_task_provider: Any = None
    validate_execution_lease: Any = None


class CliAgentInitializationRuntime:
    """Own only the AIAgent constructor wiring, without CLI host access."""

    def __init__(self, ports: CliAgentInitializationPorts) -> None:
        self.ports = ports

    def create(self) -> Any:
        ports = self.ports
        runtime = ports.runtime
        kwargs = {
            "model": ports.model,
            "api_key": runtime.get("api_key"),
            "base_url": runtime.get("base_url"),
            "provider": runtime.get("provider"),
            "acp_command": runtime.get("command"),
            "acp_args": list(runtime.get("args") or []),
            "credential_pool": runtime.get("credential_pool"),
            "max_iterations": ports.max_iterations,
            "enabled_toolsets": ports.enabled_toolsets,
            "verbose_logging": ports.verbose_logging,
            "quiet_mode": ports.quiet_mode,
            "ephemeral_system_prompt": ports.ephemeral_system_prompt,
            "prefill_messages": ports.prefill_messages,
            "reasoning_config": ports.reasoning_config,
            "service_tier": ports.service_tier,
            "request_overrides": dict(ports.request_overrides) if ports.request_overrides else None,
            "providers_allowed": ports.providers_allowed,
            "providers_ignored": ports.providers_ignored,
            "providers_order": ports.providers_order,
            "provider_sort": ports.provider_sort,
            "provider_require_parameters": ports.provider_require_parameters,
            "provider_data_collection": ports.provider_data_collection,
            "session_id": ports.session_id,
            "platform": ports.platform,
            "session_db": ports.session_db,
            "clarification_sink": ports.clarification_sink,
            "reasoning_callback": ports.reasoning_callback,
            "fallback_providers": ports.fallback_providers,
            "thinking_callback": ports.thinking_callback,
            "checkpoints_enabled": ports.checkpoints_enabled,
            "checkpoint_max_snapshots": ports.checkpoint_max_snapshots,
            "pass_session_id": ports.pass_session_id,
            "tool_event_sink": ports.tool_event_sink,
            "stream_delta_callback": ports.stream_delta_callback,
            "tool_gen_callback": ports.tool_gen_callback,
            "autonomous_task_provider": ports.autonomous_task_provider,
            "validate_execution_lease": ports.validate_execution_lease,
        }
        if ports.persist_session is not None:
            kwargs["persist_session"] = ports.persist_session
        if ports.skip_memory is not None:
            kwargs["skip_memory"] = ports.skip_memory
        if ports.skip_context_files is not None:
            kwargs["skip_context_files"] = ports.skip_context_files
        return ports.agent_factory(**kwargs)
