"""Sync/async execution policy for resolved auxiliary call targets."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .auxiliary_orchestration import AuxiliaryCallTarget, AuxiliaryFallbackCall


def execute_sync(
    *,
    target: AuxiliaryCallTarget,
    task: str | None,
    kwargs: dict[str, Any],
    messages: list,
    temperature: float | None,
    max_tokens: int | None,
    tools: list | None,
    timeout: float,
    extra_body: dict | None,
    validate_response: Callable[[Any, str | None], Any],
    retry_kwargs: Callable[[dict[str, Any], Exception, int | None], dict[str, Any] | None],
    fallback_reason: Callable[[Exception, str], str | None],
    prepare_fallback: Callable[..., AuxiliaryFallbackCall | None],
) -> Any:
    try:
        return validate_response(target.client.chat.completions.create(**kwargs), task)
    except Exception as first_error:
        retry = retry_kwargs(kwargs, first_error, max_tokens)
        if retry is not None:
            try:
                return validate_response(target.client.chat.completions.create(**retry), task)
            except Exception as retry_error:
                first_error = retry_error
        reason = fallback_reason(first_error, target.requested_provider)
        if reason is not None:
            fallback = prepare_fallback(
                target=target,
                task=task,
                reason=reason,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                timeout=timeout,
                extra_body=extra_body,
            )
            if fallback is not None:
                return validate_response(fallback.client.chat.completions.create(**fallback.kwargs), task)
        raise first_error


async def execute_async(
    *,
    target: AuxiliaryCallTarget,
    task: str | None,
    kwargs: dict[str, Any],
    messages: list,
    temperature: float | None,
    max_tokens: int | None,
    tools: list | None,
    timeout: float,
    extra_body: dict | None,
    validate_response: Callable[[Any, str | None], Any],
    retry_kwargs: Callable[[dict[str, Any], Exception, int | None], dict[str, Any] | None],
    fallback_reason: Callable[[Exception, str], str | None],
    prepare_fallback: Callable[..., AuxiliaryFallbackCall | None],
    to_async_client: Callable[[Any, str], tuple[Any, str]],
) -> Any:
    try:
        return validate_response(await target.client.chat.completions.create(**kwargs), task)
    except Exception as first_error:
        retry = retry_kwargs(kwargs, first_error, max_tokens)
        if retry is not None:
            try:
                return validate_response(await target.client.chat.completions.create(**retry), task)
            except Exception as retry_error:
                first_error = retry_error
        reason = fallback_reason(first_error, target.requested_provider)
        if reason is not None:
            fallback = prepare_fallback(
                target=target,
                task=task,
                reason=reason,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                timeout=timeout,
                extra_body=extra_body,
            )
            if fallback is not None:
                async_client, async_model = to_async_client(fallback.client, fallback.model)
                fallback_kwargs = dict(fallback.kwargs)
                if async_model and async_model != fallback_kwargs.get("model"):
                    fallback_kwargs["model"] = async_model
                return validate_response(
                    await async_client.chat.completions.create(**fallback_kwargs), task
                )
        raise first_error


__all__ = ["execute_async", "execute_sync"]
