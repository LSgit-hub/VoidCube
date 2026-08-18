"""Compatibility facade for the canonical VoidCube application API."""

try:
    from voidcube.application.configuration import (
        application_config,
        get_application_config,
        reload_application_config,
        set_application_config,
    )
    from voidcube.application.application_runtime import ApplicationRuntime, ApplicationState
    from voidcube.domain.contracts import *
    from voidcube.domain.session.identity import (
        SessionIdentity,
        generate_session_id,
        resolve_session_identity,
    )
    from voidcube.application.sessions import *
    from voidcube.interfaces.voice.session_runtime import VoiceSessionRuntime
    from voidcube.application.autonomous.execution_runtime import (
        AutonomousExecutionRuntime,
        AutonomousExecutionRuntimePorts,
    )
    from voidcube.application.scheduling import CancellationToken, TurnExecutor, TurnScheduler
    from voidcube.application.single_turn_executor import SingleTurnExecutor, SingleTurnExecutorPorts
except (ModuleNotFoundError, ImportError):
    from src.voidcube.application.configuration import (
        application_config,
        get_application_config,
        reload_application_config,
        set_application_config,
    )
    from src.voidcube.application.application_runtime import ApplicationRuntime, ApplicationState
    from src.voidcube.domain.contracts import *
    from src.voidcube.domain.session.identity import (
        SessionIdentity,
        generate_session_id,
        resolve_session_identity,
    )
    from src.voidcube.application.sessions import *
    from src.voidcube.interfaces.voice.session_runtime import VoiceSessionRuntime
    from src.voidcube.application.autonomous.execution_runtime import (
        AutonomousExecutionRuntime,
        AutonomousExecutionRuntimePorts,
    )
    from src.voidcube.application.scheduling import CancellationToken, TurnExecutor, TurnScheduler
    from src.voidcube.application.single_turn_executor import SingleTurnExecutor, SingleTurnExecutorPorts

__all__ = [
    "application_config",
    "get_application_config",
    "reload_application_config",
    "set_application_config",
    "ApplicationRuntime",
    "ApplicationState",
    "SessionIdentity",
    "generate_session_id",
    "resolve_session_identity",
    "VoiceSessionRuntime",
    "AutonomousExecutionRuntime",
    "AutonomousExecutionRuntimePorts",
    "CancellationToken",
    "TurnExecutor",
    "TurnScheduler",
    "SingleTurnExecutor",
    "SingleTurnExecutorPorts",
]
