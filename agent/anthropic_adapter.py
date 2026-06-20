"""Minimal adapter for third-party providers using Anthropic Messages API format.

This module provides compatibility functions for providers like MiniMax that
use the Anthropic Messages API format. It does NOT include any Anthropic-
specific authentication or credential handling.
"""

import os
from typing import Any, Dict, Optional


_ANTHROPIC_SDK = None
try:
    from anthropic import Anthropic
    _ANTHROPIC_SDK = Anthropic
except ImportError:
    pass


def _is_oauth_token(token: str) -> bool:
    """Check if a token appears to be an OAuth token."""
    if not token:
        return False
    return token.startswith("sk-ant-") and "oa-" in token


def build_anthropic_client(api_key: str, base_url: Optional[str] = None) -> Any:
    """Build an Anthropic-compatible client for third-party providers."""
    if _ANTHROPIC_SDK is None:
        raise ImportError("anthropic SDK not installed")

    kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url.rstrip("/")

    return _ANTHROPIC_SDK(**kwargs)


def build_anthropic_kwargs(
    model: str,
    messages: list,
    tools: Optional[list] = None,
    max_tokens: int = 8192,
    temperature: float = 0.3,
    reasoning_config: Optional[Dict[str, Any]] = None,
    preserve_dots: bool = False,
    **kwargs
) -> Dict[str, Any]:
    """Build kwargs for Anthropic Messages API call."""
    api_kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    if tools:
        api_kwargs["tools"] = tools

    if reasoning_config:
        api_kwargs["thinking"] = reasoning_config

    return api_kwargs


def normalize_anthropic_response(response: Any) -> tuple:
    """Normalize an Anthropic response to OpenAI-compatible format."""
    try:
        message = response.content[0].text
        finish_reason = response.stop_reason
        return message, finish_reason
    except (IndexError, AttributeError):
        return str(response), "stop"


def _get_anthropic_max_output(model: str) -> int:
    """Get the max output tokens for a model."""
    return 8192
