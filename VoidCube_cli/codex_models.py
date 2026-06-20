"""Simplified Codex models module for VoidCube CLI."""

from typing import List, Optional


DEFAULT_CODEX_MODELS: List[str] = []


def _add_forward_compat_models(models: List[str]) -> List[str]:
    """Add forward compatibility models."""
    return models


def get_codex_model_ids(access_token: str = "") -> List[str]:
    """Get Codex model IDs."""
    return []
