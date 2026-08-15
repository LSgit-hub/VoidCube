from __future__ import annotations

import pytest

from scripts.evaluate_longmemeval import _semantic_config_from_overrides, evaluate_longmemeval


pytestmark = [pytest.mark.unit]


def test_semantic_config_from_overrides_builds_external_provider():
    cfg = _semantic_config_from_overrides(
        "openai", "text-embedding-3-small", "https://api.example.com/v1", "EMB_KEY"
    )
    assert cfg is not None
    assert cfg.enabled is True
    assert cfg.provider == "openai"
    assert cfg.model == "text-embedding-3-small"
    assert cfg.base_url == "https://api.example.com/v1"

    # No overrides → default (local embedder) path, config left to the service.
    assert _semantic_config_from_overrides() is None


@pytest.mark.asyncio
async def test_longmemeval_baseline_metrics_hold():
    """Regression contract for the LongMemEval-schema Chinese subset."""
    report = await evaluate_longmemeval()

    assert report["case_count"] >= 10
    assert report["abstention_count"] >= 1
    assert report["metrics"]["recall_at_5"] >= 0.55
    assert report["metrics"]["mrr"] >= 0.5
    assert report["metrics"]["map_at_5"] >= 0.5
    assert report["metrics"]["abstention_false_positive_rate"] == 0.0
    assert report["metrics"]["abstention_empty_rate"] == 1.0
    # The temporal-aware recall feature (P0) must keep working.
    assert report["by_category"]["temporal-reasoning"]["recall_at_5"] >= 0.8
    # Semantic retrieval (P-语义) must keep the paraphrase/assistant categories
    # above the keyword-only baseline.
    assert report["by_category"]["single-session-assistant"]["recall_at_5"] >= 0.8
    assert report["by_category"]["single-session-preference"]["recall_at_5"] >= 0.8
    assert report["by_category"]["single-session-user"]["recall_at_5"] >= 0.8
    assert report["by_category"]["knowledge-update"]["recall_at_5"] >= 0.8
    # The answerable + abstention counts should be present in the report.
    assert set(report["by_category"]) >= {
        "single-session-user",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
    }
