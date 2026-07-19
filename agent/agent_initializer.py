from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from VoidCube_core.constants import get_VoidCube_home

logger = logging.getLogger("agent.initializer")


def load_agent_config() -> Dict[str, Any]:
    try:
        from VoidCube_cli.config import load_config as _load_agent_config
        return _load_agent_config()
    except Exception:
        logger.debug("Failed to load agent config, using defaults")
        return {}


def initialize_memory_manager(
    agent_cfg: Dict[str, Any],
    session_id: str,
    platform: str = "cli",
    user_id: Optional[str] = None,
    skip_memory: bool = False,
) -> Optional["MemoryManager"]:
    if skip_memory:
        return None
    
    try:
        from agent.memory_manager import MemoryManager as _MemoryManager
        from plugins.memory import load_memory_provider as _load_mem
        
        mem_config = agent_cfg.get("memory", {})
        _mem_provider_name = mem_config.get("provider", "mem")
        
        if not _mem_provider_name:
            return None
        
        memory_manager = _MemoryManager()
        _mp = _load_mem(_mem_provider_name)
        
        if _mp and _mp.is_available():
            memory_manager.add_provider(_mp)
        
        if memory_manager.providers:
            from VoidCube_core.constants import get_VoidCube_home as _ghh
            _init_kwargs = {
                "session_id": session_id,
                "platform": platform,
                "VoidCube_home": str(_ghh()),
                "agent_context": "primary",
            }
            
            if user_id:
                _init_kwargs["user_id"] = user_id
            
            try:
                from VoidCube_cli.profiles import get_active_profile_name
                _profile = get_active_profile_name()
                _init_kwargs["agent_identity"] = _profile
                _init_kwargs["agent_workspace"] = "VoidCube"
            except Exception:
                pass
            
            memory_manager.initialize_all(**_init_kwargs)
            logger.info("Memory provider '%s' activated", _mem_provider_name)
            return memory_manager
        else:
            logger.debug("Memory provider '%s' not found or not available", _mem_provider_name)
            return None
    except Exception as _mpe:
        logger.warning("Memory provider plugin init failed: %s", _mpe)
        return None


def initialize_context_compressor(agent_cfg: Dict[str, Any]) -> "ContextCompressor":
    _compression_cfg = agent_cfg.get("compression", {})
    if not isinstance(_compression_cfg, dict):
        _compression_cfg = {}
    
    compression_threshold = float(_compression_cfg.get("threshold", 0.50))
    compression_enabled = str(_compression_cfg.get("enabled", True)).lower() in ("true", "1", "yes")
    compression_summary_model = _compression_cfg.get("summary_model") or None
    compression_target_ratio = float(_compression_cfg.get("target_ratio", 0.20))
    compression_protect_last = int(_compression_cfg.get("protect_last_n", 20))
    
    _model_cfg = agent_cfg.get("model", {})
    _config_context_length = None
    if isinstance(_model_cfg, dict):
        _config_context_length = _model_cfg.get("context_length")
    
    if _config_context_length is not None:
        try:
            _config_context_length = int(_config_context_length)
        except (TypeError, ValueError):
            _config_context_length = None
    
    _selected_engine = None
    _engine_name = "compressor"
    
    try:
        _ctx_cfg = agent_cfg.get("context", {}) if isinstance(agent_cfg, dict) else {}
        _engine_name = _ctx_cfg.get("engine", "compressor") or "compressor"
    except Exception:
        pass
    
    if _engine_name != "compressor":
        try:
            from plugins.context_engine import load_context_engine
            _selected_engine = load_context_engine(_engine_name)
        except Exception as _ce_load_err:
            logger.debug("Context engine load from plugins/context_engine/: %s", _ce_load_err)
        
        if _selected_engine is None:
            try:
                from VoidCube_cli.plugins import get_plugin_context_engine
                _candidate = get_plugin_context_engine()
                if _candidate and _candidate.name == _engine_name:
                    _selected_engine = _candidate
            except Exception as _ce_plugin_err:
                logger.debug("Context engine load from plugin system: %s", _ce_plugin_err)
    
    if _selected_engine is None:
        from agent.context_compressor import ContextCompressor
        _selected_engine = ContextCompressor(
            enabled=compression_enabled,
            threshold=compression_threshold,
            summary_model=compression_summary_model,
            target_ratio=compression_target_ratio,
            protect_last_n=compression_protect_last,
            config_context_length=_config_context_length,
        )
    
    return _selected_engine


def initialize_tool_definitions(
    enabled_toolsets: Optional[List[str]] = None,
    disabled_toolsets: Optional[List[str]] = None,
    quiet_mode: bool = False,
    memory_manager=None,
) -> Dict[str, Any]:
    from tools.model_tools import get_tool_definitions
    
    tools = get_tool_definitions(
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
        quiet_mode=quiet_mode,
    )
    
    valid_tool_names = set()
    if tools:
        valid_tool_names = {tool["function"]["name"] for tool in tools}
        tool_names = sorted(valid_tool_names)
        if not quiet_mode:
            print(f"🛠️  Loaded {len(tools)} tools: {', '.join(tool_names)}")
            if enabled_toolsets:
                print(f"   ✅ Enabled toolsets: {', '.join(enabled_toolsets)}")
            if disabled_toolsets:
                print(f"   ❌ Disabled toolsets: {', '.join(disabled_toolsets)}")
    elif not quiet_mode:
        print("🛠️  No tools loaded (all tools filtered out or unavailable)")
    
    if memory_manager and tools is not None:
        for _schema in memory_manager.get_all_tool_schemas():
            _wrapped = {"type": "function", "function": _schema}
            tools.append(_wrapped)
            _tname = _schema.get("name", "")
            if _tname:
                valid_tool_names.add(_tname)
    
    return {
        "tools": tools,
        "valid_tool_names": valid_tool_names,
    }


def resolve_provider_credentials(
    provider: Optional[str],
    model: str,
    base_url: Optional[str],
    api_key: Optional[str],
) -> Dict[str, Any]:
    if api_key and base_url:
        return {
            "api_key": api_key,
            "base_url": base_url,
            "resolved": True,
        }
    
    try:
        from agent.auxiliary_client import resolve_provider_client
        _routed_client, _ = resolve_provider_client(provider or "auto", model=model)
        if _routed_client is not None:
            result = {
                "api_key": _routed_client.api_key,
                "base_url": str(_routed_client.base_url),
                "resolved": True,
            }
            if hasattr(_routed_client, '_default_headers') and _routed_client._default_headers:
                result["default_headers"] = dict(_routed_client._default_headers)
            return result
    except Exception:
        pass
    
    _explicit = (provider or "").strip().lower()
    if _explicit and _explicit not in ("auto", "openrouter", "custom"):
        raise RuntimeError(
            f"Provider '{_explicit}' is set in config.yaml but no API key "
            f"was found. Set the {_explicit.upper()}_API_KEY environment "
            f"variable, or switch to a different provider with `VoidCube model`."
        )
    
    from VoidCube_core.constants import OPENROUTER_BASE_URL
    return {
        "api_key": os.getenv("OPENROUTER_API_KEY", ""),
        "base_url": OPENROUTER_BASE_URL,
        "resolved": True,
        "default_headers": {
            "HTTP-Referer": "https://VoidCube-agent.nousresearch.com",
            "X-OpenRouter-Title": "Voidcube Agent",
            "X-OpenRouter-Categories": "productivity,cli-agent",
        },
    }


def initialize_session_logging(session_id: str, VoidCube_home: str):
    from VoidCube_core.logging import setup_logging, setup_verbose_logging
    setup_logging(VoidCube_home=VoidCube_home)


def initialize_checkpoint_manager(enabled: bool = False, max_snapshots: int = 50):
    from tools.checkpoint_manager import CheckpointManager
    return CheckpointManager(enabled=enabled, max_snapshots=max_snapshots)


def initialize_todo_store():
    from tools.todo_tool import TodoStore
    return TodoStore()


def initialize_session_state(session_id: str, VoidCube_home: str):
    try:
        from VoidCube_cli.session_state import SessionState
        return SessionState(session_id, VoidCube_home)
    except Exception:
        return None


def initialize_memory_store(agent_cfg: Dict[str, Any], skip_memory: bool = False):
    if skip_memory:
        return None, False, False
    
    try:
        mem_config = agent_cfg.get("memory", {})
        memory_enabled = mem_config.get("memory_enabled", False)
        user_profile_enabled = mem_config.get("user_profile_enabled", False)
        
        if not (memory_enabled or user_profile_enabled):
            return None, False, False
        
        from tools.memory_tool import MemoryStore
        memory_store = MemoryStore(
            memory_char_limit=mem_config.get("memory_char_limit", 2200),
            user_char_limit=mem_config.get("user_char_limit", 1375),
        )
        memory_store.load_from_disk()
        
        return memory_store, memory_enabled, user_profile_enabled
    except Exception:
        return None, False, False


def initialize_fallback_chain(fallback_model: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if isinstance(fallback_model, list):
        return [
            f for f in fallback_model
            if isinstance(f, dict) and f.get("provider") and f.get("model")
        ]
    elif isinstance(fallback_model, dict) and fallback_model.get("provider") and fallback_model.get("model"):
        return [fallback_model]
    return []


def initialize_model_metadata_prewarm(provider: str, base_url: str):
    if provider == "openrouter" or "openrouter" in base_url.lower():
        import threading
        from agent.model_metadata import fetch_model_metadata
        threading.Thread(
            target=lambda: fetch_model_metadata(),
            daemon=True,
        ).start()


def initialize_safe_stdio():
    from agent.stream_handler import _SafeWriter
    import sys
    
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and not isinstance(stream, _SafeWriter):
            setattr(sys, stream_name, _SafeWriter(stream))
