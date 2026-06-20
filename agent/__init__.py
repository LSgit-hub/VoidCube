"""Agent internals -- extracted modules from run_agent.py.

These modules contain pure utility functions and self-contained classes
that were previously embedded in the 3,600-line run_agent.py. Extracting
them makes run_agent.py focused on the AIAgent orchestrator class.
"""

from .agent_initializer import (
    load_agent_config,
    initialize_memory_manager,
    initialize_context_compressor,
    initialize_tool_definitions,
    initialize_llm_client,
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
    "initialize_llm_client",
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