"""Provider-neutral LLM transport validation and retry policy."""

from __future__ import annotations

from typing import Any


def completion_token_retry_kwargs(
    kwargs: dict[str, Any],
    error: Exception,
    max_tokens: int | None,
) -> dict[str, Any] | None:
    """Translate a provider rejection of ``max_tokens`` once."""
    if max_tokens is None or "max_tokens" not in kwargs:
        return None
    error_text = " ".join(
        str(value or "")
        for value in (error, getattr(error, "body", None), getattr(error, "param", None))
    ).lower()
    if "max_tokens" not in error_text and "max tokens" not in error_text:
        return None
    retry_kwargs = dict(kwargs)
    retry_kwargs.pop("max_tokens", None)
    retry_kwargs["max_completion_tokens"] = max_tokens
    return retry_kwargs


def validate_llm_response(response: Any, task: str | None = None) -> Any:
    """Fail fast when an adapter returns a non-chat-completion response."""
    if response is None:
        raise RuntimeError(f"Auxiliary {task or 'call'}: LLM returned None response")
    try:
        choices = response.choices
        if not choices or not hasattr(choices[0], "message"):
            raise AttributeError("missing choices[0].message")
    except (AttributeError, TypeError, IndexError) as exc:
        response_type = type(response).__name__
        response_preview = str(response)[:120]
        raise RuntimeError(
            f"Auxiliary {task or 'call'}: LLM returned invalid response "
            f"(type={response_type}): {response_preview!r}. Expected object "
            "with .choices[0].message — check provider adapter or custom endpoint compatibility."
        ) from exc
    return response


__all__ = ["completion_token_retry_kwargs", "validate_llm_response"]
