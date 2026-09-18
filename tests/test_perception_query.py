from datetime import datetime, timezone

from voidcube.systems.perception import (
    LocalPerceptionLoop,
    PerceptionQueryService,
    PerceptionRecord,
    PerceptionRuntime,
    ScreenFrame,
    TimelineSegment,
    TimelineStore,
)


class _Source:
    def capture(self) -> ScreenFrame:
        return ScreenFrame(b"a" * 128, 32, 1, 1, datetime(2026, 1, 1, tzinfo=timezone.utc))


class _Analyzer:
    def analyze(self, _image: bytes, **kwargs: object) -> PerceptionRecord:
        return PerceptionRecord(
            record_id=str(kwargs["record_id"]), observed_at=kwargs["observed_at"],
            scene="editing_code", summary="编辑代码", confidence=0.8,
        )


def test_query_service_returns_current_scene_and_text_context(tmp_path) -> None:
    loop = LocalPerceptionLoop(_Source(), _Analyzer())
    runtime = PerceptionRuntime(loop)
    runtime.authorize(); runtime.start(); runtime.step()
    with TimelineStore(tmp_path / "query.sqlite3") as store:
        segment = TimelineSegment(
            segment_id="s1", start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
            summary="编辑代码", scene="editing_code",
        )
        store.put(segment)
        query = PerceptionQueryService(runtime, store=store)
        scene = query.current_scene()
        context = query.context(
            level="L2", user_query="总结", start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

    assert scene["scene"] == "editing_code"
    assert context["timeline_segments"][0]["summary"] == "编辑代码"
    assert context["privacy"]["control_tools_allowed"] is False
