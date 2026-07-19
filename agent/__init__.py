"""Agent internals -- extracted modules from run_agent.py.

These modules contain utilities and self-contained runtime components
extracted from run_agent.py so the root module can converge on Agent
orchestration instead of owning provider and subsystem implementations.
"""

from .agent_initializer import (
    load_agent_config,
    initialize_memory_manager,
    initialize_context_compressor,
    initialize_tool_definitions,
    resolve_provider_credentials,
    initialize_session_logging,
    initialize_checkpoint_manager,
    initialize_todo_store,
    initialize_session_state,
    initialize_memory_store,
    initialize_fallback_chain,
    initialize_model_metadata_prewarm,
    initialize_safe_stdio,
)

__all__ = [
    "load_agent_config",
    "initialize_memory_manager",
    "initialize_context_compressor",
    "initialize_tool_definitions",
    "resolve_provider_credentials",
    "initialize_session_logging",
    "initialize_checkpoint_manager",
    "initialize_todo_store",
    "initialize_session_state",
    "initialize_memory_store",
    "initialize_fallback_chain",
    "initialize_model_metadata_prewarm",
    "initialize_safe_stdio",
]
