"""Pure request preparation for OpenAI-compatible chat completions."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.integration_policy import require_active_integration
from agent.prompt_builder import DEVELOPER_ROLE_MODELS
from agent.tool_schema import normalize_tool_definitions


@dataclass(frozen=True, slots=True)
class ChatRequestConfig:
    model: str
    base_url: str = ""
    session_id: str = ""
    tools: tuple[dict[str, Any], ...] = ()
    max_tokens: int | None = None
    providers_allowed: tuple[str, ...] = ()
    providers_ignored: tuple[str, ...] = ()
    providers_order: tuple[str, ...] = ()
    provider_sort: str = ""
    provider_require_parameters: bool = False
    provider_data_collection: str = ""
    reasoning_config: dict[str, Any] | None = None
    include_reasoning: bool = True
    ollama_num_ctx: int | None = None
    request_overrides: dict[str, Any] = field(default_factory=dict)
    timeout: float = 1800.0


def is_direct_openai_url(base_url: str) -> bool:
    url = (base_url or "").lower()
    return "api.openai.com" in url and "openrouter" not in url


def max_tokens_param(base_url: str, value: int) -> dict[str, int]:
    if is_direct_openai_url(base_url):
        return {"max_completion_tokens": value}
    return {"max_tokens": value}


def _is_qwen_portal(base_url: str) -> bool:
    return "portal.qwen.ai" in (base_url or "").lower()


def _prepare_qwen_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = copy.deepcopy(messages)
    for message in prepared:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            normalized_parts = []
            for part in content:
                if isinstance(part, str):
                    normalized_parts.append({"type": "text", "text": part})
                elif isinstance(part, dict):
                    normalized_parts.append(part)
            if normalized_parts:
                message["content"] = normalized_parts

    for message in prepared:
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, list) and content and isinstance(content[-1], dict):
            content[-1]["cache_control"] = {"type": "ephemeral"}
        break
    return prepared


def supports_reasoning_extra_body(base_url: str, model: str) -> bool:
    base_url_lower = (base_url or "").lower()
    if "nousresearch" in base_url_lower or "ai-gateway.vercel.sh" in base_url_lower:
        return True
    if "models.github.ai" in base_url_lower or "api.githubcopilot.com" in base_url_lower:
        try:
            from VoidCube_cli.models import github_model_reasoning_efforts

            return bool(github_model_reasoning_efforts(model))
        except Exception:
            return False
    if "openrouter" not in base_url_lower or "api.mistral.ai" in base_url_lower:
        return False

    model_lower = (model or "").lower()
    reasoning_model_prefixes = (
        "deepseek/",
        "openai/",
        "x-ai/",
        "google/gemini-2",
        "qwen/qwen3",
    )
    return any(model_lower.startswith(prefix) for prefix in reasoning_model_prefixes)


def _github_reasoning_payload(
    model: str,
    reasoning_config: dict[str, Any] | None,
) -> dict[str, str] | None:
    try:
        from VoidCube_cli.models import github_model_reasoning_efforts
    except Exception:
        return None

    supported_efforts = github_model_reasoning_efforts(model)
    if not supported_efforts:
        return None
    if reasoning_config and reasoning_config.get("enabled") is False:
        return None

    requested = str((reasoning_config or {}).get("effort", "medium")).strip().lower()
    if requested == "xhigh" and "high" in supported_efforts:
        requested = "high"
    elif requested not in supported_efforts:
        if requested == "minimal" and "low" in supported_efforts:
            requested = "low"
        elif "medium" in supported_efforts:
            requested = "medium"
        else:
            requested = supported_efforts[0]
    return {"effort": requested}


def _provider_preferences(config: ChatRequestConfig) -> dict[str, Any]:
    preferences: dict[str, Any] = {}
    if config.providers_allowed:
        preferences["only"] = list(config.providers_allowed)
    if config.providers_ignored:
        preferences["ignore"] = list(config.providers_ignored)
    if config.providers_order:
        preferences["order"] = list(config.providers_order)
    if config.provider_sort:
        preferences["sort"] = config.provider_sort
    if config.provider_require_parameters:
        preferences["require_parameters"] = True
    if config.provider_data_collection:
        preferences["data_collection"] = config.provider_data_collection
    return preferences


def build_chat_completion_kwargs(
    config: ChatRequestConfig,
    messages: list[dict[str, Any]],
    *,
    include_tools: bool = True,
    include_request_overrides: bool = True,
) -> dict[str, Any]:
    """Build one chat-completions request without mutating caller messages."""
    qwen_portal = _is_qwen_portal(config.base_url)
    prepared_messages = _prepare_qwen_messages(messages) if qwen_portal else messages
    model_lower = (config.model or "").lower()
    if (
        prepared_messages
        and prepared_messages[0].get("role") == "system"
        and any(pattern in model_lower for pattern in DEVELOPER_ROLE_MODELS)
    ):
        prepared_messages = list(prepared_messages)
        prepared_messages[0] = {**prepared_messages[0], "role": "developer"}

    api_kwargs: dict[str, Any] = {
        "model": config.model,
        "messages": prepared_messages,
        "timeout": config.timeout,
    }
    if qwen_portal:
        api_kwargs["metadata"] = {
            "sessionId": config.session_id or "VoidCube",
            "promptId": str(uuid.uuid4()),
        }
    if include_tools and config.tools:
        api_kwargs["tools"] = normalize_tool_definitions(list(config.tools))

    if config.max_tokens is not None:
        api_kwargs.update(max_tokens_param(config.base_url, config.max_tokens))
    elif qwen_portal:
        api_kwargs.update(max_tokens_param(config.base_url, 65536))

    base_url_lower = (config.base_url or "").lower()
    is_openrouter = "openrouter" in base_url_lower
    is_github_models = (
        "models.github.ai" in base_url_lower
        or "api.githubcopilot.com" in base_url_lower
    )
    is_nous = "nousresearch" in base_url_lower
    extra_body: dict[str, Any] = {}
    preferences = _provider_preferences(config)
    if preferences and is_openrouter:
        extra_body["provider"] = preferences

    if config.include_reasoning and supports_reasoning_extra_body(
        config.base_url,
        config.model,
    ):
        if is_github_models:
            github_reasoning = _github_reasoning_payload(config.model, config.reasoning_config)
            if github_reasoning is not None:
                extra_body["reasoning"] = github_reasoning
        elif config.reasoning_config is not None:
            reasoning = dict(config.reasoning_config)
            if not (is_nous and reasoning.get("enabled") is False):
                extra_body["reasoning"] = reasoning
        else:
            extra_body["reasoning"] = {"enabled": True, "effort": "medium"}

    if is_nous:
        extra_body["tags"] = ["product=VoidCube-agent"]
    if config.ollama_num_ctx:
        extra_body["options"] = {"num_ctx": config.ollama_num_ctx}
    if qwen_portal:
        extra_body["vl_high_resolution_images"] = True
    if extra_body:
        api_kwargs["extra_body"] = extra_body

    if "x.ai" in base_url_lower and config.session_id:
        api_kwargs["extra_headers"] = {"x-grok-conv-id": config.session_id}
    if include_request_overrides and config.request_overrides:
        api_kwargs.update(config.request_overrides)
    require_active_integration(
        config.base_url,
        api_kwargs.get("model"),
        *config.providers_allowed,
        *config.providers_ignored,
        *config.providers_order,
    )
    return api_kwargs
