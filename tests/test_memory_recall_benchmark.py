from __future__ import annotations

import pytest

from scripts.evaluate_memory_recall import evaluate_recall_benchmark, load_benchmark


pytestmark = [pytest.mark.unit]


@pytest.mark.asyncio
async def test_versioned_memory_recall_quality_benchmark_passes():
    result = await evaluate_recall_benchmark()

    assert result["passed"] is True
    assert result["metrics"]["recall_at_5"] >= 1.0
    assert result["metrics"]["mrr"] >= 0.8
    assert result["metrics"]["scope_leakage_rate"] == 0.0


def test_benchmark_covers_anonymized_runtime_query_shapes():
    benchmark = load_benchmark()
    provenance = benchmark["query_shape_provenance"]
    shaped_cases = [case for case in benchmark["cases"] if case.get("shape")]

    assert provenance["source"] == "anonymized_runtime_recall_traces"
    assert provenance["raw_query_text_retained"] is False
    assert provenance["identifiers_retained"] is False
    assert len(shaped_cases) >= 5
    assert {case["shape"]["language"] for case in shaped_cases} >= {"cjk", "mixed"}
    assert {case["shape"]["length_bucket"] for case in shaped_cases} >= {
        "short_1_20",
        "long_61_plus",
    }
    assert {case["shape"]["intent"] for case in shaped_cases} >= {
        "recent_conversation",
        "specific_memory",
        "identity",
    }
    assert any(
        "workspace-private-release" in case["forbidden_ids"]
        for case in shaped_cases
    )
