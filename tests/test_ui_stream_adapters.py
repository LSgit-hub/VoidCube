import pytest
from fastapi import HTTPException

from systems.supervisor.ui_stream_adapters import (
    normalize_media_enqueue_body,
    normalize_media_playlist_body,
    media_events,
    supervisor_state_events,
    voice_level_events,
)


class _Request:
    def __init__(self):
        self.calls = 0

    async def is_disconnected(self):
        self.calls += 1
        return self.calls > 1


def _text(chunk):
    return chunk.decode() if isinstance(chunk, bytes) else str(chunk)


@pytest.mark.asyncio
async def test_sse_adapters_emit_state_and_voice_first_frames():
    async def load_state():
        return {"status": "ok", "scene": "idle"}

    state_response = supervisor_state_events(
        _Request(), load_state=load_state, interval_seconds=0
    )
    state_chunk = await state_response.body_iterator.__anext__()
    assert "event: state" in _text(state_chunk)
    assert '"scene":"idle"' in _text(state_chunk)

    voice_response = voice_level_events(
        _Request(),
        realtime_status=lambda: {"level": 0.25},
        interval_seconds=0,
    )
    voice_chunk = await voice_response.body_iterator.__anext__()
    assert "event: level" in _text(voice_chunk)
    assert '"level":0.25' in _text(voice_chunk)


@pytest.mark.asyncio
async def test_media_sse_adapter_emits_revision_and_enqueue_body_is_normalized():
    media_response = media_events(
        _Request(),
        current_media=lambda: {
            "url": "https://example.com/a.mp3",
            "title": "Audio",
            "_revision": 3,
        },
        queue_items=lambda: [{"media_id": "next-1", "title": "Next"}],
        interval_seconds=0,
    )
    chunk = await media_response.body_iterator.__anext__()
    payload = _text(chunk)
    assert "event: play" in payload
    assert '"revision":3' in payload
    assert '"auto_play":true' in payload
    assert '"queue":[{"media_id":"next-1","title":"Next"}]' in payload

    assert normalize_media_enqueue_body(
        {"url": "  https://example.com/a.mp3  ", "type": "audio"}
    ) == {
        "url": "https://example.com/a.mp3",
        "title": "https://example.com/a.mp3",
        "type": "audio",
        "auto_play": True,
    }
    with pytest.raises(HTTPException):
        normalize_media_enqueue_body({"title": "missing"})
    with pytest.raises(HTTPException):
        normalize_media_enqueue_body({"url": "file:///tmp/song.mp3"})
    playlist = normalize_media_playlist_body({"queue_mode": "enqueue", "items": [{"url": "https://example.com/a.mp3", "type": "audio"}]})
    assert playlist["queue_mode"] == "enqueue"
    assert playlist["items"][0]["queue_mode"] == "enqueue"
