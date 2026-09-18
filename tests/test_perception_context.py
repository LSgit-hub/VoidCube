from datetime import datetime, timezone

import pytest

from voidcube.systems.perception import (
    SceneState,
    TimelineSegment,
    build_perception_context,
)


def test_perception_context_levels_are_bounded_and_text_only() -> None:
    segment = TimelineSegment(
        segment_id="s1",
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        summary="编辑代码",
        unknowns=("是否保存",),
        source_record_ids=("raw-record",),
    )
    scene = SceneState(scene="editing_code", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status="fresh")

    l0 = build_perception_context(level="L0", user_query="我现在在做什么？", scene=scene, segments=[segment])
    l2 = build_perception_context(level="L2", user_query="总结", scene=scene, segments=[segment])

    assert l0["timeline_segments"] == []
    assert l2["timeline_segments"][0]["summary"] == "编辑代码"
    assert "source_record_ids" not in l2["timeline_segments"][0]
    assert l2["privacy"]["raw_frames_included"] is False
    assert l2["privacy"]["control_tools_allowed"] is False
    assert "是否保存" in l2["uncertainties"]


def test_perception_context_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="L0"):
        build_perception_context(level="L3", user_query="看图")
