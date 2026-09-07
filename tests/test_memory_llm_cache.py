from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from memai.application.config import MemoryServiceConfig
from memai.application.memory_service import MemoryService
from memai.repository.sqlite import open_memory_sqlite


pytestmark = [pytest.mark.unit]


def _service(tmp_path) -> MemoryService:
    return MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            recall_default_limit=5,
            recall_candidate_limit=100,
        )
    )


class FakeLLM:
    """Record-calling OpenAICompatibleLLMClient stand-in."""

    def __init__(self, response):
        self._response = response
        self.call_count = 0
        self.last_task = None

    def complete_json(self, *, system_prompt, user_payload, task):
        self.call_count += 1
        self.last_task = task
        return self._response

    def extract_events(self, turns):
        self.call_count += 1
        self.last_task = "extractor.events"
        return self._response


def _turn(turn_id: str, speaker: str, text: str):
    return SimpleNamespace(
        turn_id=turn_id,
        speaker=speaker,
        text=text,
        timestamp=datetime.now(timezone.utc),
    )


def test_cache_round_trip_and_clear(tmp_path):
    from memai.repository.llm_cache import (
        build_cache_key,
        clear_cache,
        get_cached,
        open_cached,
        put_cached,
        setup_llm_cache,
        store_cached,
    )

    db_path = tmp_path / "cache.db"
    conn = open_memory_sqlite(db_path)
    setup_llm_cache(conn)
    conn.commit()

    key = build_cache_key("test-task", "fake-model", "some input")
    assert get_cached(conn, key) is None

    put_cached(
        conn,
        cache_key=key,
        task="test-task",
        model="fake-model",
        input_text="some input",
        result={"title": "缓存结果"},
    )
    conn.commit()
    assert get_cached(conn, key) == {"title": "缓存结果"}

    # Short-lived helper path reads the same row.
    assert open_cached(db_path, key) == {"title": "缓存结果"}

    store_cached(
        db_path,
        cache_key=build_cache_key("other", "", "x"),
        task="other",
        model="",
        input_text="x",
        result=[1, 2, 3],
    )
    assert clear_cache(conn, task="other") == 1
    assert clear_cache(conn) >= 1
    conn.close()


def test_cached_extraction_adapter_calls_llm_once_per_batch(tmp_path):
    from memai.application.llm_extraction import CachedLLMExtractionAdapter

    events = [
        {
            "title": "测试事件",
            "summary": "从对话中提取的事件。",
            "event_kind": "decision",
            "importance": 0.8,
            "confidence": 0.9,
            "topics": ["测试"],
            "entities": ["user"],
            "source_turns": ["t1"],
        }
    ]
    fake = FakeLLM(events)
    adapter = CachedLLMExtractionAdapter(fake, tmp_path / "memory.db", model="fake-model")
    turns = [_turn("t1", "user", "我们决定改造记忆系统。")]

    first = adapter.extract_events(turns)
    second = adapter.extract_events(turns)

    assert first == events
    assert second == events
    assert fake.call_count == 1  # second call served from cache
    assert fake.last_task == "extractor.events"

    # A different batch is a cache miss.
    other = adapter.extract_events([_turn("t2", "user", "完全不同的另一段对话。")])
    assert other == []  # fake response links only to t1 and is rejected
    assert fake.call_count == 2


def test_cached_extraction_adapter_does_not_cache_empty_result(tmp_path):
    from memai.application.llm_extraction import CachedLLMExtractionAdapter

    fake = FakeLLM([])
    adapter = CachedLLMExtractionAdapter(
        fake,
        tmp_path / "memory.db",
        model="fake-model",
    )
    turns = [_turn("t1", "user", "我们决定改造记忆系统。")]

    assert adapter.extract_events(turns) == []
    assert adapter.extract_events(turns) == []
    assert fake.call_count == 2


def test_cached_extraction_adapter_does_not_cache_unlinked_events(tmp_path):
    from memai.application.llm_extraction import CachedLLMExtractionAdapter

    fake = FakeLLM([{"title": "无效事件", "source_turns": ["missing"]}])
    adapter = CachedLLMExtractionAdapter(
        fake,
        tmp_path / "memory.db",
        model="fake-model",
    )
    turns = [_turn("t1", "user", "我们决定改造记忆系统。")]

    assert adapter.extract_events(turns) == []
    assert adapter.extract_events(turns) == []
    assert fake.call_count == 2


def test_llm_first_pipeline_falls_back_to_heuristic_without_client(
    tmp_path, monkeypatch
):
    from memai import model_config
    from memai.application.llm_extraction import build_llm_first_pipeline

    monkeypatch.setattr(model_config, "resolve_mem_llm_client", lambda role="default": (None, ""))

    pipeline = build_llm_first_pipeline(tmp_path / "memory.db", role="extraction")

    backend = getattr(getattr(pipeline, "event_extractor", None), "backend", None)
    assert backend is not None
    assert backend.name == "heuristic"
