from __future__ import annotations

import pytest

from scripts.evaluate_longmemeval import evaluate_longmemeval


pytestmark = [pytest.mark.unit]


@pytest.mark.asyncio
async def test_longmemeval_baseline_metrics_hold():
    """Baseline regression for the LongMemEval-schema Chinese subset.

    Thresholds are pinned to the current measured baseline (recall@5 = 0.61
    with the local CharNgramEmbedder semantic fallback; was 0.44 before
    semantic retrieval). Raising them tracks real improvement (e.g. a trained
    embedding model or update-aware ranking); a drop signals regression.
    """
    report = await evaluate_longmemeval()

    assert report["case_count"] >= 10
    assert report["abstention_count"] >= 1
    assert report["metrics"]["recall_at_5"] >= 0.55
    assert report["metrics"]["mrr"] >= 0.5
    assert report["metrics"]["map_at_5"] >= 0.5
    # The temporal-aware recall feature (P0) must keep working.
    assert report["by_category"]["temporal-reasoning"]["recall_at_5"] >= 0.8
    # Semantic retrieval (P-语义) must keep the paraphrase/assistant categories
    # above the keyword-only baseline.
    assert report["by_category"]["single-session-assistant"]["recall_at_5"] >= 0.8
    assert report["by_category"]["single-session-preference"]["recall_at_5"] >= 0.5
    # The answerable + abstention counts should be present in the report.
    assert set(report["by_category"]) >= {
        "single-session-user",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
    }
