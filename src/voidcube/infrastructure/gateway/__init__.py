"""Gateway process ownership and service lifecycle adapters."""

from .daemon_runtime import (
    auto_start_daemons,
    clear_daemons_auto_started,
    daemons_auto_started,
    handle_serve_command,
    mark_daemons_auto_started,
    maybe_stop_daemons_on_exit,
)
from .presence import DEFAULT_GATEWAY_URL, GatewayPresenceClient, default_gateway_url
from .executor import ExecutorOpsClient
from .internal_gateway import (
    AgentRequest,
    AgentResponse,
    GatewayConfig,
    InternalGateway,
    ServiceInfo,
)

__all__ = [
    "auto_start_daemons",
    "clear_daemons_auto_started",
    "daemons_auto_started",
    "handle_serve_command",
    "mark_daemons_auto_started",
    "maybe_stop_daemons_on_exit",
    "DEFAULT_GATEWAY_URL",
    "GatewayPresenceClient",
    "default_gateway_url",
    "ExecutorOpsClient",
    "AgentRequest",
    "AgentResponse",
    "GatewayConfig",
    "InternalGateway",
    "ServiceInfo",
]
