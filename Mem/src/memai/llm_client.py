from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import os
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib import request

from .llm_protocol import build_protocol_payload, unwrap_protocol_response
from .prompt_registry import PromptRegistry
from .schema import TranscriptTurn


Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]
StreamTransport = Callable[
    [str, dict[str, str], dict[str, Any]], Iterable[dict[str, Any]]
]
SYSTEM_PROMPT_STYLES = frozenset({"system", "developer", "inline_user"})
RESPONSE_FORMAT_STYLES = frozenset({"json_object", "json_object_string", "none"})
RESPONSE_CONTENT_STYLES = frozenset(
    {"auto", "openai_message", "choices_text", "output_text"}
)

# ── Token usage tracking (module-level, shared across LLMClient instances) ──
# Exposed so the Supervisor can report live context usage via /ui/state.
_memory_token_lock = threading.Lock()

# ── Context-length lookup ──
# Priority: VOIDCUBE_MEMORY_CONTEXT_LENGTH env var > API query > static list > default.
#
# The static list is a last-resort fallback.  Models change too often to
# maintain accurately — we try to fetch the real value from the API when
# a client is created so the user doesn't have to edit code every time a
# new model ships with a different context window.

# (base_url, model) → context_length  (immutable after write)
_context_length_cache: dict[tuple[str, str], int] = {}
_context_length_cache_lock = threading.Lock()

# Env-var override: set VOIDCUBE_MEMORY_CONTEXT_LENGTH=1048576 to bypass
# all auto-detection and static lookups.
_ENV_CONTEXT_LENGTH: int | None = None
_env_raw = os.getenv("VOIDCUBE_MEMORY_CONTEXT_LENGTH", "").strip()
if _env_raw:
    try:
        _ENV_CONTEXT_LENGTH = int(_env_raw)
    except ValueError:
        pass  # invalid → treated as unset

# ── Static fallback (best-effort; models change over time) ──
_MODEL_CONTEXT_LENGTHS: dict[str, int] = {
    # DeepSeek family
    "deepseek-chat": 131072,
    "deepseek-v4": 1048576,          # 1M
    "deepseek-v4-pro": 1048576,      # 1M
    "deepseek-v4-flash": 1048576,    # 1M
    "deepseek-reasoner": 131072,
    # Agnes AI family
    "agnes-2.0-flash": 131072,
    "agnes-2.0-pro": 131072,
    # OpenAI family
    "gpt-4o": 128000,
    "gpt-4o-mini": 200000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
    # Open-source / local
    "llama-3": 8192,
    "llama-3.1": 131072,
    "llama-3.2": 131072,
    "llama-3.3": 131072,
    "mixtral": 32768,
    "qwen": 32768,
    "qwen-2.5": 131072,
}
_DEFAULT_CONTEXT_LENGTH = 131072

_memory_token_usage: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "request_count": 0,
    "context_length": _DEFAULT_CONTEXT_LENGTH,  # updated by _set_memory_context_length when LLMClient inits
    "last_prompt_tokens": 0,  # prompt_tokens of the most recent call (for per-request context utilisation)
}


def _try_fetch_context_length_from_api(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 5.0,
) -> int | None:
    """Query the provider's /models/{model} endpoint for the real context length.

    Returns None when the provider doesn't expose context-length metadata
    or the request fails (timeout, auth error, etc.).
    """
    import socket
    url = f"{base_url.rstrip('/')}/models/{model}"
    req = request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    # Try common top-level field names
    for field in (
        "context_length", "context_window", "max_context_length",
        "max_input_tokens", "max_tokens",
    ):
        value = data.get(field)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    # Try nested containers (some APIs wrap model info)
    for container_key in ("data", "model", "model_info", "metadata"):
        container = data.get(container_key)
        if not isinstance(container, dict):
            continue
        for field in (
            "context_length", "context_window", "max_context_length",
            "max_input_tokens",
        ):
            value = container.get(field)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
    return None


def _resolve_model_context_length(
    *,
    model_name: str,
    base_url: str = "",
    api_key: str = "",
) -> int:
    """Resolve context length in priority order.

    Env var > cached API fetch > live API query > static list > DEFAULT.
    """
    # 1. Env-var override (process-wide, trumps everything)
    if _ENV_CONTEXT_LENGTH is not None:
        return _ENV_CONTEXT_LENGTH

    # 2. Cache hit (keyed by base_url + model to distinguish providers)
    cache_key = (base_url.rstrip("/"), model_name)
    with _context_length_cache_lock:
        cached = _context_length_cache.get(cache_key)
    if cached is not None:
        return cached

    # 3. Live API query
    if base_url and api_key:
        fetched = _try_fetch_context_length_from_api(
            base_url=base_url, api_key=api_key, model=model_name,
        )
        if fetched is not None:
            with _context_length_cache_lock:
                _context_length_cache[cache_key] = fetched
            return fetched

    # 4. Static list fallback
    static = _MODEL_CONTEXT_LENGTHS.get(model_name)
    if static is not None:
        return static

    # 5. Default
    return _DEFAULT_CONTEXT_LENGTH


def _set_memory_context_length(
    model_name: str,
    *,
    base_url: str = "",
    api_key: str = "",
) -> None:
    """Update the global context-length estimate.

    Called by ``OpenAICompatibleLLMClient.__init__``.
    """
    resolved = _resolve_model_context_length(
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
    )
    with _memory_token_lock:
        _memory_token_usage["context_length"] = resolved


def get_memory_context_length() -> int:
    """Return the current memory LLM's context length (thread-safe)."""
    with _memory_token_lock:
        return _memory_token_usage.get("context_length", _DEFAULT_CONTEXT_LENGTH)


def get_memory_context_max_chars(
    *,
    context_percent: float = 0.50,
    chars_per_token: float = 2.5,
) -> int:
    """Return the recommended max prompt chars for the current memory LLM.

    Derives a character budget from the model's context window so the prompt
    packet doesn't overflow.  The default 50% leaves ample headroom for the
    system prompt, response, and overhead.

    ``chars_per_token`` defaults to 2.5 to be conservative for mixed
    Chinese/English/code content (pure ASCII ≈ 4, Chinese ≈ 1.5–2).
    """
    context_tokens = get_memory_context_length()
    budget_tokens = int(context_tokens * context_percent)
    max_chars = int(budget_tokens * chars_per_token)
    # Floor: never drop below 8000 chars — smaller budgets break cognitive
    # packet structure and would make API-B useless on tiny models anyway.
    return max(8000, max_chars)


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
        # Track the last call's prompt_tokens for per-request context utilisation display.
        # The cumulative total_tokens / context_length is meaningless — it's an odometer
        # divided by a tank size.  See ui_state_orchestration.load_ui_memory_token_usage().
        prompt = usage.get("prompt_tokens")
        if isinstance(prompt, (int, float)):
            _memory_token_usage["last_prompt_tokens"] = int(prompt)


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
        user_content: Any,
    ) -> list[dict[str, Any]]:
        if self.system_prompt_style == "inline_user":
            if isinstance(user_content, list):
                content = [
                    {
                        "type": "text",
                        "text": (
                            "Follow these system instructions while answering.\n"
                            f"{system_prompt}\n\nUser payload:\n"
                        ),
                    },
                    *user_content,
                ]
                return [{"role": "user", "content": content}]
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


def _iter_sse_json_events(lines: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            if not data_lines:
                continue
            data = "\n".join(data_lines).strip()
            data_lines = []
            if not data or data == "[DONE]":
                if data == "[DONE]":
                    break
                continue
            yield json.loads(data)
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        data = "\n".join(data_lines).strip()
        if data and data != "[DONE]":
            yield json.loads(data)


def _default_stream_transport(
    url: str, headers: dict[str, str], payload: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    stream_payload = dict(payload)
    body = json.dumps(stream_payload).encode("utf-8")
    stream_headers = dict(headers)
    stream_headers.setdefault("Accept", "text/event-stream")
    http_request = request.Request(
        url,
        data=body,
        headers=stream_headers,
        method="POST",
    )
    with request.urlopen(http_request, timeout=60) as response:
        yield from _iter_sse_json_events(response)


def _text_from_content_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _extract_stream_chunk_text(chunk: dict[str, Any]) -> tuple[str, str]:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", ""
    choice = choices[0]
    if not isinstance(choice, dict):
        return "", ""
    delta = choice.get("delta")
    if isinstance(delta, dict):
        content = _text_from_content_value(delta.get("content"))
        reasoning = _text_from_content_value(
            delta.get("reasoning_content") or delta.get("reasoning")
        )
        return content, reasoning
    return _text_from_content_value(choice.get("text")), ""


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


def _extract_response_audio(response: dict[str, Any]) -> dict[str, Any] | None:
    """Return a validated native audio block from an OpenAI-style response."""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    audio = message.get("audio")
    if not isinstance(audio, dict):
        return None
    data = audio.get("data")
    if not isinstance(data, str) or not data.strip():
        return None
    try:
        base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        return None
    audio_format = str(audio.get("format") or "wav").strip().lower()
    if audio_format not in {"wav", "mp3", "flac", "ogg", "m4a"}:
        audio_format = "wav"
    return {"data": data, "format": audio_format}


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
        stream_transport: StreamTransport | None = None,
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
        self.stream_transport = stream_transport or _default_stream_transport
        # Update the global context-length estimate.  Pass base_url and
        # api_key so we can try to fetch the real value from the API
        # instead of relying on the static fallback list.
        _set_memory_context_length(model, base_url=self.base_url, api_key=self.api_key)

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
        stream_transport: StreamTransport | None = None,
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
            stream_transport=stream_transport,
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
        result, _ = self._complete_json_request(
            system_prompt=system_prompt,
            prompt_key=prompt_key,
            fallback_prompt=fallback_prompt,
            user_payload=user_payload,
            task=task,
            response_schema=response_schema,
        )
        return result

    def complete_json_with_audio(
        self,
        *,
        system_prompt: str | None = None,
        prompt_key: str | None = None,
        fallback_prompt: str | None = None,
        user_payload: dict[str, Any],
        audio_path: str | Path,
        task: str | None = None,
        response_schema: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Complete a JSON turn with native audio input and optional audio output.

        This uses the OpenAI-compatible ``input_audio`` message contract.  The
        caller explicitly opts into this method only for models known to support
        audio; text-only providers continue using ``complete_json``.
        """
        return self._complete_json_request(
            system_prompt=system_prompt,
            prompt_key=prompt_key,
            fallback_prompt=fallback_prompt,
            user_payload=user_payload,
            audio_path=audio_path,
            request_audio=True,
            task=task,
            response_schema=response_schema,
        )

    def _complete_json_request(
        self,
        *,
        system_prompt: str | None,
        prompt_key: str | None,
        fallback_prompt: str | None,
        user_payload: dict[str, Any],
        task: str | None,
        response_schema: str | None,
        audio_path: str | Path | None = None,
        request_audio: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        payload = self._build_json_request_payload(
            system_prompt=system_prompt,
            prompt_key=prompt_key,
            fallback_prompt=fallback_prompt,
            user_payload=user_payload,
            task=task,
            response_schema=response_schema,
            audio_path=audio_path,
            request_audio=request_audio,
        )
        response = self.transport(
            self.provider_capabilities.build_url(self.base_url),
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload,
        )
        content = _extract_message_content(response, self.provider_capabilities)
        audio = _extract_response_audio(response)
        return unwrap_protocol_response(_extract_json_object(content), task=task), audio

    def complete_json_stream(
        self,
        *,
        system_prompt: str | None = None,
        prompt_key: str | None = None,
        fallback_prompt: str | None = None,
        user_payload: dict[str, Any],
        task: str | None = None,
        response_schema: str | None = None,
        on_content: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        payload = self._build_json_request_payload(
            system_prompt=system_prompt,
            prompt_key=prompt_key,
            fallback_prompt=fallback_prompt,
            user_payload=user_payload,
            task=task,
            response_schema=response_schema,
        )
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        stream_payload.setdefault("stream_options", {"include_usage": True})
        content_parts: list[str] = []
        for chunk in self.stream_transport(
            self.provider_capabilities.build_url(self.base_url),
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            stream_payload,
        ):
            usage = chunk.get("usage") if isinstance(chunk, dict) else None
            if isinstance(usage, dict):
                _accumulate_memory_usage(usage)
            content_delta, reasoning_delta = _extract_stream_chunk_text(chunk)
            if content_delta:
                content_parts.append(content_delta)
                if on_content is not None:
                    on_content(content_delta)
            if reasoning_delta and on_reasoning is not None:
                on_reasoning(reasoning_delta)
        content = "".join(content_parts)
        return unwrap_protocol_response(_extract_json_object(content), task=task)

    def _build_json_request_payload(
        self,
        *,
        system_prompt: str | None,
        prompt_key: str | None,
        fallback_prompt: str | None,
        user_payload: dict[str, Any],
        task: str | None,
        response_schema: str | None,
        audio_path: str | Path | None = None,
        request_audio: bool = False,
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
        if audio_path is None:
            messages = self.provider_capabilities.build_messages(
                system_prompt=resolved_prompt,
                user_content=user_content,
            )
        else:
            encoded = base64.b64encode(Path(audio_path).read_bytes()).decode("ascii")
            suffix = Path(audio_path).suffix.lower().lstrip(".")
            audio_format = suffix if suffix in {"wav", "mp3", "flac", "ogg", "m4a"} else "wav"
            messages = self.provider_capabilities.build_messages(
                system_prompt=resolved_prompt,
                user_content=[
                    {"type": "text", "text": user_content},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": encoded, "format": audio_format},
                    },
                ],
            )
        # ── Pre-flight context-window safety check ──
        # Estimate prompt tokens to avoid sending a request that will
        # fail with a context-overflow error.  Uses a conservative
        # 2.0 chars/token so we stay well under the real limit even for
        # mixed Chinese/English/code content.
        _prompt_chars = sum(
            len(str(msg.get("content", "")))
            for msg in messages
        )
        _estimated_tokens = int(_prompt_chars / 2.0)
        _context_limit = get_memory_context_length()
        if _estimated_tokens > _context_limit:
            raise ValueError(
                f"API-B prompt exceeds model context window: "
                f"~{_estimated_tokens:,} estimated tokens > "
                f"{_context_limit:,} token limit "
                f"(model={self.model}, prompt_chars={_prompt_chars:,}). "
                f"Reduce the cognitive packet budget in the cognition charter "
                f"(prompt_attention_policy.max_chars)."
            )
        _usage_pct = _estimated_tokens / _context_limit if _context_limit else 0
        if _usage_pct >= 0.80:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning(
                "API-B prompt using ~%.0f%% of context window "
                "(%s / %s tokens, model=%s)",
                _usage_pct * 100,
                f"{_estimated_tokens:,}",
                f"{_context_limit:,}",
                self.model,
            )
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
        }
        if request_audio:
            payload["modalities"] = ["text", "audio"]
            payload["audio"] = {"voice": "alloy", "format": "wav"}
        self.provider_capabilities.apply_request_format(payload)
        return payload

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
