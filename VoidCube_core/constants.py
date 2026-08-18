"""Legacy compatibility facade for paths, environment and provider constants."""

from VoidCube_app.infrastructure.config.runtime_paths import (
    display_VoidCube_home,
    get_VoidCube_home,
    get_cache_dir,
    get_config_path,
    get_default_VoidCube_root,
    get_env_path,
    get_logs_dir,
    get_optional_skills_dir,
    get_skills_dir,
    get_subprocess_home,
)
from VoidCube_app.infrastructure.network import apply_ipv4_preference
from VoidCube_app.infrastructure.providers.endpoints import (
    AI_GATEWAY_BASE_URL,
    NOUS_API_BASE_URL,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODELS_URL,
)
from VoidCube_app.infrastructure.runtime.environment import is_container, is_termux, is_wsl
from VoidCube_app.infrastructure.shared.reasoning import (
    VALID_REASONING_EFFORTS,
    parse_reasoning_effort,
)


__all__ = [
    "AI_GATEWAY_BASE_URL",
    "NOUS_API_BASE_URL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_MODELS_URL",
    "VALID_REASONING_EFFORTS",
    "apply_ipv4_preference",
    "display_VoidCube_home",
    "get_VoidCube_home",
    "get_cache_dir",
    "get_config_path",
    "get_default_VoidCube_root",
    "get_env_path",
    "get_logs_dir",
    "get_optional_skills_dir",
    "get_skills_dir",
    "get_subprocess_home",
    "is_container",
    "is_termux",
    "is_wsl",
    "parse_reasoning_effort",
]
