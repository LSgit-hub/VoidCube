from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import MemoryService
from systems.memory.database import open_memory_sqlite


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


def _turn(turn_id: str, speaker: str, text: str):
    return SimpleNamespace(
        turn_id=turn_id,
        speaker=speaker,
        text=text,
        timestamp=datetime.now(timezone.utc),
    )


def test_cache_round_trip_and_clear(tmp_path):
    from systems.memory.llm_cache import (
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
    from systems.memory.llm_extraction import CachedLLMExtractionAdapter

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

    # A different batch is a cache miss.
    other = adapter.extract_events([_turn("t2", "user", "完全不同的另一段对话。")])
    assert other == events
    assert fake.call_count == 2


def test_llm_first_pipeline_falls_back_to_heuristic_without_client(
    tmp_path, monkeypatch
):
    from memai import model_config
    from systems.memory.llm_extraction import build_llm_first_pipeline

    monkeypatch.setattr(model_config, "resolve_mem_llm_client", lambda role="default": (None, ""))

    pipeline = build_llm_first_pipeline(tmp_path / "memory.db", role="extraction")

    backend = getattr(getattr(pipeline, "event_extractor", None), "backend", None)
    assert backend is not None
    assert backend.name == "heuristic"


@pytest.mark.asyncio
async def test_escalate_summary_caches_llm_result(tmp_path, monkeypatch):
    from systems.memory.llm_cache import build_cache_key, open_cached

    service = _service(tmp_path)
    fake = FakeLLM({"title": "升级标题", "summary": "升级后的抽象摘要。"})
    monkeypatch.setattr(service, "_resolve_mem_llm_client", lambda role="default": (fake, "fake-model"))

    first = await service._llm_escalate_summary(
        mem_id="mem-1",
        title="旧标题",
        summary="旧摘要",
        from_type="event",
        from_level=0,
        to_type="scene",
        to_level=1,
        topics=["压缩"],
    )
    second = await service._llm_escalate_summary(
        mem_id="mem-1",
        title="旧标题",
        summary="旧摘要",
        from_type="event",
        from_level=0,
        to_type="scene",
        to_level=1,
        topics=["压缩"],
    )

    assert first == ("升级标题", "升级后的抽象摘要。")
    assert second == first
    assert fake.call_count == 1  # second escalation served from cache

    key = build_cache_key(
        "escalate", "fake-model", "mem-1|0|1|旧标题|旧摘要|压缩"
    )
    cached = open_cached(service._db_path, key)
    assert cached == {"title": "升级标题", "summary": "升级后的抽象摘要。"}


@pytest.mark.asyncio
async def test_purge_review_caches_llm_result(tmp_path, monkeypatch):
    service = _service(tmp_path)
    fake = FakeLLM({"keep": True, "reason": "重大架构决策，应保留。"})
    monkeypatch.setattr(service, "_resolve_mem_llm_client", lambda role="default": (fake, "fake-model"))

    first = await service._llm_purge_review(
        mem_id="mem-2", title="架构决策", summary="曾经的关键架构转折。", topics=["架构"]
    )
    second = await service._llm_purge_review(
        mem_id="mem-2", title="架构决策", summary="曾经的关键架构转折。", topics=["架构"]
    )

    assert first is True
    assert second is True
    assert fake.call_count == 1  # second review served from cache
