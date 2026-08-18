"""Minimal provider metadata compatibility layer for VoidCube CLI."""

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


def fetch_models_dev() -> Dict[str, dict]:
    """Fetch simplified model data."""
    return {}


def list_provider_models(provider: str) -> List[str]:
    """List models currently returned by the provider API."""
    try:
        from .model_catalog import provider_model_ids

        return provider_model_ids(provider)
    except Exception:
        return []


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
