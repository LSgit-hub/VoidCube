"""Pure request preparation for OpenAI-compatible chat completions."""

from __future__ import annotations

import copy
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ...domain.contracts.integration_policy import require_active_integration
from .multimodal import build_user_content_with_attachments
from .prompt_policy import DEVELOPER_ROLE_MODELS
from .tool_schema import normalize_tool_definitions

logger = logging.getLogger(__name__)
VALID_CHAT_ROLES = frozenset({"system", "user", "assistant", "tool", "function", "developer"})
INTERNAL_CHAT_MESSAGE_FIELDS = frozenset({"reasoning", "finish_reason"})


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
    return {"max_completion_tokens" if is_direct_openai_url(base_url) else "max_tokens": value}


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
    if "openrouter" not in base_url_lower or "api.mistral.ai" in base_url_lower:
        return False
    model_lower = (model or "").lower()
    return any(
        model_lower.startswith(prefix)
        for prefix in ("deepseek/", "openai/", "x-ai/", "google/gemini-2", "qwen/qwen3")
    )


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


def _tool_call_id(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or "")
    return str(getattr(tool_call, "id", "") or "")


def sanitize_chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair role and tool-call/result invariants before an API request."""
    filtered: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role not in VALID_CHAT_ROLES:
            logger.debug("Pre-call sanitizer: dropping message with invalid role %r", role)
            continue
        filtered.append(message)

    surviving_call_ids = {
        call_id
        for message in filtered
        if message.get("role") == "assistant"
        for tool_call in (message.get("tool_calls") or [])
        if (call_id := _tool_call_id(tool_call))
    }
    result_call_ids = {
        str(message.get("tool_call_id"))
        for message in filtered
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    orphaned_results = result_call_ids - surviving_call_ids
    if orphaned_results:
        filtered = [
            message
            for message in filtered
            if not (
                message.get("role") == "tool"
                and str(message.get("tool_call_id")) in orphaned_results
            )
        ]

    missing_results = surviving_call_ids - result_call_ids
    if not missing_results:
        return filtered
    patched: list[dict[str, Any]] = []
    for message in filtered:
        patched.append(message)
        if message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            call_id = _tool_call_id(tool_call)
            if call_id in missing_results:
                patched.append(
                    {
                        "role": "tool",
                        "content": "[Result unavailable - see context summary above]",
                        "tool_call_id": call_id,
                    }
                )
    return patched


def prepare_chat_messages(
    messages: Sequence[dict[str, Any]],
    *,
    system_prompt: str = "",
    ephemeral_system_prompt: str = "",
    prefill_messages: Sequence[dict[str, Any]] = (),
    user_message_index: int | None = None,
    user_contexts: Sequence[str] = (),
    native_input_modalities: Sequence[str] = (),
    native_image_input: bool | None = None,
) -> list[dict[str, Any]]:
    """Build API-only messages without mutating persisted history."""
    prepared: list[dict[str, Any]] = []
    effective_native_modalities = set(native_input_modalities)
    if native_image_input is True:
        effective_native_modalities.add("image")
    contexts = [context.strip() for context in user_contexts if context.strip()]
    for index, message in enumerate(messages):
        api_message = dict(message)
        attachments = api_message.pop("attachments", ())
        if index == user_message_index and message.get("role") == "user" and contexts:
            base_content = api_message.get("content", "")
            if isinstance(base_content, str):
                api_message["content"] = base_content.strip() + "\n\n" + "\n\n".join(contexts)
        if (
            effective_native_modalities
            and message.get("role") == "user"
            and isinstance(attachments, Sequence)
            and not isinstance(attachments, (str, bytes))
            and attachments
        ):
            content = api_message.get("content", "")
            if not isinstance(content, str):
                raise ValueError(
                    "Native attachment input requires string user message content"
                )
            api_message["content"] = build_user_content_with_attachments(
                content.strip(),
                [
                    attachment
                    for attachment in attachments
                    if isinstance(attachment, dict)
                ],
                native_modalities=effective_native_modalities,
            )
        if message.get("role") == "assistant" and message.get("reasoning"):
            api_message["reasoning_content"] = message["reasoning"]
        for field_name in tuple(api_message):
            if field_name in INTERNAL_CHAT_MESSAGE_FIELDS or field_name == "action_refs" or field_name.startswith("_"):
                api_message.pop(field_name, None)
        prepared.append(api_message)

    effective_system = system_prompt or ""
    if ephemeral_system_prompt:
        effective_system = (effective_system + "\n\n" + ephemeral_system_prompt).strip()
    if effective_system:
        prepared.insert(0, {"role": "system", "content": effective_system})
    if prefill_messages:
        system_offset = 1 if effective_system else 0
        for index, prefill in enumerate(prefill_messages):
            prepared.insert(system_offset + index, dict(prefill))

    prepared = sanitize_chat_messages(prepared)
    for message in prepared:
        if isinstance(message.get("content"), str):
            message["content"] = message["content"].strip()
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            continue
        normalized_calls: list[Any] = []
        for tool_call in tool_calls:
            if isinstance(tool_call, dict) and "function" in tool_call:
                try:
                    arguments = json.loads(tool_call["function"]["arguments"])
                    tool_call = {
                        **tool_call,
                        "function": {
                            **tool_call["function"],
                            "arguments": json.dumps(arguments, separators=(",", ":"), sort_keys=True),
                        },
                    }
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    pass
            normalized_calls.append(tool_call)
        message["tool_calls"] = normalized_calls
    return prepared


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
    if prepared_messages and prepared_messages[0].get("role") == "system" and any(
        pattern in model_lower for pattern in DEVELOPER_ROLE_MODELS
    ):
        prepared_messages = list(prepared_messages)
        prepared_messages[0] = {**prepared_messages[0], "role": "developer"}

    api_kwargs: dict[str, Any] = {
        "model": config.model,
        "messages": prepared_messages,
        "timeout": config.timeout,
    }
    if qwen_portal:
        api_kwargs["metadata"] = {"sessionId": config.session_id or "VoidCube", "promptId": str(uuid.uuid4())}
    if include_tools and config.tools:
        api_kwargs["tools"] = normalize_tool_definitions(list(config.tools))
    if config.max_tokens is not None:
        api_kwargs.update(max_tokens_param(config.base_url, config.max_tokens))
    elif qwen_portal:
        api_kwargs.update(max_tokens_param(config.base_url, 65536))

    base_url_lower = (config.base_url or "").lower()
    is_openrouter = "openrouter" in base_url_lower
    is_nous = "nousresearch" in base_url_lower
    extra_body: dict[str, Any] = {}
    preferences = _provider_preferences(config)
    if preferences and is_openrouter:
        extra_body["provider"] = preferences
    if config.include_reasoning and supports_reasoning_extra_body(config.base_url, config.model):
        if config.reasoning_config is not None:
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


__all__ = [
    "ChatRequestConfig",
    "INTERNAL_CHAT_MESSAGE_FIELDS",
    "VALID_CHAT_ROLES",
    "build_chat_completion_kwargs",
    "is_direct_openai_url",
    "max_tokens_param",
    "prepare_chat_messages",
    "sanitize_chat_messages",
    "supports_reasoning_extra_body",
]
