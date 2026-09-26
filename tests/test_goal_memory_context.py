from __future__ import annotations

from voidcube.application.goal_memory_context import (
    GoalMemoryContextBudget,
    build_goal_memory_context_block,
    resolve_goal_memory_refs,
)


class FakeMemoryFetcher:
    def __init__(self, records: dict[str, dict], failures: set[str] | None = None):
        self.records = records
        self.failures = failures or set()
        self.calls: list[tuple[str, str | None]] = []

    def get_compressed(self, memory_id: str, *, session_id: str | None = None) -> dict:
        self.calls.append((memory_id, session_id))
        if memory_id in self.failures:
            raise RuntimeError("unauthorized")
        return self.records[memory_id]


def test_resolver_is_bounded_authorized_and_redacts_internal_fields():
    fetcher = FakeMemoryFetcher(
        {
            "m-high": {
                "title": "Prior decision",
                "summary": "Use the service owner for retrieval.",
                "embedding": [0.1, 0.2],
                "owner_id": "private-owner",
                "timespan_end": "2026-09-26T08:00:00Z",
            },
            "m-low": {"title": "Low confidence", "summary": "Should not be fetched."},
        },
        failures={"m-fail"},
    )
    resolution = resolve_goal_memory_refs(
        [
            {"id": "r-low", "memory_id": "m-low", "confidence": 0.2},
            {"id": "r-high", "memory_id": "m-high", "confidence": 0.9, "relation_type": "decision"},
            {"id": "r-fail", "memory_id": "m-fail", "confidence": 0.8},
        ],
        fetcher,
        budget=GoalMemoryContextBudget(max_refs=2, max_context_chars=2000, max_item_chars=500),
        session_id="session-1",
    )

    assert fetcher.calls == [("m-high", "session-1"), ("m-fail", "session-1")]
    assert resolution["resolved_count"] == 1
    assert resolution["skipped_count"] == 2
    result = resolution["results"][0]
    assert result["relation_type"] == "decision"
    assert "memory_id" not in result
    assert "embedding" not in result
    assert "owner_id" not in result

    block = build_goal_memory_context_block(resolution, budget=GoalMemoryContextBudget(max_context_chars=500))
    assert block
    assert "m-high" not in block
    assert "embedding" not in block
    assert "private-owner" not in block
    assert len(block) <= 500


def test_resolver_deduplicates_references_and_empty_results_render_nothing():
    fetcher = FakeMemoryFetcher({"m1": {"summary": "One useful fact"}})
    resolution = resolve_goal_memory_refs(
        [
            {"id": "a", "memory_id": "m1", "confidence": 0.7},
            {"id": "b", "memory_id": "m1", "confidence": 0.6},
        ],
        fetcher,
    )
    assert fetcher.calls == [("m1", None)]
    assert resolution["resolved_count"] == 1
    assert resolution["skipped_count"] == 1
    assert build_goal_memory_context_block({"results": []}) == ""


def test_budget_rejects_invalid_limits():
    for kwargs in (
        {"max_refs": -1},
        {"max_context_chars": -1},
        {"max_item_chars": 0},
    ):
        try:
            GoalMemoryContextBudget(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid budget was accepted")
