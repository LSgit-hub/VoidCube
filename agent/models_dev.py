"""Simplified models.dev integration for VoidCube CLI.

This is a stripped-down version for the server management CLI,
providing basic model metadata and catalog functionality.

DEPRECATED: Model lists here duplicate ``VoidCube_cli/models.py`` and are
not kept in sync.  Once all callers migrate to ``models.py``, this file
should be deleted.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ModelCapabilities:
    """Model capabilities information."""
    context_length: int = 128000
    max_completion_tokens: int = 4096
    has_vision: bool = False
    has_function_calling: bool = True


@dataclass
class ModelInfo:
    """Basic model information."""
    id: str = ""
    name: str = ""
    provider: str = ""
    context_length: int = 128000
    max_completion_tokens: int = 4096


@dataclass
class ProviderInfo:
    """Basic provider information."""
    id: str = ""
    name: str = ""
    env: list = None
    api: str = ""
    doc: str = ""
    provider: str = ""
    models: list = None
    capabilities: dict = None
    auth_type: str = ""
    base_url: str = ""
    api_key_env_vars: list = None
    
    def __post_init__(self):
        if self.env is None:
            self.env = []
        if self.models is None:
            self.models = []
        if self.capabilities is None:
            self.capabilities = {}
        if self.api_key_env_vars is None:
            self.api_key_env_vars = []


# Simplified provider to models.dev mapping
PROVIDER_TO_MODELS_DEV: Dict[str, str] = {
    "openai": "openai",
    "openrouter": "openrouter",
    "gemini": "google",
    "deepseek": "deepseek-chat",
    "minimax": "minimax",
    "kimi-coding": "moonshotai",
    "qwen-oauth": "qwen",
    "alibaba": "alibaba",
    "xiaomi": "xiaomi",
    "custom": "custom",
    "local": "local",
}


# Simplified model catalog
_PROVIDER_MODELS: Dict[str, List[str]] = {
    "openrouter": [
        "openai/gpt-5",
        "openai/gpt-4o",
        "google/gemini-1.5-pro",
        "google/gemini-1.5-flash",
        "deepseek/deepseek-chat",
        "deepseek/deepseek-r1",
        "meta-llama/llama-3.3-70b-instruct",
        "meta-llama/llama-3.3-8b-instruct",
        "qwen/qwen3-coder",
        "qwen/qwen3.5-397b",
        "moonshotai/kimi-k2.5",
        "z-ai/glm-4.5",
        "nvidia/nemotron-4-340b",
        "qwen/qwen3.6-plus",
        "openai/gpt-5.4",
        "openai/gpt-5.4-mini",
        "xiaomi/mimo-v2-pro",
        "google/gemini-3-pro-image-preview",
        "google/gemini-3-flash-preview",
        "google/gemini-3.1-pro-preview",
        "google/gemini-3.1-flash-lite-preview",
        "qwen/qwen3.5-plus-02-15",
        "qwen/qwen3.5-35b-a3b",
        "stepfun/step-3.5-flash",
        "minimax/minimax-m2.7",
        "minimax/minimax-m2.5",
        "z-ai/glm-5.1",
        "z-ai/glm-5-turbo",
        "x-ai/grok-4.20",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "arcee-ai/trinity-large-preview:free",
        "arcee-ai/trinity-large-thinking",
        "openai/gpt-5.4-pro",
        "openai/gpt-5.4-nano",
    ],
    "gemini": [
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-pro",
        "gemini-3-pro",
        "gemini-3-flash",
    ],
    "deepseek": [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ],
    "minimax": [
        "minimax-m2.5",
        "minimax-m2.7",
    ],
    "kimi-coding": [
        "kimi-k2.5",
        "kimi-k2-thinking",
    ],
    "openai": [
        "gpt-5",
        "gpt-4o",
        "gpt-4",
        "gpt-3.5-turbo",
    ],
    "x-ai": [
        "grok-4",
        "grok-4-20",
        "grok-2",
    ],
    "meta-llama": [
        "llama-3.3-70b-instruct",
        "llama-3.3-8b-instruct",
        "llama-3.2-3b-instruct",
    ],
    "qwen": [
        "qwen3-coder",
        "qwen3-plus",
        "qwen3.5-plus-02-15",
    ],
    "z-ai": [
        "glm-4.5",
        "glm-5",
        "glm-5-turbo",
    ],
    "moonshotai": [
        "kimi-k2.5",
        "kimi-k2-thinking",
    ],
    "nvidia": [
        "nemotron-4-340b",
        "nemotron-3-super-120b-a12b",
    ],
}


def fetch_models_dev() -> Dict[str, dict]:
    """Fetch simplified model data."""
    return {}


def list_provider_models(provider: str) -> List[str]:
    """List models for a provider."""
    return _PROVIDER_MODELS.get(provider, [])


def get_model_info(provider: str, model_id: str) -> Optional[ModelInfo]:
    """Get model information."""
    return ModelInfo(
        id=model_id,
        name=model_id,
        provider=provider,
    )


def get_model_capabilities(provider: str, model_id: str) -> ModelCapabilities:
    """Get model capabilities."""
    return ModelCapabilities()


def get_provider_info(provider_id: str) -> Optional[ProviderInfo]:
    """Get provider information."""
    return ProviderInfo(
        id=provider_id,
        name=provider_id,
    )


def search_models_dev(query: str) -> List[str]:
    """Search models."""
    return []


def lookup_models_dev_context(provider: str, model: str) -> Optional[int]:
    """Lookup context length from models.dev."""
    # Fallback context lengths for common models
    context_lengths = {
        "gpt-5": 128000,
        "gpt-4o": 128000,
        "gpt-4": 128000,
        "gemini-1.5-pro": 1048576,
        "gemini-1.5-flash": 1048576,
        "gemini-2.0-flash": 1048576,
        "gemini-2.0-pro": 1048576,
        "deepseek-chat": 128000,
        "deepseek-r1": 128000,
        "llama-3.3-70b-instruct": 131072,
        "llama-3.3-8b-instruct": 131072,
        "qwen3-coder": 262144,
        "qwen3.5-397b": 131072,
        "kimi-k2.5": 262144,
        "glm-4.5": 202752,
    }
    
    # Try exact match
    if model in context_lengths:
        return context_lengths[model]
    
    # Try substring match
    for key, length in context_lengths.items():
        if key in model.lower() or model.lower() in key:
            return length
    
    return None
