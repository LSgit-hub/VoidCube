from __future__ import annotations

from datetime import datetime, timezone

from voidcube.systems.perception import (
    FrameChangeDetector,
    LocalPerceptionLoop,
    PerceptionBuffer,
    PerceptionRecord,
    ScreenFrame,
    TimelineSegmenter,
)


class FakeSource:
    def __init__(self) -> None:
        self.frames = [b"a" * 128, b"a" * 128, b"b" * 128]
        self.index = 0

    def capture(self) -> ScreenFrame:
        pixels = self.frames[min(self.index, len(self.frames) - 1)]
        captured_at = datetime(2026, 1, 1, tzinfo=timezone.utc).replace(second=self.index)
        self.index += 1
        return ScreenFrame(pixels, 32, 1, 1, captured_at)


class FakeAnalyzer:
    def analyze(self, image_bytes: bytes, **kwargs: object) -> PerceptionRecord:
        from io import BytesIO
        from PIL import Image
        pixel = Image.open(BytesIO(image_bytes)).getpixel((0, 0))
        assert image_bytes.startswith(b"\x89PNG")
        return PerceptionRecord(
            record_id=str(kwargs["record_id"]),
            observed_at=kwargs["observed_at"],
            source=("screen", "fake-vl"),
            scene="editing_code" if pixel[0] == 97 else "watching_video",
            application="Fake App",
            summary="local observation",
            confidence=0.8,
            capture_session_id=str(kwargs["capture_session_id"]),
            sequence=int(kwargs["sequence"]),
        )


def test_local_perception_loop_skips_identical_frames_and_analyzes_changes() -> None:
    loop = LocalPerceptionLoop(
        FakeSource(),
        FakeAnalyzer(),
        change_detector=FrameChangeDetector(threshold=0.2),
        buffer=PerceptionBuffer(max_records=4),
        segmenter=TimelineSegmenter(segment_seconds=300, min_scene_seconds=0),
        capture_session_id="capture-1",
    )

    first = loop.step()
    second = loop.step()
    third = loop.step()

    assert first.changed is True
    assert first.record is not None
    assert second.changed is False
    assert second.record is None
    assert third.changed is True
    assert third.record is not None
    assert third.record.sequence == 3
    assert third.closed_segments[0].scene == "editing_code"
    assert third.scene.scene == "watching_video"


def test_local_perception_loop_flushes_and_clears_without_persisting_frames() -> None:
    loop = LocalPerceptionLoop(FakeSource(), FakeAnalyzer(), capture_session_id="capture-2")

    loop.step()
    tail = loop.flush()

    assert tail is not None
    assert tail.provisional is True
    loop.clear()
    assert loop.buffer.records() == ()
    assert loop.buffer.scene_state().status == "unavailable"
