from __future__ import annotations

from types import SimpleNamespace

from src.voidcube.infrastructure.providers.auxiliary_client_cache import AuxiliaryClientCache
from src.voidcube.infrastructure.providers.auxiliary_vision_clients import resolve_vision_client


def test_auxiliary_cache_reuses_client_and_preserves_runtime_key():
    cache = AuxiliaryClientCache()
    calls = []
    client = SimpleNamespace(base_url="https://example.test/v1")

    def resolve(provider, model, async_mode, **kwargs):
        calls.append((provider, model, async_mode, kwargs["main_runtime"]))
        return client, "provider-default"

    first = cache.get_or_create(
        "auto",
        model="requested",
        main_runtime={"provider": "openrouter"},
        resolve_client=resolve,
        normalize_runtime=lambda runtime: dict(runtime or {}),
        runtime_fields=("provider",),
    )
    second = cache.get_or_create(
        "auto",
        model="requested",
        main_runtime={"provider": "openrouter"},
        resolve_client=resolve,
        normalize_runtime=lambda runtime: dict(runtime or {}),
        runtime_fields=("provider",),
    )

    assert first == (client, "requested")
    assert second == first
    assert len(calls) == 1


def test_vision_policy_uses_active_provider_before_fallback():
    active = SimpleNamespace(name="active")
    fallback = SimpleNamespace(name="fallback")
    strict_calls = []

    def strict(provider):
        strict_calls.append(provider)
        return (active, "active-model") if provider == "nous" else (fallback, "fallback-model")

    provider, client, model = resolve_vision_client(
        requested="auto",
        resolved_model=None,
        resolved_base_url=None,
        resolved_api_key=None,
        async_mode=False,
        main_provider="nous",
        main_model="main-model",
        provider_order=("openrouter", "nous"),
        strict_backend=strict,
        resolve_provider_client=lambda *args, **kwargs: (None, None),
        get_cached_client=lambda *args, **kwargs: (None, None),
        to_async_client=lambda client, model: (client, model),
        log=SimpleNamespace(info=lambda *args, **kwargs: None, debug=lambda *args, **kwargs: None),
    )

    assert (provider, client, model) == ("nous", active, "active-model")
    assert strict_calls == ["nous"]
