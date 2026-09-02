from voidcube.runtime.agent.context_policy import ContextCompressionPolicy


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
