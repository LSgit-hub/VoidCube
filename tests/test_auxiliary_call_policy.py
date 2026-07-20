from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import agent.auxiliary_client as auxiliary


pytestmark = pytest.mark.unit


def _response(text: str = "ok") -> SimpleNamespace:
    message = SimpleNamespace(content=text)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _SyncCompletions:
    def __init__(self, *outcomes: Any):
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _AsyncCompletions:
    def __init__(self, *outcomes: Any):
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(completions: Any, base_url: Any = "https://api.example/v1") -> SimpleNamespace:
    return SimpleNamespace(
        api_key="test-key",
        base_url=base_url,
        chat=SimpleNamespace(completions=completions),
    )


def _target(client: Any, *, requested: str = "auto", active: str = "deepseek"):
    return auxiliary.AuxiliaryCallTarget(
        requested_provider=requested,
        active_provider=active,
        model="test-model",
        base_url=str(client.base_url),
        client=client,
    )


def test_sync_call_retries_rejected_max_tokens_with_completion_limit(monkeypatch):
    completions = _SyncCompletions(
        RuntimeError("unsupported parameter: max_tokens"),
        _response(),
    )
    target = _target(_client(completions))
    monkeypatch.setattr(auxiliary, "_resolve_auxiliary_call_target", lambda **_kwargs: target)

    result = auxiliary.call_llm(
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=321,
    )

    assert result.choices[0].message.content == "ok"
    assert completions.calls[0]["max_tokens"] == 321
    assert "max_completion_tokens" not in completions.calls[0]
    assert completions.calls[1]["max_completion_tokens"] == 321
    assert "max_tokens" not in completions.calls[1]


@pytest.mark.asyncio
async def test_async_call_retries_rejected_max_tokens_with_completion_limit(monkeypatch):
    completions = _AsyncCompletions(
        RuntimeError("max_tokens is unsupported"),
        _response(),
    )
    target = _target(_client(completions))
    monkeypatch.setattr(auxiliary, "_resolve_auxiliary_call_target", lambda **_kwargs: target)

    await auxiliary.async_call_llm(
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=654,
    )

    assert completions.calls[0]["max_tokens"] == 654
    assert completions.calls[1]["max_completion_tokens"] == 654
    assert "max_tokens" not in completions.calls[1]


def test_missing_token_limit_does_not_create_a_none_retry(monkeypatch):
    error = RuntimeError("unsupported parameter: max_tokens")
    completions = _SyncCompletions(error)
    target = _target(_client(completions), requested="deepseek")
    monkeypatch.setattr(auxiliary, "_resolve_auxiliary_call_target", lambda **_kwargs: target)

    with pytest.raises(RuntimeError, match="max_tokens"):
        auxiliary.call_llm(messages=[{"role": "user", "content": "hello"}])

    assert len(completions.calls) == 1
    assert "max_tokens" not in completions.calls[0]
    assert "max_completion_tokens" not in completions.calls[0]


@pytest.mark.asyncio
async def test_async_resolution_receives_live_main_runtime(monkeypatch):
    runtime = {
        "provider": "custom",
        "model": "runtime-model",
        "base_url": "https://runtime.example/v1",
        "api_key": "runtime-key",
    }
    completions = _AsyncCompletions(_response())
    target = _target(_client(completions), active="custom")
    captured: dict[str, Any] = {}

    def fake_resolve(**kwargs: Any):
        captured.update(kwargs)
        return target

    monkeypatch.setattr(auxiliary, "_resolve_auxiliary_call_target", fake_resolve)

    await auxiliary.async_call_llm(
        task="web_extract",
        messages=[{"role": "user", "content": "hello"}],
        main_runtime=runtime,
    )

    assert captured["main_runtime"] == runtime
    assert captured["async_mode"] is True


def test_vision_target_uses_the_client_resolved_by_vision_router(monkeypatch):
    vision_client = _client(_SyncCompletions(_response()))
    monkeypatch.setattr(
        auxiliary,
        "_resolve_task_provider_model",
        lambda *_args, **_kwargs: ("auto", "vision-model", None, None),
    )
    monkeypatch.setattr(
        auxiliary,
        "resolve_vision_provider_client",
        lambda **_kwargs: ("openrouter", vision_client, "vision-model"),
    )
    monkeypatch.setattr(
        auxiliary,
        "_get_cached_client",
        lambda *_args, **_kwargs: pytest.fail("vision client must not be resolved twice"),
    )

    target = auxiliary._resolve_auxiliary_call_target(
        task="vision",
        provider=None,
        model=None,
        base_url=None,
        api_key=None,
        main_runtime=None,
        async_mode=False,
    )

    assert target.client is vision_client
    assert target.active_provider == "openrouter"


def test_explicit_provider_resolution_never_enters_auto(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        auxiliary,
        "_resolve_task_provider_model",
        lambda *_args, **_kwargs: ("openrouter", "test-model", None, None),
    )

    def fake_get_cached(provider: str, *_args: Any, **_kwargs: Any):
        calls.append(provider)
        return None, None

    monkeypatch.setattr(auxiliary, "_get_cached_client", fake_get_cached)

    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        auxiliary._resolve_auxiliary_call_target(
            task="compression",
            provider="openrouter",
            model=None,
            base_url=None,
            api_key=None,
            main_runtime=None,
            async_mode=False,
        )

    assert calls == ["openrouter"]


def test_explicit_provider_transport_error_never_falls_back(monkeypatch):
    error = _PaymentError("credits exhausted")
    completions = _SyncCompletions(error)
    target = _target(_client(completions), requested="openrouter", active="openrouter")
    monkeypatch.setattr(auxiliary, "_resolve_auxiliary_call_target", lambda **_kwargs: target)
    monkeypatch.setattr(
        auxiliary,
        "_try_provider_fallback",
        lambda *_args, **_kwargs: pytest.fail("explicit provider must not fall back"),
    )

    with pytest.raises(_PaymentError):
        auxiliary.call_llm(messages=[{"role": "user", "content": "hello"}])


class _PaymentError(RuntimeError):
    status_code = 402


@pytest.mark.asyncio
async def test_sync_and_async_auto_fallback_share_route_and_request(monkeypatch):
    sync_primary = _client(_SyncCompletions(_PaymentError("credits exhausted")))
    async_primary = _client(_AsyncCompletions(_PaymentError("credits exhausted")))
    sync_fallback_completions = _SyncCompletions(_response("sync fallback"))
    async_fallback_completions = _AsyncCompletions(_response("async fallback"))
    sync_fallback = _client(
        sync_fallback_completions,
        "https://inference-api.nousresearch.com/v1",
    )
    async_fallback = _client(
        async_fallback_completions,
        "https://inference-api.nousresearch.com/v1",
    )
    failed_providers: list[str] = []

    def fake_resolve(*, async_mode: bool, **_kwargs: Any):
        client = async_primary if async_mode else sync_primary
        return _target(client, requested="auto", active="deepseek")

    def fake_fallback(failed_provider: str, *_args: Any, **_kwargs: Any):
        failed_providers.append(failed_provider)
        return sync_fallback, "fallback-model", "nous"

    monkeypatch.setattr(auxiliary, "_resolve_auxiliary_call_target", fake_resolve)
    monkeypatch.setattr(auxiliary, "_try_provider_fallback", fake_fallback)
    monkeypatch.setattr(
        auxiliary,
        "_to_async_client",
        lambda _client, model: (async_fallback, model),
    )

    call_args = {
        "task": "compression",
        "messages": [{"role": "user", "content": "summarize"}],
        "temperature": 0.25,
        "max_tokens": 777,
        "tools": [{"type": "function", "function": {"name": "inspect"}}],
        "timeout": 12.0,
        "extra_body": {"metadata": {"purpose": "test"}},
    }
    auxiliary.call_llm(**call_args)
    await auxiliary.async_call_llm(**call_args)

    assert failed_providers == ["deepseek", "deepseek"]
    assert sync_fallback_completions.calls == async_fallback_completions.calls
    fallback_kwargs = sync_fallback_completions.calls[0]
    assert fallback_kwargs["model"] == "fallback-model"
    assert fallback_kwargs["max_tokens"] == 777
    assert fallback_kwargs["extra_body"] == {
        "metadata": {"purpose": "test"},
        "tags": ["product=VoidCube-agent"],
    }


def test_active_provider_is_inferred_from_url_objects(monkeypatch):
    monkeypatch.setattr(auxiliary, "_read_main_provider", lambda: "")
    client = _client(
        _SyncCompletions(_response()),
        httpx.URL("https://api.deepseek.com/v1"),
    )

    assert auxiliary._infer_active_provider("auto", client, None) == "deepseek"


def test_fallback_skips_the_failed_provider_chain_entry(monkeypatch):
    attempted: list[str] = []

    def candidate(label: str, result: Any = (None, None)):
        def try_candidate():
            attempted.append(label)
            return result

        return try_candidate

    fallback_client = _client(_SyncCompletions(_response()))
    monkeypatch.setattr(
        auxiliary,
        "_get_provider_chain",
        lambda: [
            ("openrouter", candidate("openrouter")),
            ("nous", candidate("nous", (fallback_client, "fallback-model"))),
            ("local/custom", candidate("local/custom")),
            ("api-key", candidate("api-key")),
        ],
    )

    client, model, provider = auxiliary._try_provider_fallback("deepseek")

    assert (client, model, provider) == (fallback_client, "fallback-model", "nous")
    assert attempted == ["openrouter", "nous"]


def test_target_rejects_an_empty_resolved_model(monkeypatch):
    client = _client(_SyncCompletions(_response()))
    monkeypatch.setattr(
        auxiliary,
        "_resolve_task_provider_model",
        lambda *_args, **_kwargs: ("auto", None, None, None),
    )
    monkeypatch.setattr(
        auxiliary,
        "_get_cached_client",
        lambda *_args, **_kwargs: (client, None),
    )
    monkeypatch.setattr(auxiliary, "_read_main_provider", lambda: "")

    with pytest.raises(RuntimeError, match="No LLM model resolved"):
        auxiliary._resolve_auxiliary_call_target(
            task="compression",
            provider=None,
            model=None,
            base_url=None,
            api_key=None,
            main_runtime=None,
            async_mode=False,
        )
