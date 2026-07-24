from __future__ import annotations

import pytest

from scripts.evaluate_memory_recall import evaluate_recall_benchmark


pytestmark = [pytest.mark.unit]


@pytest.mark.asyncio
async def test_versioned_memory_recall_quality_benchmark_passes():
    result = await evaluate_recall_benchmark()

    assert result["passed"] is True
    assert result["metrics"]["recall_at_5"] >= 1.0
    assert result["metrics"]["mrr"] >= 0.8
    assert result["metrics"]["scope_leakage_rate"] == 0.0
