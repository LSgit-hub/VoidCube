from __future__ import annotations

from datetime import datetime, timezone

from memai import AnswerAssembler, ArcState, ChroniclePipeline, Status, TranscriptTurn


def _build_result():
    turns = [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="2026-03-01 we decided to build the memory system project.",
            timestamp=datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_002",
            speaker="assistant",
            text="2026-03-10 we implemented the retrieval pipeline.",
            timestamp=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_003",
            speaker="assistant",
            text="The project must preserve evidence traces.",
            timestamp=datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc),
        ),
    ]
    return ChroniclePipeline().ingest(turns)


def test_answer_assembler_builds_timeline_first_sections() -> None:
    planner = _build_result().create_query_planner()
    execution = planner.plan_and_execute(
        "What changed in the project this month?",
        reference_time=datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc),
    )

    answer = AnswerAssembler().assemble(execution).to_dict()

    assert answer["strategy"] == "timeline_first"
    assert answer["summary"]
    assert answer["observed"]
    assert answer["blockers"] == []
    assert "stable_context" in answer


def test_answer_assembler_builds_stable_context_sections() -> None:
    planner = _build_result().create_query_planner()
    execution = planner.plan_and_execute(
        "What stable constraints should I remember before answering?",
        reference_time=datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc),
    )

    answer = AnswerAssembler().assemble(execution).to_dict()

    assert answer["strategy"] == "stable_context_first"
    assert answer["summary"]
    assert answer["stable_context"]
    assert answer["blockers"] == []
    assert any("constraint" in item for item in answer["structure"])


def test_answer_assembler_separates_blockers_in_state_first() -> None:
    result = _build_result()
    result.arcs[0].status = Status.DORMANT
    result.arcs[0].arc_state = ArcState.STALLED
    result.arcs[
        0
    ].summary = "The retrieval line is stalled by unresolved ranking blockers."
    planner = result.create_query_planner()

    execution = planner.plan_and_execute(
        "What unresolved blockers are still active?",
        reference_time=datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc),
    )

    answer = AnswerAssembler().assemble(execution).to_dict()

    assert answer["strategy"] == "state_first"
    assert answer["observed"]
    assert answer["blockers"] == [] or all(
        item != answer["observed"][0] for item in answer["blockers"]
    )
    assert answer["structure"] == []
    assert (
        "block" in answer["summary"].lower() or "stalled" in answer["summary"].lower()
    )


def test_answer_assembler_uses_chinese_summary_for_chinese_request() -> None:
    planner = _build_result().create_query_planner()
    execution = planner.plan_and_execute(
        "这个月项目有什么变化？",
        reference_time=datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc),
    )

    answer = AnswerAssembler().assemble(execution).to_dict()

    assert any("\u4e00" <= char <= "\u9fff" for char in answer["summary"])


def test_answer_assembler_uses_stable_context_language_preference() -> None:
    turns = [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="Please use Chinese responses.",
            timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_002",
            speaker="assistant",
            text="The project must preserve evidence traces.",
            timestamp=datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    planner = ChroniclePipeline().ingest(turns).create_query_planner()
    execution = planner.plan_and_execute(
        "What stable constraints should I remember before answering?",
        reference_time=datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc),
    )

    answer = AnswerAssembler().assemble(execution).to_dict()

    assert any("\u4e00" <= char <= "\u9fff" for char in answer["summary"])


def test_answer_assembler_localizes_theme_structure_entries() -> None:
    planner = _build_result().create_query_planner()
    execution = planner.plan_and_execute(
        "请总结 retrieval evolved so far",
        reference_time=datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc),
    )

    answer = AnswerAssembler().assemble(execution).to_dict()

    assert answer["strategy"] == "theme_first"
    assert any(item.startswith("当前状态：") or item.startswith("关键转折点：") for item in answer["structure"])


def test_answer_assembler_localizes_uncertainty_flags() -> None:
    planner = _build_result().create_query_planner()
    execution = planner.plan_and_execute(
        "最近发生了什么以及还有什么阻塞？",
        reference_time=datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc),
    )

    answer = AnswerAssembler().assemble(execution).to_dict()

    assert any("意图" in item or "时间范围" in item for item in answer["unknown"])
