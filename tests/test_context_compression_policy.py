from voidcube.runtime.agent.context_policy import (
    ContextCompressionPolicy,
    configured_context_length,
)


def test_policy_keeps_default_budgets_and_exposes_source(monkeypatch):
    monkeypatch.setattr(
        "voidcube.runtime.agent.context_policy.resolve_model_context_length",
        lambda *args, **kwargs: (128_000, "endpoint_metadata"),
    )

    policy = ContextCompressionPolicy.for_model("demo")

    assert policy.context_length == 128_000
    assert policy.threshold_tokens == 64_000
    assert policy.tail_token_budget == 12_800
    assert policy.source == "endpoint_metadata"
    assert policy.adaptive_by_model is False
    assert policy.detection_known is True
    assert policy.hard_reference_limit == 64_000
    assert policy.soft_reference_limit == 32_000


def test_policy_with_context_length_preserves_tuning_and_updates_model():
    policy = ContextCompressionPolicy(
        model="large",
        context_length=256_000,
        threshold_percent=0.5,
        target_ratio=0.2,
        protect_last_n=10,
        source="config",
    )

    updated = policy.with_context_length(64_000, model="fallback", source="probe")

    assert updated.model == "fallback"
    assert updated.context_length == 64_000
    assert updated.threshold_tokens == 64_000
    assert updated.tail_token_budget == 12_800
    assert updated.protect_last_n == 10
    assert updated.source == "probe"


def test_policy_supports_one_million_token_provider_override():
    config = {
        "providers": {
            "api-a": {
                "base_url": "https://private.example/v1",
                "model_context_lengths": {"model-a": 1_000_000},
            }
        }
    }

    length = configured_context_length(
        config,
        provider="api-a",
        model="model-a",
        base_url="https://private.example/v1",
    )
    assert length == 1_000_000

    policy = ContextCompressionPolicy.for_model(
        "model-a",
        config_context_length=length,
    )
    assert policy.context_length == 1_000_000
    assert policy.threshold_tokens == 500_000
    assert policy.tail_token_budget == 100_000


def test_fallback_policy_reports_unknown_detection():
    policy = ContextCompressionPolicy(
        model="private-model",
        context_length=128_000,
        source="fallback_endpoint",
    )
    assert policy.detection_known is False
    assert policy.with_context_length(64_000, source="probe_tier").detection_known is False


def test_provider_wide_context_does_not_leak_across_model_switches():
    config = {
        "providers": {
            "api-a": {
                "base_url": "https://private.example/v1",
                "context_length": 1_000_000,
            }
        }
    }
    assert configured_context_length(
        config,
        provider="api-a",
        model="new-model",
        base_url="https://private.example/v1",
    ) is None


def test_provider_context_override_accepts_human_readable_value():
    config = {
        "providers": {
            "api-a": {
                "model_context_lengths": {"model-a": "1M"},
            }
        }
    }
    assert configured_context_length(
        config, provider="api-a", model="model-a"
    ) == 1_000_000


def test_provider_context_override_reads_model_registry_shapes():
    config = {
        "providers": {
            "api-a": {
                "models": {
                    "model-a": {"max_context_tokens": "1M"},
                },
            },
            "api-b": {
                "model_catalog": {
                    "models": [
                        {"id": "model-b", "input_token_limit": 1_048_576},
                    ],
                },
            },
            "api-c": {
                "model_capabilities": {
                    "model-c": {"context_window_size": "1,048,576"},
                },
            },
        }
    }

    assert configured_context_length(config, provider="api-a", model="model-a") == 1_000_000
    assert configured_context_length(config, provider="api-b", model="model-b") == 1_048_576
    assert configured_context_length(config, provider="api-c", model="model-c") == 1_048_576
