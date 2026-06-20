from types import SimpleNamespace

import pytest

from plugins.memory.mem import MemMemoryProvider


@pytest.mark.unit
def test_mem_query_arcs_filters_stalled_arc_state(monkeypatch):
    provider = MemMemoryProvider()
    provider._memory_state = SimpleNamespace(
        result=SimpleNamespace(
            events=[],
            scenes=[],
            arcs=[],
            epochs=[],
            profile_memories=[],
        )
    )

    captured = {}

    class FakeMemoryQueryEngine:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs

        def active_arcs(self, statuses=None, max_results=10):
            captured["statuses"] = statuses
            captured["max_results"] = max_results
            return {
                "result_type": "active_arcs",
                "arcs": [
                    {"id": "arc-stalled", "arc_state": "stalled", "status": "active"},
                    {"id": "arc-active", "arc_state": "active", "status": "active"},
                ],
            }

    monkeypatch.setattr("memai.query.MemoryQueryEngine", FakeMemoryQueryEngine)

    payload = provider._query_arcs(status="stalled", max_results=5)

    assert payload["success"] is True
    assert payload["data"]["arcs"] == [
        {"id": "arc-stalled", "arc_state": "stalled", "status": "active"}
    ]
    assert [item.value for item in captured["statuses"]] == ["active", "dormant", "closed"]
    assert captured["max_results"] == 5
