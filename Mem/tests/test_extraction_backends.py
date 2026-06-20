from __future__ import annotations

from datetime import datetime, timezone

from memai import (
    ChroniclePipeline,
    EventExtractor,
    LLMEventExtractionBackend,
    LLMScholarBackend,
    OpenAICompatibleLLMClient,
    TranscriptTurn,
)


class FakeLLMClient:
    def extract_events(self, turns):
        return [
            {
                "title": "LLM generated decision",
                "summary": "The model identifies a durable decision about the memory system.",
                "event_kind": "decision",
                "impact_scope": "arc",
                "topics": ["memory-system", "timeline-indexing"],
                "entities": ["user", "project"],
                "source_turns": [turns[0].turn_id],
                "time_hint": "today",
                "importance": 0.82,
                "confidence": 0.9,
                "main_or_side": "main",
                "novelty": 0.88,
            }
        ]


def test_llm_backend_can_generate_events() -> None:
    turn = TranscriptTurn(
        turn_id="turn_001",
        speaker="user",
        text="今天我们决定把这个项目做成时间优先的记忆系统。",
        timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
    )
    extractor = EventExtractor(backend=LLMEventExtractionBackend(FakeLLMClient()))

    events = extractor.extract([turn])

    assert len(events) == 1
    assert events[0].event_kind.value == "decision"
    assert "memory-system" in events[0].topics


def test_llm_backend_normalizes_malformed_event_payloads() -> None:
    class NoisyLLMClient:
        def extract_events(self, turns):
            return [
                {
                    "summary": 12345,
                    "event_kind": "not-a-kind",
                    "impact_scope": "bad-scope",
                    "topics": "retrieval",
                    "entities": "user",
                    "source_turns": turns[0].turn_id,
                    "importance": "0.95",
                    "confidence": "oops",
                    "main_or_side": "bad-side",
                    "novelty": "0.4",
                },
                "ignore-me",
            ]

    turn = TranscriptTurn(
        turn_id="turn_001",
        speaker="user",
        text="Today retrieval is blocked and needs revision.",
        timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
    )
    extractor = EventExtractor(backend=LLMEventExtractionBackend(NoisyLLMClient()))

    events = extractor.extract([turn])

    assert len(events) == 1
    assert events[0].summary == "12345"
    assert events[0].event_kind.value == "progress"
    assert events[0].impact_scope.value == "thread"
    assert events[0].topics == ["retrieval"]
    assert events[0].entities == ["user"]
    assert events[0].main_or_side.value == "undetermined"
    assert events[0].confidence == 0.75


def test_llm_scholar_backend_can_drive_scene_and_arc_text() -> None:
    def fake_transport(url, headers, payload):
        system_prompt = payload["messages"][0]["content"]
        if "scene summarizer" in system_prompt:
            content = '{"title": "Scene: scholar", "summary": "Scholar summary", "scene_goal": "Scholar goal", "open_questions": ["Scholar question"]}'
        elif "arc analyst" in system_prompt:
            content = '{"title": "Mainline: scholar", "summary": "Scholar arc summary", "arc_goal": "Scholar arc goal", "drivers": ["Driver"], "obstacles": ["Obstacle"], "classification_reason": ["Scholar reason"], "main_or_side": "main", "status": "active", "arc_state": "active"}'
        else:
            content = '{"events": [{"title": "LLM generated decision", "summary": "Scholar event summary", "event_kind": "decision", "impact_scope": "arc", "topics": ["memory-system"], "entities": ["user"], "source_turns": ["turn_001"], "time_hint": "today", "importance": 0.82, "confidence": 0.9, "main_or_side": "main", "novelty": 0.88}]}'
        return {"choices": [{"message": {"content": content}}]}

    client = OpenAICompatibleLLMClient(
        model="test-model", api_key="test-key", transport=fake_transport
    )
    pipeline = ChroniclePipeline(
        event_extractor=EventExtractor(backend=LLMEventExtractionBackend(client)),
        scholar_backend=LLMScholarBackend(client),
    )
    turn = TranscriptTurn(
        turn_id="turn_001",
        speaker="user",
        text="今天我们决定把这个项目做成时间优先的记忆系统。",
        timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
    )

    result = pipeline.ingest([turn])

    assert result.scenes[0].summary == "Scholar summary"
    assert result.arcs[0].summary == "Scholar arc summary"


def test_llm_scholar_backend_falls_back_on_invalid_payload_fields() -> None:
    def fake_transport(url, headers, payload):
        user_payload = payload["messages"][1]["content"]
        if '"events":' in user_payload and '"heuristic":' in user_payload:
            content = '{"title": 12, "summary": ["bad"], "scene_goal": null, "open_questions": "Need evidence"}'
        elif '"classification_score":' in user_payload:
            content = '{"title": "Arc title", "summary": "Arc summary", "main_or_side": "invalid", "status": "bad", "arc_state": "also-bad", "drivers": "Driver"}'
        else:
            content = '{"title": 3, "summary": null, "importance": "bad", "confidence": 0.95}'
        return {"choices": [{"message": {"content": content}}]}

    client = OpenAICompatibleLLMClient(
        model="test-model", api_key="test-key", transport=fake_transport
    )
    pipeline = ChroniclePipeline(
        event_extractor=EventExtractor(backend=LLMEventExtractionBackend(FakeLLMClient())),
        scholar_backend=LLMScholarBackend(client),
    )
    turn = TranscriptTurn(
        turn_id="turn_001",
        speaker="user",
        text="Today we decided to build the memory system.",
        timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
    )

    result = pipeline.ingest([turn])
    assert result.scenes[0].open_questions == ["Need evidence"]
    assert result.arcs[0].title == "Arc title"
    assert result.arcs[0].status.value == "active"

    execution = result.revise_memory(
        target_id=result.events[0].id,
        revision_type="factual_revision",
        reason="Need better wording",
        changes={},
    )

    replacements = [event for event in execution.events if event.supersedes]
    assert replacements[0].confidence == 0.95
