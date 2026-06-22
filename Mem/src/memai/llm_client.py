from __future__ import annotations

from dataclasses import dataclass
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib import request

from .llm_protocol import build_protocol_payload, unwrap_protocol_response
from .prompt_registry import PromptRegistry
from .schema import TranscriptTurn


Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]
SYSTEM_PROMPT_STYLES = frozenset({"system", "developer", "inline_user"})
RESPONSE_FORMAT_STYLES = frozenset({"json_object", "json_object_string", "none"})
RESPONSE_CONTENT_STYLES = frozenset(
    {"auto", "openai_message", "choices_text", "output_text"}
)

# ── Token usage tracking (module-level, shared across LLMClient instances) ──
# Exposed so the Supervisor can report live context usage via /ui/state.
_memory_token_usage: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "request_count": 0,
    "context_length": 65536,  # updated by _set_memory_context_length when LLMClient inits
}
_memory_token_lock = threading.Lock()

# ── Context-length lookup (best-effort; model names change over time) ──
_MODEL_CONTEXT_LENGTHS: dict[str, int] = {
    # DeepSeek family
    "deepseek-chat": 65536,
    "deepseek-v4": 65536,
    "deepseek-v4-flash": 65536,
    "deepseek-reasoner": 65536,
    # OpenAI family
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
    # Anthropic family (via OpenAI-compatible proxy)
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3.5-sonnet": 200000,
    # Open-source / local
    "llama-3": 8192,
    "llama-3.1": 131072,
    "mixtral": 32768,
    "qwen": 32768,
}
_DEFAULT_CONTEXT_LENGTH = 65536


def _set_memory_context_length(model_name: str) -> None:
    """Update the global context-length estimate based on the model name.

    Called by ``OpenAICompatibleLLMClient.__init__`` so the accumulator
    reflects the actual model's context window.  Falls back to 65536 for
    unknown models.
    """
    with _memory_token_lock:
        _memory_token_usage["context_length"] = _MODEL_CONTEXT_LENGTHS.get(
            model_name, _DEFAULT_CONTEXT_LENGTH
        )


def get_memory_token_usage() -> dict[str, int]:
    """Return a snapshot of cumulative memory LLM token usage (thread-safe)."""
    with _memory_token_lock:
        return dict(_memory_token_usage)


def _accumulate_memory_usage(usage: dict[str, Any]) -> None:
    """Merge an API ``usage`` object into the module-level accumulator.

    Thread-safe: the accumulator is protected by a module-level lock.
    """
    if not isinstance(usage, dict):
        return
    with _memory_token_lock:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                _memory_token_usage[key] += int(value)
        _memory_token_usage["request_count"] += 1


@dataclass(frozen=True, slots=True)
class LLMProviderCapabilities:
    profile_name: str
    chat_completions_path: str = "/chat/completions"
    supports_response_format_json_object: bool = True
    system_prompt_style: str = "system"
    response_format_style: str = "json_object"
    response_content_style: str = "auto"

    def build_url(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}{self.chat_completions_path}"

    def build_messages(
        self,
        *,
        system_prompt: str,
        user_content: str,
    ) -> list[dict[str, str]]:
        if self.system_prompt_style == "inline_user":
            return [
                {
                    "role": "user",
                    "content": (
                        "Follow these system instructions while answering.\n"
                        f"{system_prompt}\n\n"
                        "User payload:\n"
                        f"{user_content}"
                    ),
                }
            ]
        system_role = "developer" if self.system_prompt_style == "developer" else "system"
        return [
            {"role": system_role, "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    def apply_request_format(self, payload: dict[str, Any]) -> None:
        if (
            not self.supports_response_format_json_object
            or self.response_format_style == "none"
        ):
            return
        if self.response_format_style == "json_object_string":
            payload["response_format"] = "json_object"
            return
        payload["response_format"] = {"type": "json_object"}


BUILTIN_PROVIDER_CAPABILITIES = {
    "openai": LLMProviderCapabilities("openai"),
    "generic": LLMProviderCapabilities("generic"),
    "legacy-compatible": LLMProviderCapabilities(
        "legacy-compatible",
        supports_response_format_json_object=False,
        response_format_style="none",
    ),
    "developer-role": LLMProviderCapabilities(
        "developer-role",
        system_prompt_style="developer",
    ),
    "user-only": LLMProviderCapabilities(
        "user-only",
        system_prompt_style="inline_user",
    ),
    "text-choice": LLMProviderCapabilities(
        "text-choice",
        response_content_style="choices_text",
    ),
    "output-text": LLMProviderCapabilities(
        "output-text",
        response_content_style="output_text",
    ),
}


def resolve_provider_capabilities(
    profile_name: str | None = None,
    *,
    profile_path: str | Path | None = None,
    chat_completions_path: str | None = None,
    system_prompt_style: str | None = None,
    response_format_style: str | None = None,
    response_content_style: str | None = None,
) -> LLMProviderCapabilities:
    if profile_path is not None:
        resolved = load_provider_capabilities_profile(profile_path, profile_name)
    else:
        resolved = BUILTIN_PROVIDER_CAPABILITIES.get(
            profile_name or "openai",
            LLMProviderCapabilities(profile_name or "custom"),
        )
    normalized_path = resolved.chat_completions_path
    if chat_completions_path is not None:
        normalized_path = _normalize_chat_completions_path(chat_completions_path)
    normalized_response_format_style = (
        response_format_style or resolved.response_format_style
    )
    normalized_system_prompt_style = (
        system_prompt_style or resolved.system_prompt_style
    )
    normalized_response_content_style = (
        response_content_style or resolved.response_content_style
    )
    _validate_capability_style(
        "system_prompt_style",
        normalized_system_prompt_style,
        SYSTEM_PROMPT_STYLES,
    )
    _validate_capability_style(
        "response_format_style",
        normalized_response_format_style,
        RESPONSE_FORMAT_STYLES,
    )
    _validate_capability_style(
        "response_content_style",
        normalized_response_content_style,
        RESPONSE_CONTENT_STYLES,
    )
    return LLMProviderCapabilities(
        profile_name=resolved.profile_name,
        chat_completions_path=normalized_path,
        supports_response_format_json_object=(
            normalized_response_format_style != "none"
        ),
        system_prompt_style=normalized_system_prompt_style,
        response_format_style=normalized_response_format_style,
        response_content_style=normalized_response_content_style,
    )


def load_provider_capabilities_profile(
    profile_path: str | Path,
    profile_name: str | None = None,
) -> LLMProviderCapabilities:
    payload = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    spec = _resolve_profile_spec_from_payload(payload, profile_name)
    return _build_capabilities_from_spec(spec, profile_name=profile_name)


def _resolve_profile_spec_from_payload(
    payload: Any,
    profile_name: str | None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Provider capability profile file must contain a JSON object")
    if "profiles" in payload:
        profiles = payload["profiles"]
        if not isinstance(profiles, dict):
            raise ValueError("Provider capability profiles must be a JSON object")
        resolved_name = profile_name
        if resolved_name is None:
            if len(profiles) != 1:
                raise ValueError(
                    "Provider profile name is required when the file defines multiple profiles"
                )
            resolved_name = next(iter(profiles))
        spec = profiles.get(resolved_name)
        if not isinstance(spec, dict):
            raise ValueError(f"Unknown provider capability profile: {resolved_name}")
        spec = dict(spec)
        spec.setdefault("profile_name", resolved_name)
        return spec
    if profile_name is not None and "profile_name" not in payload:
        payload = dict(payload)
        payload["profile_name"] = profile_name
    return payload


def _build_capabilities_from_spec(
    spec: dict[str, Any],
    *,
    profile_name: str | None = None,
) -> LLMProviderCapabilities:
    base_profile = spec.get("extends") or "openai"
    base = BUILTIN_PROVIDER_CAPABILITIES.get(
        base_profile,
        LLMProviderCapabilities(str(base_profile)),
    )
    resolved_name = str(spec.get("profile_name") or profile_name or base.profile_name)
    chat_path = spec.get("chat_completions_path", base.chat_completions_path)
    system_prompt_style = spec.get("system_prompt_style", base.system_prompt_style)
    response_format_style = spec.get("response_format_style", base.response_format_style)
    response_content_style = spec.get(
        "response_content_style", base.response_content_style
    )
    _validate_capability_style(
        "system_prompt_style",
        system_prompt_style,
        SYSTEM_PROMPT_STYLES,
    )
    _validate_capability_style(
        "response_format_style",
        response_format_style,
        RESPONSE_FORMAT_STYLES,
    )
    _validate_capability_style(
        "response_content_style",
        response_content_style,
        RESPONSE_CONTENT_STYLES,
    )
    return LLMProviderCapabilities(
        profile_name=resolved_name,
        chat_completions_path=_normalize_chat_completions_path(str(chat_path)),
        supports_response_format_json_object=response_format_style != "none",
        system_prompt_style=system_prompt_style,
        response_format_style=response_format_style,
        response_content_style=response_content_style,
    )


def _normalize_chat_completions_path(path_value: str) -> str:
    normalized_path = path_value.strip()
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    return normalized_path


def _validate_capability_style(
    name: str,
    value: str,
    valid_values: frozenset[str],
) -> None:
    if value not in valid_values:
        supported = ", ".join(sorted(valid_values))
        raise ValueError(f"Unsupported {name}: {value}. Expected one of: {supported}")


def _default_transport(
    url: str, headers: dict[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(url, data=body, headers=headers, method="POST")
    with request.urlopen(http_request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    # Accumulate token usage so the Memory Service can report it to the CLI
    usage = result.get("usage") if isinstance(result, dict) else None
    if usage is not None:
        _accumulate_memory_usage(usage)
    return result


def _extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Model response does not contain a JSON object")
    return json.loads(text[start : end + 1])


def _extract_openai_message_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Model response does not contain choices")
    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "\n".join(parts)
    raise ValueError("Model response does not contain textual message content")


def _extract_choice_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Model response does not contain choices")
    text = choices[0].get("text")
    if isinstance(text, str) and text.strip():
        return text
    raise ValueError("Model response does not contain textual choice content")


def _extract_output_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    output = response.get("output")
    if not isinstance(output, list):
        raise ValueError("Model response does not contain output text")
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
    if parts:
        return "\n".join(parts)
    raise ValueError("Model response does not contain output text")


def _extract_message_content(
    response: dict[str, Any],
    provider_capabilities: LLMProviderCapabilities,
) -> str:
    ordered_extractors: list[Callable[[dict[str, Any]], str]]
    if provider_capabilities.response_content_style == "choices_text":
        ordered_extractors = [
            _extract_choice_text,
            _extract_openai_message_content,
            _extract_output_text,
        ]
    elif provider_capabilities.response_content_style == "output_text":
        ordered_extractors = [
            _extract_output_text,
            _extract_openai_message_content,
            _extract_choice_text,
        ]
    else:
        ordered_extractors = [
            _extract_openai_message_content,
            _extract_choice_text,
            _extract_output_text,
        ]
    for extractor in ordered_extractors:
        try:
            return extractor(response)
        except ValueError:
            continue
    raise ValueError("Model response does not contain supported textual content")


class OpenAICompatibleLLMClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        provider_capabilities: LLMProviderCapabilities | None = None,
        system_prompt: str | None = None,
        prompt_registry: PromptRegistry | None = None,
        temperature: float = 0.1,
        transport: Transport | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.provider_capabilities = provider_capabilities or resolve_provider_capabilities(
            "openai"
        )
        self.system_prompt = system_prompt or self.default_system_prompt()
        self.prompt_registry = prompt_registry or PromptRegistry.default()
        self.temperature = temperature
        self.transport = transport or _default_transport
        # Update the global context-length estimate so the accumulator
        # (and therefore the UI percentage) reflects this model's window.
        _set_memory_context_length(model)

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        provider_profile: str | None = None,
        provider_profile_path: str | Path | None = None,
        chat_completions_path: str | None = None,
        system_prompt_style: str | None = None,
        response_format_style: str | None = None,
        response_content_style: str | None = None,
        system_prompt: str | None = None,
        prompt_registry: PromptRegistry | None = None,
        temperature: float = 0.1,
        transport: Transport | None = None,
    ) -> "OpenAICompatibleLLMClient":
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(f"Missing API key in environment variable: {api_key_env}")
        resolved_model = model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        resolved_base_url = (
            base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        )
        resolved_profile = provider_profile or os.environ.get(
            "OPENAI_PROVIDER_PROFILE"
        )
        resolved_profile_path = provider_profile_path or os.environ.get(
            "OPENAI_PROVIDER_PROFILE_FILE"
        )
        resolved_chat_path = chat_completions_path or os.environ.get(
            "OPENAI_CHAT_COMPLETIONS_PATH"
        )
        resolved_system_prompt_style = system_prompt_style or os.environ.get(
            "OPENAI_SYSTEM_PROMPT_STYLE"
        )
        resolved_response_format_style = response_format_style or os.environ.get(
            "OPENAI_RESPONSE_FORMAT_STYLE"
        )
        resolved_response_content_style = response_content_style or os.environ.get(
            "OPENAI_RESPONSE_CONTENT_STYLE"
        )
        return cls(
            model=resolved_model,
            api_key=api_key,
            base_url=resolved_base_url,
            provider_capabilities=resolve_provider_capabilities(
                resolved_profile,
                profile_path=resolved_profile_path,
                chat_completions_path=resolved_chat_path,
                system_prompt_style=resolved_system_prompt_style,
                response_format_style=resolved_response_format_style,
                response_content_style=resolved_response_content_style,
            ),
            system_prompt=system_prompt,
            prompt_registry=prompt_registry,
            temperature=temperature,
            transport=transport,
        )

    @staticmethod
    def default_system_prompt() -> str:
        return (
            "You extract durable timeline-worthy memory events from multilingual transcripts. "
            'Return strict JSON with the shape {"events": [...]} and no extra prose. '
            "Each event must include title, summary, event_kind, impact_scope, topics, entities, source_turns, time_hint, importance, confidence, main_or_side, novelty."
        )

    @staticmethod
    def default_json_prompt(task: str, output_schema: str) -> str:
        return (
            f"You are a chronicle scholar assistant. Complete this task: {task}. "
            f"Return strict JSON only. Required output schema: {output_schema}."
        )

    @staticmethod
    def load_system_prompt(prompt_path: str | Path | None) -> str | None:
        if prompt_path is None:
            return None
        return Path(prompt_path).read_text(encoding="utf-8")

    def extract_events(self, turns: Sequence[TranscriptTurn]) -> list[dict[str, Any]]:
        parsed = self.safe_complete_json(
            task="extractor.events",
            prompt_key="extractor.events",
            fallback_prompt=self.system_prompt,
            user_payload={
                "instruction": "Extract only durable, time-anchored changes worth long-term memory.",
                "turns": [
                    {
                        "turn_id": turn.turn_id,
                        "speaker": turn.speaker,
                        "timestamp": turn.timestamp.isoformat(),
                        "text": turn.text,
                    }
                    for turn in turns
                ],
            },
        ) or {"events": []}
        events = parsed.get("events", [])
        if not isinstance(events, list):
            return []
        return events

    def complete_json(
        self,
        *,
        system_prompt: str | None = None,
        prompt_key: str | None = None,
        fallback_prompt: str | None = None,
        user_payload: dict[str, Any],
        task: str | None = None,
        response_schema: str | None = None,
    ) -> dict[str, Any]:
        resolved_prompt = system_prompt
        if prompt_key is not None:
            resolved_prompt = self.prompt_registry.get(
                prompt_key,
                fallback=fallback_prompt,
            )
        if resolved_prompt is None:
            raise ValueError("A system prompt or prompt key must be provided")
        user_content = json.dumps(
            build_protocol_payload(
                task=task,
                user_payload=user_payload,
                response_schema=response_schema,
            ),
            ensure_ascii=False,
        )
        messages = self.provider_capabilities.build_messages(
            system_prompt=resolved_prompt,
            user_content=user_content,
        )
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
        }
        self.provider_capabilities.apply_request_format(payload)
        response = self.transport(
            self.provider_capabilities.build_url(self.base_url),
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload,
        )
        content = _extract_message_content(response, self.provider_capabilities)
        return unwrap_protocol_response(_extract_json_object(content), task=task)

    def safe_complete_json(
        self,
        *,
        system_prompt: str | None = None,
        prompt_key: str | None = None,
        fallback_prompt: str | None = None,
        user_payload: dict[str, Any],
        task: str | None = None,
        response_schema: str | None = None,
    ) -> dict[str, Any]:
        try:
            return self.complete_json(
                system_prompt=system_prompt,
                prompt_key=prompt_key,
                fallback_prompt=fallback_prompt,
                user_payload=user_payload,
                task=task,
                response_schema=response_schema,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {}
